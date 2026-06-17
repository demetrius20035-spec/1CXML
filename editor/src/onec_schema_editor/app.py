"""Главное окно GUI-редактора схемы .1cmeta."""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QSortFilterProxyModel
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDockWidget, QFileDialog,
    QFormLayout, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressDialog,
    QPushButton, QTableWidget, QTableWidgetItem, QTreeView, QVBoxLayout, QWidget,
)

from . import estimate as estimate_mod
from . import exporters, importers, llm_export, profiles, validate
from .datafile import summarize
from .diff import diff_schemas
from .dialogs import DryOutDialog, TypeEditorDialog
from .filters import NodeFilter, known_object_names
from .history import (
    History, SelectionCommand, SubtreeCommand, TypesCommand,
)
from .model import DATA_BEARING_KINDS, KIND_TITLES, Schema, SchemaNode, types_label
from .operations import (
    SearchOptions, bulk_delete, bulk_set_kind, bulk_set_selected,
    compute_statistics, dryout_keep_subset, dryout_split,
    find_matches, replace_in_nodes,
)
from .sqlgen import DDLOptions, generate_ddl
from .tree_model import COL_FULLNAME, COL_NAME, SchemaFilterProxy, SchemaTreeModel
from .ui_extra import DataViewerDialog, DiffDialog, GraphDialog
from .ui_integrations import IntegrationsDialog


