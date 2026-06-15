"""Главное окно GUI-редактора схемы .1cmeta."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDockWidget, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressDialog, QPushButton, QTableWidget, QTableWidgetItem,
    QTreeView, QVBoxLayout, QWidget,
)

from .dialogs import DryOutDialog, TypeEditorDialog
from .model import DATA_BEARING_KINDS, KIND_TITLES, Schema, SchemaNode, types_label
from .operations import (
    SearchOptions, compute_statistics, dryout_keep_subset, dryout_split,
    find_matches, replace_in_nodes,
)
from .tree_model import COL_FULLNAME, COL_NAME, SchemaTreeModel


class MainWindow(QMainWindow):
    def __init__(self, schema: Optional[Schema] = None):
        super().__init__()
        self.setWindowTitle("Редактор схемы метаданных 1С (.1cmeta)")
        self.resize(1280, 800)

        self.schema = schema or Schema()
        self.model = SchemaTreeModel(self.schema, self)
        self._matches: list[SchemaNode] = []
        self._match_pos = -1

        self._build_tree()
        self._build_detail_dock()
        self._build_search_dock()
        self._build_stats_dock()
        self._build_actions()
        self._update_title()
        self._refresh_status()

    # --------------------------------------------------------------- widgets
    def _build_tree(self) -> None:
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.selectionModel().currentChanged.connect(self._on_current_changed)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.resizeSection(0, 360)
        self.setCentralWidget(self.tree)

    def _build_detail_dock(self) -> None:
        dock = QDockWidget("Свойства узла", self)
        w = QWidget()
        form = QFormLayout(w)
        self.d_name = QLabel("—")
        self.d_kind = QLabel("—")
        self.d_full = QLabel("—")
        self.d_synonym = QLabel("—")
        self.d_type = QLabel("—")
        self.d_type.setWordWrap(True)
        self.d_comment = QLabel("—")
        self.d_comment.setWordWrap(True)
        for lbl, widget in [
            ("Имя", self.d_name), ("Вид", self.d_kind), ("Полное имя", self.d_full),
            ("Синоним", self.d_synonym), ("Тип", self.d_type), ("Комментарий", self.d_comment),
        ]:
            form.addRow(lbl + ":", widget)
        self.kind_combo = QComboBox()
        self.kind_combo.addItems(sorted(KIND_TITLES.keys()))
        change_kind_btn = QPushButton("Сменить вид узла")
        change_kind_btn.clicked.connect(self._change_kind)
        form.addRow("Новый вид:", self.kind_combo)
        form.addRow(change_kind_btn)
        dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_search_dock(self) -> None:
        dock = QDockWidget("Поиск и замена", self)
        w = QWidget()
        layout = QVBoxLayout(w)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Что искать…")
        self.search_edit.returnPressed.connect(self._find_next)
        layout.addWidget(self.search_edit)

        opt_row = QHBoxLayout()
        self.field_combo = QComboBox()
        self.field_combo.addItems(["name", "synonym", "type", "fullname", "all"])
        self.cb_case = QCheckBox("Aa")
        self.cb_case.setToolTip("Учитывать регистр")
        self.cb_regex = QCheckBox(".*")
        self.cb_regex.setToolTip("Регулярное выражение")
        self.cb_only_sel = QCheckBox("☑")
        self.cb_only_sel.setToolTip("Только отмеченные")
        opt_row.addWidget(QLabel("Поле:"))
        opt_row.addWidget(self.field_combo)
        opt_row.addWidget(self.cb_case)
        opt_row.addWidget(self.cb_regex)
        opt_row.addWidget(self.cb_only_sel)
        layout.addLayout(opt_row)

        btn_row = QHBoxLayout()
        b_find = QPushButton("Найти все")
        b_next = QPushButton("▼")
        b_prev = QPushButton("▲")
        b_check = QPushButton("Отметить найденные")
        b_uncheck = QPushButton("Снять с найденных")
        b_find.clicked.connect(self._find_all)
        b_next.clicked.connect(self._find_next)
        b_prev.clicked.connect(self._find_prev)
        b_check.clicked.connect(lambda: self._set_matches_selected(True))
        b_uncheck.clicked.connect(lambda: self._set_matches_selected(False))
        for b in (b_find, b_prev, b_next):
            btn_row.addWidget(b)
        layout.addLayout(btn_row)
        chk_row = QHBoxLayout()
        chk_row.addWidget(b_check)
        chk_row.addWidget(b_uncheck)
        layout.addLayout(chk_row)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Заменить на…")
        layout.addWidget(self.replace_edit)
        b_replace = QPushButton("Заменить во всех найденных")
        b_replace.clicked.connect(self._replace_all)
        layout.addWidget(b_replace)

        self.search_status = QLabel("")
        layout.addWidget(self.search_status)
        layout.addStretch()

        dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

    def _build_stats_dock(self) -> None:
        dock = QDockWidget("Статистика", self)
        w = QWidget()
        layout = QVBoxLayout(w)
        self.stats_summary = QLabel("")
        layout.addWidget(self.stats_summary)
        self.stats_table = QTableWidget(0, 2)
        self.stats_table.setHorizontalHeaderLabels(["Вид", "Кол-во"])
        self.stats_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.stats_table)
        refresh = QPushButton("Обновить статистику")
        refresh.clicked.connect(self._refresh_stats)
        layout.addWidget(refresh)
        dock.setWidget(w)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _build_actions(self) -> None:
        tb = self.addToolBar("Главная")
        tb.setMovable(False)

        def act(text, slot, shortcut=None):
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            tb.addAction(a)
            return a

        act("Открыть…", self.open_file, "Ctrl+O")
        act("Сохранить", self.save_file, "Ctrl+S")
        act("Сохранить как…", self.save_file_as, "Ctrl+Shift+S")
        tb.addSeparator()
        act("Развернуть всё", self.tree.expandAll)
        act("Свернуть всё", self.tree.collapseAll)
        tb.addSeparator()
        act("Отметить всё", lambda: self._bulk_select(True))
        act("Снять всё", lambda: self._bulk_select(False))
        act("Только с данными", self._select_data_only)
        tb.addSeparator()
        act("Высушить тип", self._dryout, "Ctrl+D")
        act("Редактировать тип", self._edit_type, "Ctrl+T")
        act("Удалить узел", self._delete_node, "Del")

    # ------------------------------------------------------------- file ops
    def open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Открыть схему", "", "Схема 1С (*.1cmeta);;Все файлы (*)")
        if not path:
            return
        dlg = QProgressDialog("Загрузка схемы…", "Отмена", 0, 100, self)
        dlg.setWindowModality(Qt.WindowModal)

        def progress(read, total):
            if total:
                dlg.setValue(int(read * 100 / total))
            QApplication.processEvents()

        try:
            schema = Schema.load(path, progress=progress)
        except Exception as exc:  # noqa: BLE001
            dlg.close()
            QMessageBox.critical(self, "Ошибка", f"Не удалось загрузить:\n{exc}")
            return
        dlg.close()
        self.schema = schema
        self.model.set_schema(schema)
        self.tree.expandToDepth(1)
        self._update_title()
        self._refresh_status()
        self._refresh_stats()

    def save_file(self) -> None:
        if not self.schema.source_path:
            self.save_file_as()
            return
        self._do_save(self.schema.source_path)

    def save_file_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить схему", self.schema.source_path or "",
            "Схема 1С (*.1cmeta);;Все файлы (*)")
        if path:
            self._do_save(path)

    def _do_save(self, path: str) -> None:
        try:
            self.schema.save(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка", f"Не удалось сохранить:\n{exc}")
            return
        self._update_title()
        self.statusBar().showMessage(f"Сохранено: {path}", 4000)

    # ---------------------------------------------------------- node helpers
    def _current_node(self) -> Optional[SchemaNode]:
        idx = self.tree.currentIndex()
        return idx.internalPointer() if idx.isValid() else None

    def _on_current_changed(self, current, _previous) -> None:
        node = current.internalPointer() if current.isValid() else None
        if not node:
            return
        self.d_name.setText(node.name)
        self.d_kind.setText(node.title())
        self.d_full.setText(node.full_name or "—")
        self.d_synonym.setText(node.synonym or "—")
        self.d_type.setText(types_label(node.types) or "—")
        self.d_comment.setText(node.comment or "—")
        self.kind_combo.setCurrentText(node.kind)

    def _change_kind(self) -> None:
        node = self._current_node()
        if not node:
            return
        node.data["kind"] = self.kind_combo.currentText()
        self.model.notify_node_changed(node)

    def _edit_type(self) -> None:
        node = self._current_node()
        if not node:
            return
        dlg = TypeEditorDialog(node, self)
        if dlg.exec():
            node.types = dlg.result_types()
            self.model.notify_node_changed(node)
            self._on_current_changed(self.tree.currentIndex(), None)

    def _dryout(self) -> None:
        node = self._current_node()
        if not node:
            return
        if not node.is_composite:
            QMessageBox.information(self, "Высушивание",
                                    "Тип узла не составной — высушивать нечего.")
            return
        dlg = DryOutDialog(node, self)
        if not dlg.exec():
            return
        indices = dlg.selected_indices()
        if not indices:
            QMessageBox.warning(self, "Высушивание", "Не выбран ни один тип.")
            return
        parent = node.parent
        if dlg.mode() == DryOutDialog.MODE_SPLIT and parent is not None:
            row = parent.children.index(node)
            created = dryout_split(self.schema, node, indices)
            self.model.replace_children(parent, created, row, 1)
        else:
            dryout_keep_subset(node, indices)
            self.model.notify_node_changed(node)
        self._on_current_changed(self.tree.currentIndex(), None)

    def _delete_node(self) -> None:
        node = self._current_node()
        if not node:
            return
        if QMessageBox.question(
                self, "Удаление",
                f"Удалить узел «{node.name}» и всё его поддерево?") != QMessageBox.Yes:
            return
        self.model.remove_node(node)
        self._refresh_status()

    # -------------------------------------------------------------- bulk sel
    def _bulk_select(self, value: bool) -> None:
        for node in self.schema.all_nodes():
            node.selected = value
        self.model.set_schema(self.schema)
        self.tree.expandToDepth(1)
        self._refresh_status()

    def _select_data_only(self) -> None:
        for node in self.schema.all_nodes():
            on_data_branch = node.kind in DATA_BEARING_KINDS or node.is_field \
                or node.kind in ("TabularSection", "StandardTabularSection") \
                or node.kind in ("ConfigRoot", "MetadataClass")
            node.selected = on_data_branch
        self.model.set_schema(self.schema)
        self.tree.expandToDepth(1)
        self._refresh_status()

    # ----------------------------------------------------------- search ops
    def _current_options(self) -> SearchOptions:
        return SearchOptions(
            text=self.search_edit.text(),
            field=self.field_combo.currentText(),
            case_sensitive=self.cb_case.isChecked(),
            regex=self.cb_regex.isChecked(),
            only_selected=self.cb_only_sel.isChecked(),
        )

    def _find_all(self) -> None:
        try:
            self._matches = find_matches(self.schema, self._current_options())
        except Exception as exc:  # noqa: BLE001 (bad regex)
            QMessageBox.warning(self, "Поиск", f"Ошибка поиска:\n{exc}")
            return
        self._match_pos = -1
        self.search_status.setText(f"Найдено: {len(self._matches)}")
        if self._matches:
            self._find_next()

    def _find_next(self) -> None:
        if not self._matches:
            self._find_all()
            if not self._matches:
                return
        self._match_pos = (self._match_pos + 1) % len(self._matches)
        self._reveal(self._matches[self._match_pos])

    def _find_prev(self) -> None:
        if not self._matches:
            return
        self._match_pos = (self._match_pos - 1) % len(self._matches)
        self._reveal(self._matches[self._match_pos])

    def _reveal(self, node: SchemaNode) -> None:
        idx = self.model.index_for_node(node, COL_NAME)
        self.tree.scrollTo(idx)
        self.tree.setCurrentIndex(idx)
        self.search_status.setText(
            f"{self._match_pos + 1} / {len(self._matches)}")

    def _set_matches_selected(self, value: bool) -> None:
        if not self._matches:
            self._find_all()
        for node in self._matches:
            node.selected = value
        self.model.set_schema(self.schema)
        self.tree.expandToDepth(1)
        self._refresh_status()

    def _replace_all(self) -> None:
        if not self._matches:
            self._find_all()
        if not self._matches:
            return
        opts = self._current_options()
        count = replace_in_nodes(self._matches, opts, self.replace_edit.text())
        self.model.set_schema(self.schema)
        self.tree.expandToDepth(1)
        self.search_status.setText(f"Заменено вхождений: {count}")

    # ---------------------------------------------------------- context menu
    def _show_context_menu(self, pos) -> None:
        node = self._current_node()
        if not node:
            return
        menu = QMenu(self)
        menu.addAction("Редактировать тип", self._edit_type)
        if node.is_composite:
            menu.addAction("Высушить составной тип", self._dryout)
        menu.addAction("Сменить вид…", self._focus_kind_combo)
        menu.addSeparator()
        menu.addAction("Отметить поддерево", lambda: self._set_subtree(node, True))
        menu.addAction("Снять поддерево", lambda: self._set_subtree(node, False))
        menu.addSeparator()
        menu.addAction("Удалить узел", self._delete_node)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _focus_kind_combo(self) -> None:
        self.kind_combo.setFocus()
        self.kind_combo.showPopup()

    def _set_subtree(self, node: SchemaNode, value: bool) -> None:
        node.selected = value
        for desc in node.iter_descendants():
            desc.selected = value
        self.model.set_schema(self.schema)
        self.tree.expandToDepth(1)
        self._refresh_status()

    # -------------------------------------------------------------- statuses
    def _update_title(self) -> None:
        name = self.schema.header.get("config", "") if self.schema.header else ""
        path = self.schema.source_path or "(не сохранено)"
        self.setWindowTitle(f"Редактор схемы 1С — {name} — {os.path.basename(path)}")

    def _refresh_status(self) -> None:
        total = len(self.schema.nodes_by_id)
        selected = sum(1 for n in self.schema.all_nodes() if n.selected)
        self.statusBar().showMessage(f"Узлов: {total}  |  Отмечено: {selected}")

    def _refresh_stats(self) -> None:
        st = compute_statistics(self.schema)
        self.stats_summary.setText(
            f"Всего узлов: {st.total_nodes}\n"
            f"Отмечено: {st.selected_nodes}\n"
            f"Объектов с данными: {st.objects}\n"
            f"Составных типов: {st.composite_fields}\n"
            f"Ссылочных целей (уник.): {len(st.ref_targets)}")
        rows = sorted(st.by_kind.items(), key=lambda kv: -kv[1])
        self.stats_table.setRowCount(len(rows))
        for i, (kind, cnt) in enumerate(rows):
            self.stats_table.setItem(i, 0, QTableWidgetItem(KIND_TITLES.get(kind, kind)))
            self.stats_table.setItem(i, 1, QTableWidgetItem(str(cnt)))


def main(argv=None) -> int:
    import sys

    argv = argv if argv is not None else sys.argv
    app = QApplication(argv)
    schema = None
    if len(argv) > 1 and os.path.exists(argv[1]):
        schema = Schema.load(argv[1])
    win = MainWindow(schema)
    if schema:
        win.tree.expandToDepth(1)
        win._refresh_status()
        win._refresh_stats()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
