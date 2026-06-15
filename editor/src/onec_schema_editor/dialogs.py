"""Диалоги редактора: редактирование типа узла и высушивание составного типа."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QRadioButton, QSpinBox, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from .model import SchemaNode, type_descriptor_label

PRIMITIVE_TYPES = ["String", "Number", "Date", "Boolean", "UUID", "ValueStorage", "Type"]
ALL_TYPES = PRIMITIVE_TYPES + ["Ref", "EnumRef", "AnyRef", "DefinedType", "Unknown"]


class TypeEditorDialog(QDialog):
    """Редактирование массива дескрипторов типа узла."""

    def __init__(self, node: SchemaNode, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Тип узла: {node.name}")
        self.resize(640, 380)
        self.node = node
        self.descriptors = [dict(d) for d in node.types]

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Тип", "Параметры"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 160)
        layout.addWidget(self.table)

        btns = QHBoxLayout()
        add_btn = QPushButton("Добавить тип")
        del_btn = QPushButton("Удалить выбранный")
        add_btn.clicked.connect(self._add_type)
        del_btn.clicked.connect(self._del_type)
        btns.addWidget(add_btn)
        btns.addWidget(del_btn)
        btns.addStretch()
        layout.addLayout(btns)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        for desc in self.descriptors:
            self._append_row(desc)

    def _append_row(self, desc: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)

        combo = QComboBox()
        combo.addItems(ALL_TYPES)
        combo.setCurrentText(desc.get("t", "String"))
        combo.currentTextChanged.connect(lambda v, r=row: self._on_type_changed(r, v))
        self.table.setCellWidget(row, 0, combo)

        self.table.setCellWidget(row, 1, self._make_params_widget(desc))

    def _make_params_widget(self, desc: dict) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(2, 2, 2, 2)
        t = desc.get("t")
        if t == "String":
            spin = QSpinBox()
            spin.setMaximum(1_000_000)
            spin.setValue(desc.get("len", 0))
            spin.valueChanged.connect(lambda v: desc.__setitem__("len", v))
            form.addRow("Длина", spin)
        elif t == "Number":
            d = QSpinBox(); d.setMaximum(64); d.setValue(desc.get("digits", 0))
            f = QSpinBox(); f.setMaximum(32); f.setValue(desc.get("fraction", 0))
            d.valueChanged.connect(lambda v: desc.__setitem__("digits", v))
            f.valueChanged.connect(lambda v: desc.__setitem__("fraction", v))
            form.addRow("Разрядность", d)
            form.addRow("Дробная часть", f)
        elif t == "Date":
            combo = QComboBox(); combo.addItems(["Date", "Time", "DateTime"])
            combo.setCurrentText(desc.get("dateParts", "DateTime"))
            combo.currentTextChanged.connect(lambda v: desc.__setitem__("dateParts", v))
            form.addRow("Части даты", combo)
        elif t in ("Ref", "EnumRef", "DefinedType"):
            edit = QLineEdit(desc.get("meta", desc.get("name", "")))
            key = "meta" if t in ("Ref", "EnumRef") else "name"
            edit.textChanged.connect(lambda v: desc.__setitem__(key, v))
            form.addRow("Объект", edit)
        elif t == "Unknown":
            edit = QLineEdit(desc.get("raw", ""))
            edit.textChanged.connect(lambda v: desc.__setitem__("raw", v))
            form.addRow("raw", edit)
        else:
            form.addRow(QLabel("—"))
        return w

    def _on_type_changed(self, row: int, new_type: str) -> None:
        desc = self.descriptors[row]
        desc.clear()
        desc["t"] = new_type
        self.table.setCellWidget(row, 1, self._make_params_widget(desc))

    def _add_type(self) -> None:
        desc = {"t": "String", "len": 0}
        self.descriptors.append(desc)
        self._append_row(desc)

    def _del_type(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            del self.descriptors[row]

    def result_types(self) -> list[dict]:
        return self.descriptors


class DryOutDialog(QDialog):
    """Мастер высушивания составного типа."""

    MODE_KEEP = "keep"
    MODE_SPLIT = "split"

    def __init__(self, node: SchemaNode, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Высушивание составного типа: {node.name}")
        self.resize(520, 420)
        self.node = node

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            f"Узел «{node.name}» имеет {len(node.types)} типов.\n"
            "Отметьте типы, которые нужно оставить, и выберите режим."))

        self.list = QListWidget()
        for i, desc in enumerate(node.types):
            item = QListWidgetItem(type_descriptor_label(desc))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, i)
            self.list.addItem(item)
        layout.addWidget(self.list)

        self.group = QButtonGroup(self)
        self.rb_keep = QRadioButton("Оставить выбранные типы в одном узле")
        self.rb_split = QRadioButton("Разнести выбранные типы по отдельным узлам (split)")
        self.rb_keep.setChecked(True)
        self.group.addButton(self.rb_keep)
        self.group.addButton(self.rb_split)
        layout.addWidget(self.rb_keep)
        layout.addWidget(self.rb_split)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def selected_indices(self) -> list[int]:
        result = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == Qt.Checked:
                result.append(item.data(Qt.UserRole))
        return result

    def mode(self) -> str:
        return self.MODE_SPLIT if self.rb_split.isChecked() else self.MODE_KEEP
