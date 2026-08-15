"""配方管理 Tab — 表格展示所有配方，支持 CRUD、复制、批量删除、搜索筛选"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QMessageBox, QLabel,
    QComboBox, QFileDialog,
)

from src.core.recipe import Recipe, RecipeManager
from src.ui.recipe_edit_dialog import RecipeEditDialog


class RecipeTab(QWidget):
    """配方管理 Tab 页"""

    recipe_added = Signal(Recipe)
    recipe_updated = Signal(Recipe)
    recipe_deleted = Signal(str)  # recipe_id
    recipes_changed = Signal()    # bulk operations such as import

    def __init__(self, recipe_manager: RecipeManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = recipe_manager
        self._building = False  # 批量构建时抑制信号
        self._current_filter_engine: str | None = None

        self._build_ui()
        self._refresh_table()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        # --- 工具栏 ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        # 搜索
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索配方...")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setMinimumWidth(180)
        self._search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_edit)

        # 引擎筛选
        from src.engines import engine_registry
        self._filter_combo = QComboBox()
        self._filter_combo.addItem("全部引擎", None)
        for eng in engine_registry.list_engines():
            self._filter_combo.addItem(eng.meta.engine_name, eng.meta.engine_id)
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)

        toolbar.addStretch()

        # 操作按钮
        self._add_btn = QPushButton("新建配方")
        self._add_btn.setObjectName("primaryBtn")
        self._add_btn.clicked.connect(self._on_add)
        toolbar.addWidget(self._add_btn)

        self._copy_btn = QPushButton("复制")
        self._copy_btn.clicked.connect(self._on_copy)
        self._copy_btn.setEnabled(False)
        toolbar.addWidget(self._copy_btn)

        self._delete_btn = QPushButton("删除")
        self._delete_btn.clicked.connect(self._on_delete)
        self._delete_btn.setEnabled(False)
        toolbar.addWidget(self._delete_btn)

        self._delete_batch_btn = QPushButton("批量删除")
        self._delete_batch_btn.clicked.connect(self._on_delete_batch)
        self._delete_batch_btn.setEnabled(False)
        toolbar.addWidget(self._delete_batch_btn)

        toolbar.addSpacing(8)

        self._export_btn = QPushButton("导出")
        self._export_btn.clicked.connect(self._on_export)
        toolbar.addWidget(self._export_btn)

        self._import_btn = QPushButton("导入")
        self._import_btn.clicked.connect(self._on_import)
        toolbar.addWidget(self._import_btn)

        layout.addLayout(toolbar)

        # --- 表格 ---
        self._table = QTableWidget()
        self._table.setObjectName("recipeTable")
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["", "名称", "引擎", "创建时间", "更新时间"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 36)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        self._table.doubleClicked.connect(self._on_edit)

        layout.addWidget(self._table, 1)

        # --- 状态栏 ---
        status_layout = QHBoxLayout()
        self._status_label = QLabel("共 0 个配方")
        status_layout.addWidget(self._status_label)
        layout.addLayout(status_layout)

    # ------------------------------------------------------------------
    # 表格刷新
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        self._refresh_table()

    def _refresh_table(self) -> None:
        self._building = True

        search_text = self._search_edit.text().strip().lower()
        engine_filter = self._current_filter_engine

        # 筛选
        recipes = self._manager.list_all()
        if search_text:
            recipes = [
                r for r in recipes
                if search_text in r.name.lower()
            ]
        if engine_filter:
            recipes = [r for r in recipes if r.engine == engine_filter]

        self._table.setRowCount(len(recipes))

        for row, recipe in enumerate(recipes):
            # checkbox
            cb_item = QTableWidgetItem()
            cb_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            cb_item.setCheckState(Qt.CheckState.Unchecked)
            self._table.setItem(row, 0, cb_item)

            # 名称
            name_item = QTableWidgetItem(recipe.name)
            name_item.setData(Qt.ItemDataRole.UserRole, recipe.id)
            self._table.setItem(row, 1, name_item)

            # 引擎
            self._table.setItem(row, 2, QTableWidgetItem(recipe.engine))

            # 创建时间
            created = recipe.created_at[:10] if recipe.created_at else "—"
            self._table.setItem(row, 3, QTableWidgetItem(created))

            # 更新时间
            updated = recipe.updated_at[:10] if recipe.updated_at else "—"
            self._table.setItem(row, 4, QTableWidgetItem(updated))

        self._status_label.setText(f"共 {len(recipes)} 个配方")
        self._building = False
        self._on_selection_changed()

    # ------------------------------------------------------------------
    # 搜索 & 筛选
    # ------------------------------------------------------------------
    def _on_search_changed(self) -> None:
        self._refresh_table()

    def _on_filter_changed(self) -> None:
        self._current_filter_engine = self._filter_combo.currentData()
        self._refresh_table()

    # ------------------------------------------------------------------
    # 选择变化
    # ------------------------------------------------------------------
    def _on_selection_changed(self) -> None:
        if self._building:
            return
        sel = self._table.selectedItems()
        self._copy_btn.setEnabled(len(sel) > 0)
        self._delete_btn.setEnabled(len(sel) > 0)
        self._delete_batch_btn.setEnabled(len(sel) >= 1)

    def _selected_recipe_ids(self) -> list[str]:
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        ids = []
        for row in rows:
            name_item = self._table.item(row, 1)
            if name_item:
                rid = name_item.data(Qt.ItemDataRole.UserRole)
                if rid:
                    ids.append(rid)
        return ids

    def _checked_recipe_ids(self) -> list[str]:
        ids = []
        for row in range(self._table.rowCount()):
            cb_item = self._table.item(row, 0)
            if cb_item and cb_item.checkState() == Qt.CheckState.Checked:
                name_item = self._table.item(row, 1)
                if name_item:
                    rid = name_item.data(Qt.ItemDataRole.UserRole)
                    if rid:
                        ids.append(rid)
        return ids

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------
    def _on_add(self) -> None:
        dlg = RecipeEditDialog(
            self,
            recipe=None,
            existing_names=[r.name for r in self._manager.list_all()],
        )
        dlg.recipe_saved.connect(self._on_recipe_saved_from_add)
        dlg.exec()

    def _on_recipe_saved_from_add(self, recipe: Recipe) -> None:
        existing = self._manager.find_by_name(recipe.name)
        if existing and existing.id != recipe.id:
            # 覆盖
            self._manager.delete(existing.id)
        self._manager.add(recipe)
        self.recipe_added.emit(recipe)
        self._refresh_table()

    def _on_edit(self, index) -> None:
        row = index.row()
        name_item = self._table.item(row, 1)
        if not name_item:
            return
        rid = name_item.data(Qt.ItemDataRole.UserRole)
        recipe = self._manager.get(rid)
        if not recipe:
            return

        existing = [r.name for r in self._manager.list_all() if r.id != rid]
        dlg = RecipeEditDialog(self, recipe=recipe, existing_names=existing)
        dlg.recipe_saved.connect(self._on_recipe_saved_from_edit)
        dlg.exec()

    def _on_recipe_saved_from_edit(self, recipe: Recipe) -> None:
        self._manager.update(recipe)
        self.recipe_updated.emit(recipe)
        self._refresh_table()

    def _on_copy(self) -> None:
        ids = self._selected_recipe_ids()
        if not ids:
            return
        for rid in ids:
            new_recipe = self._manager.duplicate(rid)
            if new_recipe:
                self.recipe_added.emit(new_recipe)
        self._refresh_table()

    def _on_delete(self) -> None:
        ids = self._selected_recipe_ids()
        if not ids:
            return
        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除选中的 {len(ids)} 个配方吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for rid in ids:
                self._manager.delete(rid)
                self.recipe_deleted.emit(rid)
            self._refresh_table()

    def _on_delete_batch(self) -> None:
        ids = self._checked_recipe_ids()
        if not ids:
            QMessageBox.information(self, "提示", "请先勾选要删除的配方")
            return
        reply = QMessageBox.question(
            self, "确认批量删除",
            f"确定要删除选中的 {len(ids)} 个配方吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._manager.delete_batch(ids)
            for rid in ids:
                self.recipe_deleted.emit(rid)
            self._refresh_table()

    # ------------------------------------------------------------------
    # 导出 / 导入
    # ------------------------------------------------------------------
    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配方", "recipes.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            count = self._manager.export_to_file(path)
            QMessageBox.information(self, "导出成功", f"已导出 {count} 个配方到:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def _on_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "导入配方", "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return

        from PySide6.QtWidgets import QInputDialog
        modes = ["merge", "replace", "force"]
        labels = ["追加（跳过同名）", "按名称覆盖", "全量替换"]
        selected_label, ok = QInputDialog.getItem(
            self, "导入模式", "请选择导入模式:", labels, 0, False,
        )
        if not ok:
            return

        try:
            mode = modes[labels.index(selected_label)]
            count = self._manager.import_from_file(path, mode)
            QMessageBox.information(self, "导入成功", f"已导入 {count} 个配方")
            self._refresh_table()
            self.recipes_changed.emit()
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"导入失败: {e}")
