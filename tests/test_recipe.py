"""Recipe 数据模型 + RecipeManager 持久化测试 (UT-01~05)"""

import json
import tempfile
from pathlib import Path

import pytest

from src.core.recipe import Recipe, RecipeManager, _params_equal


# ======================================================================
# UT-01: Recipe 数据模型
# ======================================================================
class TestRecipeCreation:
    """Recipe 创建与序列化"""

    def test_create_with_defaults(self):
        recipe = Recipe(id="", name="测试配方", engine="indextts")
        assert recipe.id != ""
        assert len(recipe.id) == 8  # UUID[:8]
        assert recipe.created_at
        assert recipe.updated_at

    def test_create_with_explicit_id(self):
        recipe = Recipe(id="abc12345", name="显式ID", engine="indextts")
        assert recipe.id == "abc12345"

    def test_to_dict_and_from_dict(self):
        recipe = Recipe(
            id="rcp_001", name="标准配方", engine="indextts",
            engine_params={"reference_audio": "/tmp/ref.wav", "speed": 1.0},
        )
        data = recipe.to_dict()
        restored = Recipe.from_dict(data)
        assert restored.id == recipe.id
        assert restored.name == recipe.name
        assert restored.engine == recipe.engine
        assert restored.engine_params == recipe.engine_params

    def test_from_dict_backward_compat(self):
        """旧数据缺少 engine_params 等字段"""
        data = {"id": "rcp_old", "name": "旧配方", "engine": "indextts"}
        recipe = Recipe.from_dict(data)
        assert recipe.engine_params == {}
        # created_at 为空时会自填时间戳（PostInit 行为）
        assert recipe.created_at != ""

    def test_touch_updates_timestamp(self):
        recipe = Recipe(id="rcp_t", name="ts测试", engine="indextts")
        old_ts = recipe.updated_at
        recipe.touch()
        assert recipe.updated_at != old_ts

    def test_duplicate_creates_new_id(self):
        recipe = Recipe(
            id="rcp_orig", name="原始配方", engine="indextts",
            engine_params={"speed": 1.5},
        )
        dup = recipe.duplicate()
        assert dup.id != recipe.id
        assert dup.name == "原始配方 (副本)"
        assert dup.engine == recipe.engine
        assert dup.engine_params == recipe.engine_params
        # 副本的 params 应该是深拷贝
        dup.engine_params["speed"] = 2.0
        assert recipe.engine_params["speed"] == 1.5


# ======================================================================
# UT-02: _params_equal 参数比较
# ======================================================================
class TestParamsEqual:
    """参数等价性比较"""

    def test_identical(self):
        assert _params_equal({"a": 1}, {"a": 1}) is True

    def test_different_values(self):
        assert _params_equal({"a": 1}, {"a": 2}) is False

    def test_different_keys(self):
        assert _params_equal({"a": 1}, {"a": 1, "b": 2}) is False

    def test_float_tolerance(self):
        assert _params_equal({"f": 1.0}, {"f": 1.0000001}) is True
        assert _params_equal({"f": 1.0}, {"f": 1.001}) is False

    def test_ignore_text_key(self):
        """默认忽略 text 字段"""
        assert _params_equal({"speed": 1.0, "text": "你好"}, {"speed": 1.0, "text": "世界"}) is True

    def test_list_comparison(self):
        assert _params_equal({"l": [1, 2, 3]}, {"l": [1, 2, 3]}) is True
        assert _params_equal({"l": [1, 2]}, {"l": [1, 2, 3]}) is False

    def test_list_float_tolerance(self):
        assert _params_equal({"l": [1.0, 2.0]}, {"l": [1.0000001, 2.0]}) is True