class MainWindow(QMainWindow):
    def __init__(self, schema: Optional[Schema] = None):
        super().__init__()
        self.setWindowTitle("Редактор схемы метаданных 1С (.1cmeta)")
        self.resize(1280, 800)

        self.schema = schema or Schema()
        self.model = SchemaTreeModel(self.schema, self)
        self.history = History()
        self.model.history = self.history
        self.proxy = SchemaFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self._matches: list[SchemaNode] = []
        self._match_pos = -1
        self.ddl_opts = DDLOptions()
        self._refresh_known()

        self._build_tree()
        self._build_detail_dock()
        self._build_search_dock()
        self._build_stats_dock()
        self._build_filter_dock()
        self._build_actions()
        self._build_menu()
        self._update_title()
        self._refresh_status()

    # --------------------------------------------------------------- widgets
    def _build_tree(self) -> None:
        self.tree = QTreeView()
        self.tree.setModel(self.proxy)
        self.tree.setSelectionBehavior(QTreeView.SelectRows)
        self.tree.setSelectionMode(QTreeView.ExtendedSelection)
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

    def _build_filter_dock(self) -> None:
        dock = QDockWidget("Фильтр дерева", self)
        w = QWidget()
        layout = QVBoxLayout(w)
        self.filter_text = QLineEdit()
        self.filter_text.setPlaceholderText("Текст (имя/синоним/полное имя)…")
        self.filter_text.returnPressed.connect(self._apply_tree_filter)
        layout.addWidget(self.filter_text)
        self.f_only_selected = QCheckBox("Только отмеченные")
        self.f_only_composite = QCheckBox("Только составные типы")
        self.f_only_dangling = QCheckBox("Только висячие ссылки")
        self.f_only_data = QCheckBox("Только объекты/поля данных")
        for cb in (self.f_only_selected, self.f_only_composite,
                   self.f_only_dangling, self.f_only_data):
            layout.addWidget(cb)
        row = QHBoxLayout()
        b_apply = QPushButton("Применить")
        b_clear = QPushButton("Сбросить")
        b_apply.clicked.connect(self._apply_tree_filter)
        b_clear.clicked.connect(self._clear_tree_filter)
        row.addWidget(b_apply)
        row.addWidget(b_clear)
        layout.addLayout(row)
        layout.addStretch()
        dock.setWidget(w)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)

    def _apply_tree_filter(self) -> None:
        flt = NodeFilter(
            text=self.filter_text.text(),
            only_selected=self.f_only_selected.isChecked(),
            only_composite=self.f_only_composite.isChecked(),
            only_dangling=self.f_only_dangling.isChecked(),
            only_with_data=self.f_only_data.isChecked(),
        )
        self.proxy.set_filter(flt, self.model.known)
        if flt.is_active():
            self.tree.expandAll()
        else:
            self.tree.expandToDepth(1)

    def _clear_tree_filter(self) -> None:
        self.filter_text.clear()
        for cb in (self.f_only_selected, self.f_only_composite,
                   self.f_only_dangling, self.f_only_data):
            cb.setChecked(False)
        self.proxy.set_filter(None, self.model.known)
        self.tree.expandToDepth(1)

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
        tb.addSeparator()
        act("Отменить", self._undo, "Ctrl+Z")
        act("Повторить", self._redo, "Ctrl+Y")

    def _build_menu(self) -> None:
        bar = self.menuBar()

        m_file = bar.addMenu("Файл")
        m_file.addAction("Открыть схему…", self.open_file)
        m_file.addAction("Импорт из Configuration.xml…", self._import_xml)
        m_file.addSeparator()
        m_file.addAction("Сохранить", self.save_file)
        m_file.addAction("Сохранить как…", self.save_file_as)

        m_prof = bar.addMenu("Профиль")
        m_prof.addAction("Сохранить профиль…", self._save_profile)
        m_prof.addAction("Применить профиль…", self._apply_profile)

        m_an = bar.addMenu("Анализ")
        m_an.addAction("Сравнить со схемой… (diff)", self._diff_with)
        m_an.addAction("Проверить ссылочную целостность", self._validate)
        m_an.addAction("Граф связей объектов", self._show_graph)
        m_an.addAction("Оценка объёмов…", self._estimate)
        m_an.addAction("Просмотр данных .1cdata…", self._view_data)

        m_exp = bar.addMenu("Экспорт")
        m_exp.addAction("Паспорт для LLM (Markdown)…", lambda: self._export_passport("md"))
        m_exp.addAction("Паспорт для LLM (JSON)…", lambda: self._export_passport("json"))
        m_exp.addSeparator()
        m_exp.addAction("DDL: MariaDB…", lambda: self._generate_ddl("mariadb"))
        m_exp.addAction("DDL: ClickHouse…", lambda: self._generate_ddl("clickhouse"))
        m_exp.addAction("DDL: YDB…", lambda: self._generate_ddl("ydb"))
        m_exp.addSeparator()
        m_exp.addAction("Для LLM: JSON Schema…", lambda: self._export_llm("jsonschema"))
        m_exp.addAction("Для LLM: Pydantic…", lambda: self._export_llm("pydantic"))
        m_exp.addAction("Для LLM: SQLAlchemy…", lambda: self._export_llm("sqlalchemy"))
        m_exp.addAction("Для LLM: dbt schema.yml…", lambda: self._export_llm("dbt"))

        m_int = bar.addMenu("Интеграции")
        m_int.addAction("СУБД и Yandex Cloud…", self._open_integrations)

        m_set = bar.addMenu("Настройки SQL")
        self.act_translit = QAction("Транслитерация имён", self, checkable=True)
        self.act_translit.setChecked(self.ddl_opts.transliterate)
        self.act_translit.toggled.connect(
            lambda v: setattr(self.ddl_opts, "transliterate", v))
        m_set.addAction(self.act_translit)
        self.act_pres = QAction("Колонки представлений ссылок (_view)", self, checkable=True)
        self.act_pres.setChecked(self.ddl_opts.include_presentation)
        self.act_pres.toggled.connect(
            lambda v: setattr(self.ddl_opts, "include_presentation", v))
        m_set.addAction(self.act_pres)
        m_set.addAction("Имя базы данных…", self._set_database)

    # -------------------------------------------------- новые обработчики
    def _import_xml(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Каталог XML-выгрузки конфигурации (с Configuration.xml)")
        if not path:
            return
        try:
            schema = importers.import_configuration(path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Ошибка импорта", str(exc))
            return
        self.schema = schema
        self.history.clear()
        self.model.set_schema(schema)
        self._refresh_known()
        self.tree.expandToDepth(1)
        self._update_title()
        self._refresh_status()
        self._refresh_stats()
        QMessageBox.information(self, "Импорт",
                                f"Импортировано узлов: {len(schema.nodes_by_id)}")

    def _save_profile(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить профиль", "",
                                              "Профиль (*.json)")
        if not path:
            return
        name, _ = QInputDialog.getText(self, "Профиль", "Имя профиля:")
        profiles.save_profile(self.schema, path, name=name or "",
                              settings={"ddl": vars(self.ddl_opts)})
        self.statusBar().showMessage(f"Профиль сохранён: {path}", 4000)

    def _apply_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Применить профиль", "",
                                              "Профиль (*.json)")
        if not path:
            return
        prof = profiles.load_profile(path)
        changed, missing = profiles.apply_profile(self.schema, prof)
        self.history.clear()  # профиль меняет много галок — сбрасываем историю
        self._reset_view()
        QMessageBox.information(self, "Профиль применён",
                                f"Изменено галок: {changed}\nНе найдено в схеме: {missing}")

    def _diff_with(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Схема для сравнения", "",
                                              "Схема 1С (*.1cmeta)")
        if not path:
            return
        other = Schema.load(path)
        result = diff_schemas(other, self.schema)
        DiffDialog(result, self).exec()

    def _validate(self) -> None:
        report = validate.validate_schema(self.schema)
        lines = [report.summary(), ""]
        for issue in report.errors + report.warnings:
            mark = "❌" if issue.severity == "error" else "⚠️"
            lines.append(f"{mark} {issue.path}: {issue.message}")
        dlg = QDialog(self)
        dlg.setWindowTitle("Ссылочная целостность")
        dlg.resize(760, 520)
        lay = QVBoxLayout(dlg)
        text = QPlainTextEdit("\n".join(lines))
        text.setReadOnly(True)
        lay.addWidget(text)
        dlg.exec()

    def _show_graph(self) -> None:
        GraphDialog(self.schema, self).exec()

    def _estimate(self) -> None:
        summary = None
        if QMessageBox.question(
                self, "Оценка объёмов",
                "Подгрузить файл .1cdata для точного числа строк?\n"
                "(Нет — оценка только по ширине строк схемы)") == QMessageBox.Yes:
            path, _ = QFileDialog.getOpenFileName(self, "Файл данных", "",
                                                  "Данные 1С (*.1cdata)")
            if path:
                summary = summarize(path)
        rows = estimate_mod.estimate(self.schema, self.ddl_opts, summary)
        dlg = QDialog(self)
        dlg.setWindowTitle("Оценка объёмов")
        dlg.resize(760, 520)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(rows), 4)
        table.setHorizontalHeaderLabels(["Объект", "Колонок", "Строк", "≈ Размер"])
        total = 0
        for i, e in enumerate(rows):
            table.setItem(i, 0, QTableWidgetItem(e.full_name))
            table.setItem(i, 1, QTableWidgetItem(str(e.columns)))
            table.setItem(i, 2, QTableWidgetItem(str(e.rows)))
            table.setItem(i, 3, QTableWidgetItem(estimate_mod.human_size(e.est_bytes)))
            total += e.est_bytes
        table.resizeColumnsToContents()
        lay.addWidget(QLabel(f"Итого ≈ {estimate_mod.human_size(total)}"))
        lay.addWidget(table)
        dlg.exec()

    def _view_data(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Файл данных .1cdata", "",
                                              "Данные 1С (*.1cdata)")
        if path:
            DataViewerDialog(path, self).exec()

    def _export_llm(self, fmt: str) -> None:
        builders = {
            "jsonschema": (llm_export.to_json_schema, "json", "JSON Schema"),
            "pydantic": (llm_export.to_pydantic, "py", "Pydantic"),
            "sqlalchemy": (llm_export.to_sqlalchemy, "py", "SQLAlchemy"),
            "dbt": (llm_export.to_dbt_yaml, "yml", "dbt schema"),
        }
        builder, ext, title = builders[fmt]
        content = builder(self.schema)
        tokens = llm_export.estimate_tokens(content)
        path, _ = QFileDialog.getSaveFileName(
            self, f"Экспорт: {title}", f"schema.{ext}", f"*.{ext}")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.statusBar().showMessage(
            f"{title} сохранён: {path}  (≈ {tokens} токенов)", 6000)

    def _export_passport(self, fmt: str) -> None:
        ext = "md" if fmt == "md" else "json"
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить паспорт", "", f"Паспорт (*.{ext})")
        if not path:
            return
        content = exporters.export_markdown(self.schema) if fmt == "md" \
            else exporters.export_json(self.schema)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        self.statusBar().showMessage(f"Паспорт сохранён: {path}", 4000)

    def _generate_ddl(self, dialect: str) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, f"schema.sql ({dialect})", f"schema_{dialect}.sql", "SQL (*.sql)")
        if not path:
            return
        sql = generate_ddl(self.schema, dialect, self.ddl_opts)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sql)
        self.statusBar().showMessage(f"DDL сохранён: {path}", 4000)

    def _set_database(self) -> None:
        name, ok = QInputDialog.getText(self, "Имя базы данных",
                                        "База:", text=self.ddl_opts.database)
        if ok and name:
            self.ddl_opts.database = name

    def _open_integrations(self) -> None:
        IntegrationsDialog(lambda: self.schema, lambda: self.ddl_opts, self).exec()

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
        self.history.clear()
        self.model.set_schema(schema)
        self._refresh_known()
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
    def _node_of(self, proxy_index) -> Optional[SchemaNode]:
        if not proxy_index.isValid():
            return None
        return self.proxy.mapToSource(proxy_index).internalPointer()

    def _current_node(self) -> Optional[SchemaNode]:
        return self._node_of(self.tree.currentIndex())

    def _selected_nodes(self) -> list[SchemaNode]:
        seen, result = set(), []
        for idx in self.tree.selectionModel().selectedRows(0):
            node = self._node_of(idx)
            if node is not None and id(node) not in seen:
                seen.add(id(node))
                result.append(node)
        return result

    def _refresh_known(self) -> None:
        self.model.set_known(known_object_names(self.schema))

    def _reset_view(self) -> None:
        """Полное обновление модели/дерева (после undo/redo и массовых правок)."""
        self.model.set_schema(self.schema)
        self._refresh_known()
        self.tree.expandToDepth(1)
        self._refresh_status()

    def _on_current_changed(self, current, _previous) -> None:
        node = self._node_of(current)
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
        from .history import FieldCommand
        node = self._current_node()
        if not node:
            return
        old, new = node.kind, self.kind_combo.currentText()
        if old == new:
            return
        node.data["kind"] = new
        self.history.push(FieldCommand(node, "kind", old, new, "смена вида"))
        self.model.notify_node_changed(node)

    def _edit_type(self) -> None:
        node = self._current_node()
        if not node:
            return
        old_types = [dict(d) for d in node.types]
        dlg = TypeEditorDialog(node, self)
        if dlg.exec():
            node.types = dlg.result_types()
            self.history.push(TypesCommand(node, old_types, node.types, "изменение типа"))
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
            before = self.schema.snapshot_children(parent)
            dryout_split(self.schema, node, indices)
            after = self.schema.snapshot_children(parent)
            self.history.push(SubtreeCommand(self.schema, parent, before, after, "высушивание (split)"))
            self._reset_view()
        else:
            old_types = [dict(d) for d in node.types]
            dryout_keep_subset(node, indices)
            self.history.push(TypesCommand(node, old_types, node.types, "высушивание"))
            self.model.notify_node_changed(node)
            self._on_current_changed(self.tree.currentIndex(), None)

    def _delete_node(self) -> None:
        from .history import CompositeCommand

        nodes = self._selected_nodes() or ([self._current_node()] if self._current_node() else [])
        nodes = [n for n in nodes if n is not None and n.parent is not None]
        if not nodes:
            return
        подпись = nodes[0].name if len(nodes) == 1 else f"{len(nodes)} узлов"
        if QMessageBox.question(
                self, "Удаление",
                f"Удалить «{подпись}» вместе с поддеревом?") != QMessageBox.Yes:
            return
        # снимок затронутых родителей до удаления
        parents = []
        seen = set()
        for n in nodes:
            if id(n.parent) not in seen:
                seen.add(id(n.parent))
                parents.append(n.parent)
        before = {id(p): (p, self.schema.snapshot_children(p)) for p in parents}
        bulk_delete(self.schema, nodes)
        commands = []
        for p in parents:
            after = self.schema.snapshot_children(p)
            commands.append(SubtreeCommand(self.schema, p, before[id(p)][1], after, "удаление"))
        self.history.push(CompositeCommand(commands, "удаление"))
        self._reset_view()

    def _undo(self) -> None:
        cmd = self.history.undo()
        if cmd:
            self._reset_view()
            self.statusBar().showMessage("Отменено: " + cmd.label, 3000)

    def _redo(self) -> None:
        cmd = self.history.redo()
        if cmd:
            self._reset_view()
            self.statusBar().showMessage("Повторено: " + cmd.label, 3000)

    # -------------------------------------------------------------- bulk sel
    def _apply_selection(self, targets, value: bool, label: str) -> None:
        changes = [(n, n.selected, value) for n in targets if n.selected != value]
        if not changes:
            return
        for n, _old, _new in changes:
            n.selected = value
        self.history.push(SelectionCommand(changes, label))
        self._reset_view()

    def _bulk_select(self, value: bool) -> None:
        self._apply_selection(list(self.schema.all_nodes()), value, "массовая галка")

    def _select_data_only(self) -> None:
        on, off = [], []
        for node in self.schema.all_nodes():
            data_branch = node.kind in DATA_BEARING_KINDS or node.is_field \
                or node.kind in ("TabularSection", "StandardTabularSection") \
                or node.kind in ("ConfigRoot", "MetadataClass")
            (on if data_branch else off).append(node)
        changes = [(n, n.selected, True) for n in on if not n.selected]
        changes += [(n, n.selected, False) for n in off if n.selected]
        for n, _o, new in changes:
            n.selected = new
        if changes:
            self.history.push(SelectionCommand(changes, "только с данными"))
        self._reset_view()

    def _bulk_selected_nodes(self, value: bool) -> None:
        nodes = self._selected_nodes()
        if not nodes:
            return
        targets = []
        for n in nodes:
            targets.append(n)
            targets.extend(n.iter_descendants())
        self._apply_selection(targets, value, "галка выделенных")

    def _bulk_kind_selected(self) -> None:
        from .history import FieldCommand, CompositeCommand
        nodes = self._selected_nodes()
        if not nodes:
            return
        kind, ok = QInputDialog.getItem(self, "Сменить вид", "Новый вид:",
                                        sorted(KIND_TITLES.keys()), editable=False)
        if not ok or not kind:
            return
        cmds = []
        for n in nodes:
            if n.kind != kind:
                cmds.append(FieldCommand(n, "kind", n.kind, kind, "смена вида"))
                n.data["kind"] = kind
        if cmds:
            self.history.push(CompositeCommand(cmds, "смена вида выделенных"))
        self._reset_view()

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
        idx = self.proxy.mapFromSource(self.model.index_for_node(node, COL_NAME))
        self.tree.scrollTo(idx)
        self.tree.setCurrentIndex(idx)
        self.search_status.setText(
            f"{self._match_pos + 1} / {len(self._matches)}")

    def _set_matches_selected(self, value: bool) -> None:
        if not self._matches:
            self._find_all()
        self._apply_selection(list(self._matches), value, "галка найденных")

    def _replace_all(self) -> None:
        if not self._matches:
            self._find_all()
        if not self._matches:
            return
        opts = self._current_options()
        count = replace_in_nodes(self._matches, opts, self.replace_edit.text())
        self._reset_view()
        self.search_status.setText(f"Заменено вхождений: {count}")

    # ---------------------------------------------------------- context menu
    def _show_context_menu(self, pos) -> None:
        node = self._current_node()
        if not node:
            return
        selected = self._selected_nodes()
        menu = QMenu(self)
        if len(selected) > 1:
            menu.addAction(f"Отметить выделенные ({len(selected)})",
                           lambda: self._bulk_selected_nodes(True))
            menu.addAction("Снять с выделенных",
                           lambda: self._bulk_selected_nodes(False))
            menu.addAction("Сменить вид у выделенных…", self._bulk_kind_selected)
            menu.addSeparator()
            menu.addAction(f"Удалить выделенные ({len(selected)})", self._delete_node)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
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
        targets = [node] + list(node.iter_descendants())
        self._apply_selection(targets, value, "галка поддерева")

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
