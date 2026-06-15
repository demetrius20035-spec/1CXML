"""Доп. окна: diff схем, просмотрщик .1cdata, граф связей объектов."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGraphicsEllipseItem, QGraphicsLineItem, QGraphicsScene,
    QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QPlainTextEdit,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from .datafile import iter_rows, ref_presentation, ref_value
from .diff import SchemaDiff
from .model import DATA_BEARING_KINDS, Schema


# --------------------------------------------------------------------- diff
class DiffDialog(QDialog):
    def __init__(self, result: SchemaDiff, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Сравнение схем")
        self.resize(820, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(result.summary()))
        tabs = QTabWidget()
        tabs.addTab(self._list_tab(result.added), f"Добавлено ({len(result.added)})")
        tabs.addTab(self._list_tab(result.removed), f"Удалено ({len(result.removed)})")
        tabs.addTab(self._pairs_tab(result.type_changed, "Тип"),
                    f"Сменился тип ({len(result.type_changed)})")
        tabs.addTab(self._pairs_tab(result.kind_changed, "Вид"),
                    f"Сменился вид ({len(result.kind_changed)})")
        layout.addWidget(tabs)

    def _list_tab(self, items):
        w = QPlainTextEdit()
        w.setReadOnly(True)
        w.setPlainText("\n".join(items) if items else "—")
        return w

    def _pairs_tab(self, pairs, what):
        table = QTableWidget(len(pairs), 3)
        table.setHorizontalHeaderLabels(["Путь", f"{what} (было)", f"{what} (стало)"])
        for i, (path, old, new) in enumerate(pairs):
            table.setItem(i, 0, QTableWidgetItem(path))
            table.setItem(i, 1, QTableWidgetItem(str(old)))
            table.setItem(i, 2, QTableWidgetItem(str(new)))
        table.resizeColumnsToContents()
        return table


# --------------------------------------------------------------- data viewer
class DataViewerDialog(QDialog):
    MAX_ROWS = 1000

    def __init__(self, data_path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Просмотр данных: {data_path}")
        self.resize(1000, 600)
        self.data_path = data_path

        # группируем строки по объектам (ограниченно)
        self.rows_by_obj: dict[str, list] = {}
        self.columns_by_obj: dict[str, list] = {}
        for obj, data in iter_rows(data_path):
            bucket = self.rows_by_obj.setdefault(obj.full_name, [])
            if len(bucket) < self.MAX_ROWS:
                bucket.append(data)
                cols = self.columns_by_obj.setdefault(obj.full_name, [])
                for k in data:
                    if k not in cols:
                        cols.append(k)

        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Объект:"))
        self.selector = QComboBox()
        self.selector.addItems(sorted(self.rows_by_obj.keys()))
        self.selector.currentTextChanged.connect(self._show)
        top.addWidget(self.selector)
        top.addStretch()
        layout.addLayout(top)
        self.table = QTableWidget()
        layout.addWidget(self.table)
        if self.rows_by_obj:
            self._show(self.selector.currentText())

    def _show(self, full_name: str):
        rows = self.rows_by_obj.get(full_name, [])
        cols = self.columns_by_obj.get(full_name, [])
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(rows))
        self.table.setHorizontalHeaderLabels(cols)
        for r, data in enumerate(rows):
            for c, key in enumerate(cols):
                val = data.get(key)
                pres = ref_presentation(val)
                text = pres if pres is not None else ("" if val is None else str(ref_value(val)))
                self.table.setItem(r, c, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()


# ----------------------------------------------------------------- graph view
class GraphDialog(QDialog):
    """Граф связей объектов по ссылочным типам (круговая раскладка)."""

    def __init__(self, schema: Schema, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Граф связей объектов")
        self.resize(900, 700)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            "Узлы — объекты с данными; рёбра — ссылочные связи (реквизит → объект)."))
        view = QGraphicsView()
        scene = QGraphicsScene()
        view.setScene(scene)
        view.setDragMode(QGraphicsView.ScrollHandDrag)
        layout.addWidget(view)
        self._build(schema, scene)

    def _build(self, schema: Schema, scene: QGraphicsScene):
        objects = [n for n in schema.all_nodes()
                   if n.kind in DATA_BEARING_KINDS and n.selected]
        names = [o.full_name or o.name for o in objects]
        index = {name: i for i, name in enumerate(names)}
        n = max(1, len(objects))
        radius = max(220, n * 12)
        positions = {}
        for i, obj in enumerate(objects):
            ang = 2 * math.pi * i / n
            x = radius * math.cos(ang)
            y = radius * math.sin(ang)
            positions[names[i]] = (x, y)

        # рёбра
        pen = QPen(QColor("#bbbbbb"))
        for obj in objects:
            src = obj.full_name or obj.name
            for field in obj.iter_descendants():
                for desc in field.types:
                    if desc.get("t") in ("Ref", "EnumRef"):
                        tgt = desc.get("meta")
                        if tgt in index and tgt != src:
                            x1, y1 = positions[src]
                            x2, y2 = positions[tgt]
                            line = QGraphicsLineItem(x1, y1, x2, y2)
                            line.setPen(pen)
                            scene.addItem(line)

        # узлы
        for obj in objects:
            name = obj.full_name or obj.name
            x, y = positions[name]
            node = QGraphicsEllipseItem(x - 6, y - 6, 12, 12)
            node.setBrush(QBrush(QColor("#1565c0")))
            node.setToolTip(name)
            scene.addItem(node)
            label = QGraphicsSimpleTextItem(obj.name)
            label.setPos(x + 8, y - 6)
            scene.addItem(label)