# ======================================================================
# UT-03: RecipeManager CRUD
# ======================================================================
class TestRecipeManagerCRUD:
    """配方管理器增删改查"""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield RecipeManager(Path(tmp))

    def test_empty_on_create(self, manager):
        assert manager.list_all() == []
        assert manager.count() == 0

    def test_add_and_get(self, manager):
        recipe = Recipe(id="rcp1", name="配方1", engine="indextts")
        manager.add(recipe)
        assert manager.count() == 1
        got = manager.get("rcp1")
        assert got is not None
        assert got.name == "配方1"

    def test_add_duplicate_raises(self, manager):
        recipe = Recipe(id="rcp1", name="配方1", engine="indextts")
        manager.add(recipe)
        with pytest.raises(ValueError):
            manager.add(recipe)

    def test_update(self, manager):
        recipe = Recipe(id="rcp1", name="配方1", engine="indextts")
        manager.add(recipe)
        recipe.name = "配方1-改"
        manager.update(recipe)
        assert manager.get("rcp1").name == "配方1-改"

    def test_update_nonexistent_raises(self, manager):
        recipe = Recipe(id="ghost", name="不存在", engine="indextts")
        with pytest.raises(ValueError):
            manager.update(recipe)

    def test_delete(self, manager):
        recipe = Recipe(id="rcp1", name="配方1", engine="indextts")
        manager.add(recipe)
        manager.delete("rcp1")
        assert manager.count() == 0
        assert manager.get("rcp1") is None

    def test_delete_nonexistent_silent(self, manager):
        manager.delete("ghost")  # 无异常

    def test_delete_batch(self, manager):
        for i in range(3):
            manager.add(Recipe(id=f"rcp{i}", name=f"配方{i}", engine="indextts"))
        manager.delete_batch(["rcp0", "rcp2"])
        assert manager.count() == 1
        assert manager.get("rcp1") is not None

    def test_duplicate(self, manager):
        manager.add(Recipe(id="rcp1", name="配方1", engine="indextts", engine_params={"speed": 1.0}))
        dup = manager.duplicate("rcp1")
        assert dup is not None
        assert dup.id != "rcp1"
        assert dup.name == "配方1 (副本)"
        assert manager.count() == 2

    def test_duplicate_nonexistent(self, manager):
        assert manager.duplicate("ghost") is None

    def test_list_by_engine(self, manager):
        manager.add(Recipe(id="r1", name="IDXT1", engine="indextts"))
        manager.add(Recipe(id="r2", name="IDXT2", engine="indextts"))
        manager.add(Recipe(id="r3", name="GSV1", engine="indextts"))
        assert len(manager.list_by_engine("indextts")) == 3
        assert len(manager.list_by_engine("gpt_sovits")) == 0  # GPT-SoVITS 已注销

    def test_find_by_name(self, manager):
        manager.add(Recipe(id="r1", name="我的配方", engine="indextts"))
        found = manager.find_by_name("我的配方")
        assert found is not None
        assert found.id == "r1"
        assert manager.find_by_name("不存在") is None

    def test_upsert_new(self, manager):
        recipe = Recipe(id="r_new", name="新配方", engine="indextts")
        manager.upsert(recipe)
        assert manager.count() == 1

    def test_upsert_existing_by_id(self, manager):
        recipe = Recipe(id="r1", name="原配方", engine="indextts")
        manager.add(recipe)
        recipe.name = "已更新"
        manager.upsert(recipe)
        assert manager.get("r1").name == "已更新"

    def test_upsert_name_conflict_overwrites(self, manager):
        """同名冲突：覆盖旧 ID"""
        r1 = Recipe(id="r1", name="同名", engine="indextts")
        r2 = Recipe(id="r2", name="同名", engine="indextts")  # 同引擎同名冲突
        manager.add(r1)
        manager.upsert(r2)
        assert manager.count() == 1
        assert manager.get("r1").engine == "indextts"

    def test_matches(self, manager):
        r = Recipe(id="r1", name="参考配方", engine="indextts", engine_params={"speed": 1.0})
        manager.add(r)
        assert manager.matches(r, {"speed": 1.0}) is True
        assert manager.matches(r, {"speed": 1.5}) is False


