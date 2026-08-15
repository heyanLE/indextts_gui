"""Recipe 数据模型 — 引擎配置快照（配方）"""

from __future__ import annotations

import shutil
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from ._persistence import atomic_write_json


@dataclass
class Recipe:
    """引擎配置快照（配方） — 保存一组完整的引擎参数以便复用"""

    id: str  # UUID
    name: str  # 配方名称
    engine: str  # 引擎标识: "indextts"
    engine_params: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("配方名称不能为空")
        if not isinstance(self.engine, str) or not self.engine:
            raise ValueError("配方引擎不能为空")
        if not isinstance(self.engine_params, dict):
            raise TypeError("Recipe.engine_params 必须是字典")
        now = datetime.now(timezone.utc).isoformat()
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def touch(self) -> None:
        """更新时间戳"""
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Recipe:
        data = dict(data)
        # 向后兼容：旧数据可能缺少某些字段
        data.setdefault("engine_params", {})
        data.setdefault("created_at", "")
        data.setdefault("updated_at", "")
        return cls(**data)

    def duplicate(self) -> Recipe:
        """深拷贝当前配方，生成新 ID 和名称"""
        new_recipe = Recipe(
            id=str(uuid.uuid4())[:8],
            name=f"{self.name} (副本)",
            engine=self.engine,
            engine_params=deepcopy(self.engine_params),
        )
        return new_recipe


def _params_equal(a: dict[str, Any], b: dict[str, Any], ignore_keys: set | None = None) -> bool:
    """比较两组参数字典是否等价（忽略 text 字段，浮点数容忍 1e-6）"""
    if ignore_keys is None:
        ignore_keys = {"text"}

    keys_a = set(k for k in a if k not in ignore_keys)
    keys_b = set(k for k in b if k not in ignore_keys)
    if keys_a != keys_b:
        return False

    for k in keys_a:
        va, vb = a[k], b[k]
        if isinstance(va, float) and isinstance(vb, float):
            if abs(va - vb) > 1e-6:
                return False
        elif isinstance(va, list) and isinstance(vb, list):
            if len(va) != len(vb):
                return False
            for x, y in zip(va, vb):
                if isinstance(x, float) and isinstance(y, float):
                    if abs(x - y) > 1e-6:
                        return False
                elif x != y:
                    return False
        elif va != vb:
            return False
    return True


