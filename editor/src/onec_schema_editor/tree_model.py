"""Qt-модель дерева для QTreeView поверх :class:`Schema`.

Поддерживает:
* tri-state чекбоксы (включение узлов в выгрузку, ``sel``);
* колонки Имя / Вид / Тип / Синоним / Полное имя;
* цветовую индикацию видов и составных типов;
* редактирование структуры (удаление, добавление, смена типа).
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QAbstractItemModel, QModelIndex, Qt
from PySide6.QtGui import QBrush, QColor, QFont

from .model import Schema, SchemaNode, types_label

COL_NAME = 0
COL_KIND = 1
COL_TYPE = 2
COL_SYNONYM = 3
COL_FULLNAME = 4
COLUMNS = ["Имя", "Вид", "Тип", "Синоним", "Полное имя"]

# Цвета видов для визуализации (категория -> цвет текста).
KIND_COLORS = {
    "ConfigRoot": "#222222",
    "MetadataClass": "#6a6a6a",
    "Catalog": "#1565c0",
    "Document": "#ad1457",
    "Enum": "#6a1b9a",
    "InformationRegister": "#00695c",
    "AccumulationRegister": "#2e7d32",
    "AccountingRegister": "#827717",
    "CalculationRegister": "#4e342e",
    "Constant": "#5d4037",
    "Attribute": "#0d47a1",
    "StandardAttribute": "#789262",
    "TabularSection": "#e65100",
    "Dimension": "#00838f",
    "Resource": "#388e3c",
    "EnumValue": "#7b1fa2",
}


class SchemaTreeModel(QAbstractItemModel):
    def __init__(self, schema: Schema, parent=None):
        super().__init__(parent)
        self.schema = schema

    # -------------------------------------------------------- basic plumbing
    def set_schema(self, schema: Schema) -> None:
        self.beginResetModel()
        self.schema = schema
        self.endResetModel()

    def columnCount(self, parent=QModelIndex()) -> int:
        return len(COLUMNS)

    def rowCount(self, parent=QModelIndex()) -> int:
        if not parent.isValid():
            return len(self.schema.roots)
        node: SchemaNode = parent.internalPointer()
        return len(node.children)

    def index(self, row, column, parent=QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        if not parent.isValid():
            node = self.schema.roots[row]
        else:
            node = parent.internalPointer().children[row]
        return self.createIndex(row, column, node)

    def parent(self, index) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node: SchemaNode = index.internalPointer()
        parent = node.parent
        if parent is None:
            return QModelIndex()
        row = self._row_of(parent)
        return self.createIndex(row, 0, parent)

    def _row_of(self, node: SchemaNode) -> int:
        siblings = node.parent.children if node.parent else self.schema.roots
        return siblings.index(node)

    def index_for_node(self, node: SchemaNode, column: int = 0) -> QModelIndex:
        return self.createIndex(self._row_of(node), column, node)

    # ------------------------------------------------------------------ data
    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.column() == COL_NAME:
            flags |= Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate
            flags |= Qt.ItemIsEditable
        elif index.column() == COL_SYNONYM:
            flags |= Qt.ItemIsEditable
        return flags

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node: SchemaNode = index.internalPointer()
        col = index.column()

        if role == Qt.DisplayRole or role == Qt.EditRole:
            if col == COL_NAME:
                return node.name
            if col == COL_KIND:
                return node.title()
            if col == COL_TYPE:
                return types_label(node.types)
            if col == COL_SYNONYM:
                return node.synonym
            if col == COL_FULLNAME:
                return node.full_name
            return None

        if role == Qt.CheckStateRole and col == COL_NAME:
            return self._check_state(node)

        if role == Qt.ForegroundRole and col == COL_NAME:
            color = KIND_COLORS.get(node.kind)
            if color:
                return QBrush(QColor(color))

        if role == Qt.ForegroundRole and col == COL_TYPE and node.is_composite:
            return QBrush(QColor("#c62828"))

        if role == Qt.FontRole and col == COL_NAME:
            if node.kind in ("ConfigRoot", "MetadataClass") or node.kind in (
                "Catalog", "Document", "InformationRegister",
                "AccumulationRegister", "AccountingRegister",
                "CalculationRegister", "Enum", "Constant",
            ):
                font = QFont()
                font.setBold(True)
                return font

        if role == Qt.ToolTipRole:
            parts = [f"{node.title()}: {node.name}"]
            if node.full_name:
                parts.append(node.full_name)
            if node.synonym:
                parts.append(f"Синоним: {node.synonym}")
            if node.types:
                parts.append(f"Тип: {types_label(node.types)}")
            if node.comment:
                parts.append(f"// {node.comment}")
            return "\n".join(parts)

        return None

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid():
            return False
        node: SchemaNode = index.internalPointer()
        col = index.column()

        if role == Qt.CheckStateRole and col == COL_NAME:
            checked = Qt.CheckState(value) == Qt.Checked
            self._set_selected_recursive(node, checked)
            top = self.index_for_node(node, 0)
            self.dataChanged.emit(top, self.index_for_node(node, COL_FULLNAME))
            self._emit_subtree_changed(node)
            self._refresh_ancestors(node)
            return True

        if role == Qt.EditRole:
            if col == COL_NAME and value:
                node.name = str(value)
            elif col == COL_SYNONYM:
                node.synonym = str(value)
            else:
                return False
            self.dataChanged.emit(index, index)
            return True
        return False

    # ----------------------------------------------------------- checkstates
    def _check_state(self, node: SchemaNode) -> Qt.CheckState:
        if not node.children:
            return Qt.Checked if node.selected else Qt.Unchecked
        states = {self._check_state(c) for c in node.children}
        if states == {Qt.Checked}:
            return Qt.Checked
        if states == {Qt.Unchecked}:
            return Qt.Unchecked
        return Qt.PartiallyChecked

    def _set_selected_recursive(self, node: SchemaNode, checked: bool) -> None:
        node.selected = checked
        for child in node.children:
            self._set_selected_recursive(child, checked)

    def _emit_subtree_changed(self, node: SchemaNode) -> None:
        for child in node.children:
            idx0 = self.index_for_node(child, 0)
            self.dataChanged.emit(idx0, self.index_for_node(child, COL_FULLNAME))
            self._emit_subtree_changed(child)

    def _refresh_ancestors(self, node: SchemaNode) -> None:
        parent = node.parent
        while parent is not None:
            idx = self.index_for_node(parent, 0)
            self.dataChanged.emit(idx, idx, [Qt.CheckStateRole])
            parent = parent.parent

    # ------------------------------------------------------------ structural
    def remove_node(self, node: SchemaNode) -> None:
        parent = node.parent
        row = self._row_of(node)
        parent_index = self.index_for_node(parent, 0) if parent else QModelIndex()
        self.beginRemoveRows(parent_index, row, row)
        self.schema.remove_node(node)
        self.endRemoveRows()
        if parent:
            self._refresh_ancestors(parent)
            self.dataChanged.emit(
                self.index_for_node(parent, 0),
                self.index_for_node(parent, 0),
                [Qt.CheckStateRole],
            )

    def notify_node_changed(self, node: SchemaNode) -> None:
        self.dataChanged.emit(
            self.index_for_node(node, 0),
            self.index_for_node(node, COL_FULLNAME),
        )

    def replace_children(self, parent: SchemaNode, new_children: list[SchemaNode],
                         start_row: int, removed_count: int) -> None:
        parent_index = self.index_for_node(parent, 0)
        if removed_count:
            self.beginRemoveRows(parent_index, start_row, start_row + removed_count - 1)
            self.endRemoveRows()
        if new_children:
            self.beginInsertRows(parent_index, start_row, start_row + len(new_children) - 1)
            self.endInsertRows()