# ======================================================================
# UT-04: RecipeManager 持久化
# ======================================================================
class TestRecipeManagerPersistence:
    """持久化到 recipes.json"""

    @pytest.fixture
    def tmpdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    def test_save_and_load_preserves_data(self, tmpdir):
        mgr1 = RecipeManager(tmpdir)
        mgr1.add(Recipe(id="r1", name="配方1", engine="indextts", engine_params={"s": 1.0}))
        mgr1.add(Recipe(id="r2", name="配方2", engine="indextts"))

        # 新建 Manager 从相同目录加载
        mgr2 = RecipeManager(tmpdir)
        assert mgr2.count() == 2
        r = mgr2.get("r1")
        assert r.name == "配方1"
        assert r.engine_params == {"s": 1.0}

    def test_load_empty_file_is_safe(self, tmpdir):
        """RecipeManager 首次创建空目录"""
        mgr = RecipeManager(tmpdir / "empty")
        assert mgr.list_all() == []
        assert mgr.count() == 0

    def test_load_corrupted_json_is_safe(self, tmpdir):
        """损坏的 JSON 文件应不崩溃"""
        fpath = tmpdir / "recipes.json"
        fpath.write_text("{invalid json}", encoding="utf-8")
        mgr = RecipeManager(tmpdir)
        assert mgr.list_all() == []


# ======================================================================
# UT-05: RecipeManager 导出/导入
# ======================================================================
class TestRecipeManagerExportImport:
    """导出和导入功能"""

    @pytest.fixture
    def manager(self):
        with tempfile.TemporaryDirectory() as tmp:
            mgr = RecipeManager(Path(tmp))
            mgr.add(Recipe(id="r1", name="配方A", engine="indextts", engine_params={"speed": 1.0}))
            mgr.add(Recipe(id="r2", name="配方B", engine="indextts"))
            yield mgr

    def test_export_to_file(self, manager):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            export_path = Path(f.name)
        try:
            count = manager.export_to_file(export_path)
            assert count == 2
            raw = json.loads(export_path.read_text(encoding="utf-8"))
            assert len(raw) == 2
        finally:
            export_path.unlink(missing_ok=True)

    def test_import_merge_skips_same_name(self, manager):
        """merge 模式：同名跳过"""
        data = [
            {"id": "new1", "name": "配方A", "engine": "indextts", "engine_params": {"speed": 5.0}},
            {"id": "new2", "name": "配方C", "engine": "indextts"},
        ]
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            json.dump(data, f)
            import_path = Path(f.name)
        try:
            imported = manager.import_from_file(import_path, mode="merge")
            assert imported == 1  # 配方A 同名跳过，配方C 新加
            assert manager.count() == 3
            # 配方A 不应被覆盖
            assert manager.get("r1").engine_params["speed"] == 1.0
        finally:
            import_path.unlink(missing_ok=True)

    def test_import_replace_overwrites_same_name(self, manager):
        """replace 模式：同名覆盖"""
        data = [
            {"id": "new1", "name": "配方X", "engine": "indextts"},
        ]
        manager.add(Recipe(id="old", name="配方X", engine="indextts"))

        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            json.dump(data, f)
            import_path = Path(f.name)
        try:
            imported = manager.import_from_file(import_path, mode="replace")
            assert imported == 1
            # 配方X 的 engine 应被覆盖
            assert manager.find_by_name("配方X").engine == "indextts"
        finally:
            import_path.unlink(missing_ok=True)

    def test_import_force_replaces_all(self, manager):
        """force 模式：全量替换"""
        data = [
            {"id": "f1", "name": "全新配方", "engine": "indextts"},
        ]
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", encoding="utf-8", delete=False) as f:
            json.dump(data, f)
            import_path = Path(f.name)
        try:
            imported = manager.import_from_file(import_path, mode="force")
            assert imported == 1
            assert manager.count() == 1
            assert manager.get("f1") is not None
        finally:
            import_path.unlink(missing_ok=True)