class RecipeManager:
    """配方持久化管理器

    存储路径: {ConfigManager.storage_dir}/recipes.json
    """

    _FILENAME = "recipes.json"

    def __init__(self, storage_dir: Path) -> None:
        self._storage_dir = Path(storage_dir)
        self._storage_dir.mkdir(parents=True, exist_ok=True)
        self._recipes: dict[str, Recipe] = {}
        self._lock = RLock()
        self._load_error: str | None = None
        self._load()

    @property
    def filepath(self) -> Path:
        return self._storage_dir / self._FILENAME

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        import json

        if self.filepath.exists():
            try:
                raw = json.loads(self.filepath.read_text(encoding="utf-8"))
                if not isinstance(raw, list):
                    raise ValueError("配方文件根节点必须是数组")
                recipes_list: list[dict] = raw
                loaded: dict[str, Recipe] = {}
                loaded_names: set[str] = set()
                for item in recipes_list:
                    recipe = Recipe.from_dict(item)
                    if recipe.id in loaded:
                        raise ValueError(f"重复配方 ID: {recipe.id}")
                    if recipe.name in loaded_names:
                        raise ValueError(f"重复配方名称: {recipe.name}")
                    loaded[recipe.id] = recipe
                    loaded_names.add(recipe.name)
                self._recipes = loaded
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                self._recipes = {}
                self._load_error = str(exc)
        else:
            self._recipes = {}

    def _save(self, recipes: dict[str, Recipe] | None = None) -> None:
        source = self._recipes if recipes is None else recipes
        atomic_write_json(self.filepath, [recipe.to_dict() for recipe in source.values()])

    def _commit(self, recipes: dict[str, Recipe]) -> None:
        """Persist a complete candidate snapshot before publishing it in memory."""
        if self._load_error is not None and self.filepath.exists():
            # Never silently overwrite the only copy of unreadable user data.
            backup = self.filepath.with_name(
                f"{self.filepath.stem}.corrupt-{uuid.uuid4().hex[:8]}{self.filepath.suffix}"
            )
            shutil.copy2(self.filepath, backup)
        self._save(recipes)
        self._recipes = recipes
        self._load_error = None

    @property
    def load_error(self) -> str | None:
        """Parsing error from the original file, if recovery was required."""
        return self._load_error

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_all(self) -> list[Recipe]:
        with self._lock:
            return deepcopy(list(self._recipes.values()))

    def list_by_engine(self, engine: str) -> list[Recipe]:
        with self._lock:
            return deepcopy([r for r in self._recipes.values() if r.engine == engine])

    def get(self, recipe_id: str) -> Recipe | None:
        with self._lock:
            recipe = self._recipes.get(recipe_id)
            return deepcopy(recipe) if recipe is not None else None

    def find_by_name(self, name: str) -> Recipe | None:
        with self._lock:
            for recipe in self._recipes.values():
                if recipe.name == name:
                    return deepcopy(recipe)
            return None

    def add(self, recipe: Recipe) -> None:
        with self._lock:
            if recipe.id in self._recipes:
                raise ValueError(f"配方 ID 已存在: {recipe.id}")
            if any(existing.name == recipe.name for existing in self._recipes.values()):
                raise ValueError(f"配方名称已存在: {recipe.name}")
            candidate = deepcopy(self._recipes)
            candidate[recipe.id] = deepcopy(recipe)
            self._commit(candidate)

    def update(self, recipe: Recipe) -> None:
        with self._lock:
            if recipe.id not in self._recipes:
                raise ValueError(f"配方 ID 不存在: {recipe.id}")
            if any(
                existing.id != recipe.id and existing.name == recipe.name
                for existing in self._recipes.values()
            ):
                raise ValueError(f"配方名称已存在: {recipe.name}")
            updated = deepcopy(recipe)
            updated.touch()
            candidate = deepcopy(self._recipes)
            candidate[updated.id] = updated
            self._commit(candidate)

    def delete(self, recipe_id: str) -> None:
        with self._lock:
            if recipe_id in self._recipes:
                candidate = deepcopy(self._recipes)
                del candidate[recipe_id]
                self._commit(candidate)

    def delete_batch(self, ids: list[str]) -> None:
        with self._lock:
            candidate = deepcopy(self._recipes)
            changed = False
            for recipe_id in ids:
                if recipe_id in candidate:
                    del candidate[recipe_id]
                    changed = True
            if changed:
                self._commit(candidate)

    def duplicate(self, recipe_id: str) -> Recipe | None:
        with self._lock:
            original = self._recipes.get(recipe_id)
            if original is None:
                return None
            new_recipe = original.duplicate()
            while new_recipe.id in self._recipes:
                new_recipe.id = str(uuid.uuid4())[:8]
            candidate = deepcopy(self._recipes)
            candidate[new_recipe.id] = deepcopy(new_recipe)
            self._commit(candidate)
            return deepcopy(new_recipe)

    def upsert(self, recipe: Recipe) -> None:
        """插入或更新（按名称查重）"""
        with self._lock:
            candidate = deepcopy(self._recipes)
            existing = next(
                (item for item in candidate.values() if item.name == recipe.name),
                None,
            )
            incoming = deepcopy(recipe)
            if existing and existing.id != incoming.id:
                # Preserve the existing identity when replacing by name.  This
                # keeps dictionary keys, serialized IDs and UI references aligned.
                existing.engine = incoming.engine
                existing.engine_params = deepcopy(incoming.engine_params)
                existing.touch()
            elif incoming.id in candidate:
                incoming.touch()
                candidate[incoming.id] = incoming
            else:
                candidate[incoming.id] = incoming
            self._commit(candidate)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def matches(self, recipe: Recipe, params: dict[str, Any]) -> bool:
        """检查 recipe 的 engine_params 是否与给定参数匹配"""
        if recipe.engine_params == params:
            return True
        return _params_equal(recipe.engine_params, params)

    def count(self) -> int:
        with self._lock:
            return len(self._recipes)

    # ------------------------------------------------------------------
    # 导出 / 导入
    # ------------------------------------------------------------------
    def export_to_file(self, path: Path) -> int:
        """导出全部配方到 JSON 文件，返回导出数量"""
        with self._lock:
            data = [recipe.to_dict() for recipe in self._recipes.values()]
            atomic_write_json(Path(path), data)
            return len(data)

    def import_from_file(self, path: Path, mode: str = "merge") -> int:
        """从 JSON 文件导入配方

        Args:
            path: JSON 文件路径
            mode: "merge" 追加（跳过同名）| "replace" 按名称覆盖 | "force" 全量替换

        Returns:
            导入的数量
        """
        import json

        if mode not in {"merge", "replace", "force"}:
            raise ValueError(f"无效导入模式: {mode}")
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("配方导入文件必须是 JSON 数组")
        imported_recipes = [Recipe.from_dict(item) for item in raw]
        imported_ids = [recipe.id for recipe in imported_recipes]
        if len(imported_ids) != len(set(imported_ids)):
            raise ValueError("配方导入文件包含重复 ID")

        with self._lock:
            if mode == "force":
                names = [recipe.name for recipe in imported_recipes]
                if len(names) != len(set(names)):
                    raise ValueError("配方导入文件包含重复名称")
                candidate = {recipe.id: deepcopy(recipe) for recipe in imported_recipes}
                self._commit(candidate)
                return len(candidate)

            candidate = deepcopy(self._recipes)
            imported = 0
            for recipe in imported_recipes:
                existing = next(
                    (item for item in candidate.values() if item.name == recipe.name),
                    None,
                )
                if existing:
                    if mode == "replace":
                        existing.engine = recipe.engine
                        existing.engine_params = deepcopy(recipe.engine_params)
                        existing.touch()
                        imported += 1
                    continue

                incoming = deepcopy(recipe)
                while incoming.id in candidate:
                    incoming.id = str(uuid.uuid4())[:8]
                candidate[incoming.id] = incoming
                imported += 1

            if imported:
                self._commit(candidate)
            return imported
