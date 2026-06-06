"""Recipe 数据模型 — 引擎配置快照（配方）"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
        self._load()

    @property
    def filepath(self) -> Path:
        return self._storage_dir / self._FILENAME

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def _load(self) -> None:
        if self.filepath.exists():
            try:
                raw = json.loads(self.filepath.read_text(encoding="utf-8"))
                recipes_list: list[dict] = raw if isinstance(raw, list) else []
                self._recipes = {}
                for item in recipes_list:
                    recipe = Recipe.from_dict(item)
                    self._recipes[recipe.id] = recipe
            except (json.JSONDecodeError, KeyError, TypeError):
                self._recipes = {}
        else:
            self._recipes = {}

    def _save(self) -> None:
        data = [r.to_dict() for r in self._recipes.values()]
        tmp = self.filepath.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.filepath)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def list_all(self) -> list[Recipe]:
        return list(self._recipes.values())

    def list_by_engine(self, engine: str) -> list[Recipe]:
        return [r for r in self._recipes.values() if r.engine == engine]

    def get(self, recipe_id: str) -> Recipe | None:
        return self._recipes.get(recipe_id)

    def find_by_name(self, name: str) -> Recipe | None:
        for r in self._recipes.values():
            if r.name == name:
                return r
        return None

    def add(self, recipe: Recipe) -> None:
        if recipe.id in self._recipes:
            raise ValueError(f"配方 ID 已存在: {recipe.id}")
        self._recipes[recipe.id] = recipe
        self._save()

    def update(self, recipe: Recipe) -> None:
        if recipe.id not in self._recipes:
            raise ValueError(f"配方 ID 不存在: {recipe.id}")
        recipe.touch()
        self._recipes[recipe.id] = recipe
        self._save()

    def delete(self, recipe_id: str) -> None:
        if recipe_id in self._recipes:
            del self._recipes[recipe_id]
            self._save()

    def delete_batch(self, ids: list[str]) -> None:
        for rid in ids:
            self._recipes.pop(rid, None)
        if ids:
            self._save()

    def duplicate(self, recipe_id: str) -> Recipe | None:
        original = self._recipes.get(recipe_id)
        if original is None:
            return None
        new_recipe = original.duplicate()
        self.add(new_recipe)
        return new_recipe

    def upsert(self, recipe: Recipe) -> None:
        """插入或更新（按名称查重）"""
        existing = self.find_by_name(recipe.name)
        if existing and existing.id != recipe.id:
            # 同名冲突：覆盖
            self._recipes[existing.id] = recipe
            recipe.touch()
        elif recipe.id in self._recipes:
            self._recipes[recipe.id] = recipe
            recipe.touch()
        else:
            self._recipes[recipe.id] = recipe
        self._save()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def matches(self, recipe: Recipe, params: dict[str, Any]) -> bool:
        """检查 recipe 的 engine_params 是否与给定参数匹配"""
        if recipe.engine_params == params:
            return True
        return _params_equal(recipe.engine_params, params)

    def count(self) -> int:
        return len(self._recipes)

    # ------------------------------------------------------------------
    # 导出 / 导入
    # ------------------------------------------------------------------
    def export_to_file(self, path: Path) -> int:
        """导出全部配方到 JSON 文件，返回导出数量"""
        data = [r.to_dict() for r in self._recipes.values()]
        Path(path).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return len(data)

    def import_from_file(self, path: Path, mode: str = "merge") -> int:
        """从 JSON 文件导入配方

        Args:
            path: JSON 文件路径
            mode: "merge" 追加（跳过同名）| "replace" 按名称覆盖 | "force" 全量替换

        Returns:
            导入的数量
        """
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        items: list[dict] = raw if isinstance(raw, list) else []

        if mode == "force":
            self._recipes.clear()
            for item in items:
                recipe = Recipe.from_dict(item)
                self._recipes[recipe.id] = recipe
            self._save()
            return len(items)

        imported = 0
        for item in items:
            recipe = Recipe.from_dict(item)
            existing = self.find_by_name(recipe.name)
            if existing:
                if mode == "replace":
                    existing.engine = recipe.engine
                    existing.engine_params = recipe.engine_params
                    existing.touch()
                    imported += 1
                # merge 模式下跳过同名
            else:
                self._recipes[recipe.id] = recipe
                imported += 1

        if imported:
            self._save()
        return imported
