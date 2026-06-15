"""Модель данных схемы .1cmeta (NDJSON).

Потоковая загрузка/сохранение: файл читается и пишется построчно, что позволяет
работать с гигантскими выгрузками без полной материализации JSON в памяти
(в память попадают только сами узлы как лёгкие объекты).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable, Iterator, Optional


# Узлы-носители данных (для статистики/фильтров «только данные»).
DATA_BEARING_KINDS = {
    "Constant", "Catalog", "Document", "DocumentJournal",
    "ChartOfCharacteristicTypes", "ChartOfAccounts", "ChartOfCalculationTypes",
    "InformationRegister", "AccumulationRegister", "AccountingRegister",
    "CalculationRegister", "BusinessProcess", "Task", "ExchangePlan",
}

FIELD_KINDS = {
    "Attribute", "StandardAttribute", "Dimension", "Resource",
    "AccountingFlag", "ExtDimensionAccountingFlag", "EnumValue",
}

# Человекочитаемые названия видов.
KIND_TITLES = {
    "ConfigRoot": "Конфигурация",
    "MetadataClass": "Класс объектов",
    "Constant": "Константа",
    "Catalog": "Справочник",
    "Document": "Документ",
    "DocumentJournal": "Журнал документов",
    "Enum": "Перечисление",
    "ChartOfCharacteristicTypes": "План видов характеристик",
    "ChartOfAccounts": "План счетов",
    "ChartOfCalculationTypes": "План видов расчёта",
    "InformationRegister": "Регистр сведений",
    "AccumulationRegister": "Регистр накопления",
    "AccountingRegister": "Регистр бухгалтерии",
    "CalculationRegister": "Регистр расчёта",
    "BusinessProcess": "Бизнес-процесс",
    "Task": "Задача",
    "ExchangePlan": "План обмена",
    "Report": "Отчёт",
    "DataProcessor": "Обработка",
    "StandardAttribute": "Стандартный реквизит",
    "Attribute": "Реквизит",
    "TabularSection": "Табличная часть",
    "StandardTabularSection": "Стандартная ТЧ",
    "Dimension": "Измерение",
    "Resource": "Ресурс",
    "EnumValue": "Значение перечисления",
    "AccountingFlag": "Признак учёта",
    "ExtDimensionAccountingFlag": "Признак учёта субконто",
    "Recalculation": "Перерасчёт",
    "Command": "Команда",
}


class SchemaNode:
    """Узел дерева метаданных.

    Хранит «сырые» поля JSON в ``data`` и поддерживает связи parent/children.
    """

    __slots__ = ("id", "parent_id", "data", "children", "parent")

    def __init__(self, data: dict):
        self.id: int = data.get("id", 0)
        self.parent_id: int = data.get("parent", 0)
        self.data: dict = data
        self.children: list["SchemaNode"] = []
        self.parent: Optional["SchemaNode"] = None

    # --- удобные свойства над сырыми данными ---
    @property
    def kind(self) -> str:
        return self.data.get("kind", "")

    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @name.setter
    def name(self, value: str) -> None:
        self.data["name"] = value

    @property
    def synonym(self) -> str:
        return self.data.get("synonym", "")

    @synonym.setter
    def synonym(self, value: str) -> None:
        self.data["synonym"] = value

    @property
    def full_name(self) -> str:
        return self.data.get("fullName", "")

    @property
    def comment(self) -> str:
        return self.data.get("comment", "")

    @property
    def selected(self) -> bool:
        return self.data.get("sel", True)

    @selected.setter
    def selected(self, value: bool) -> None:
        self.data["sel"] = bool(value)

    @property
    def types(self) -> list[dict]:
        return self.data.get("types", [])

    @types.setter
    def types(self, value: list[dict]) -> None:
        if value:
            self.data["types"] = value
            self.data["composite"] = len(value) > 1
            if not self.data["composite"]:
                self.data.pop("composite", None)
        else:
            self.data.pop("types", None)
            self.data.pop("composite", None)

    @property
    def is_composite(self) -> bool:
        return len(self.types) > 1

    @property
    def is_field(self) -> bool:
        return self.kind in FIELD_KINDS

    def title(self) -> str:
        return KIND_TITLES.get(self.kind, self.kind)

    def iter_descendants(self) -> Iterator["SchemaNode"]:
        for child in self.children:
            yield child
            yield from child.iter_descendants()


@dataclass
class Schema:
    """Контейнер всей схемы."""

    header: dict = field(default_factory=dict)
    footer: dict = field(default_factory=dict)
    roots: list[SchemaNode] = field(default_factory=list)
    nodes_by_id: dict[int, SchemaNode] = field(default_factory=dict)
    source_path: Optional[str] = None
    max_id: int = 0

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str, progress=None) -> "Schema":
        """Потоковая загрузка NDJSON.

        ``progress`` — необязательный callable(bytes_read, bytes_total).
        """
        schema = cls(source_path=path)
        import os

        total = os.path.getsize(path)
        read = 0
        pending: list[SchemaNode] = []

        with open(path, "r", encoding="utf-8-sig") as fh:
            for line in fh:
                read += len(line.encode("utf-8"))
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rtype = rec.get("$")
                if rtype == "header":
                    schema.header = rec
                elif rtype == "footer":
                    schema.footer = rec
                elif rtype == "node":
                    node = SchemaNode(rec)
                    schema.nodes_by_id[node.id] = node
                    schema.max_id = max(schema.max_id, node.id)
                    pending.append(node)
                if progress and read % (1 << 20) < 4096:
                    progress(read, total)

        # связываем дерево, сохраняя порядок появления
        for node in pending:
            parent = schema.nodes_by_id.get(node.parent_id)
            if parent is not None:
                parent.children.append(node)
                node.parent = parent
            else:
                schema.roots.append(node)
        if progress:
            progress(total, total)
        return schema

    # ------------------------------------------------------------------ save
    def save(self, path: str) -> None:
        """Потоковая запись NDJSON (DFS в порядке дерева)."""
        nodes_written = 0
        with open(path, "w", encoding="utf-8") as fh:
            header = dict(self.header) if self.header else {
                "$": "header", "format": "1cmeta-ndjson", "version": 1,
            }
            header["$"] = "header"
            fh.write(json.dumps(header, ensure_ascii=False) + "\n")

            stack = list(reversed(self.roots))
            while stack:
                node = stack.pop()
                node.data["$"] = "node"
                node.data["id"] = node.id
                node.data["parent"] = node.parent_id
                fh.write(json.dumps(node.data, ensure_ascii=False) + "\n")
                nodes_written += 1
                for child in reversed(node.children):
                    stack.append(child)

            footer = {"$": "footer", "nodes": nodes_written}
            fh.write(json.dumps(footer, ensure_ascii=False) + "\n")
        self.source_path = path

    # --------------------------------------------------------------- editing
    def new_id(self) -> int:
        self.max_id += 1
        return self.max_id

    def all_nodes(self) -> Iterator[SchemaNode]:
        for root in self.roots:
            yield root
            yield from root.iter_descendants()

    def remove_node(self, node: SchemaNode) -> None:
        """Удаляет узел вместе с поддеревом."""
        for desc in list(node.iter_descendants()):
            self.nodes_by_id.pop(desc.id, None)
        self.nodes_by_id.pop(node.id, None)
        if node.parent is not None:
            node.parent.children.remove(node)
        elif node in self.roots:
            self.roots.remove(node)

    def add_child(self, parent: SchemaNode, data: dict, index: Optional[int] = None) -> SchemaNode:
        data.setdefault("id", self.new_id())
        data["parent"] = parent.id
        node = SchemaNode(data)
        node.parent = parent
        self.nodes_by_id[node.id] = node
        if index is None:
            parent.children.append(node)
        else:
            parent.children.insert(index, node)
        return node


# ---------------------------------------------------------------------------
# Отображение типа в человекочитаемую строку.
# ---------------------------------------------------------------------------

def type_descriptor_label(desc: dict) -> str:
    t = desc.get("t", "?")
    if t == "String":
        ln = desc.get("len", 0)
        return f"Строка({ln})" if ln else "Строка"
    if t == "Number":
        return f"Число({desc.get('digits', 0)},{desc.get('fraction', 0)})"
    if t == "Date":
        return {"Date": "Дата", "Time": "Время", "DateTime": "Дата+Время"}.get(
            desc.get("dateParts", "DateTime"), "Дата")
    if t == "Boolean":
        return "Булево"
    if t == "UUID":
        return "УникальныйИдентификатор"
    if t == "ValueStorage":
        return "ХранилищеЗначения"
    if t == "Type":
        return "Тип"
    if t in ("Ref", "EnumRef"):
        return "→ " + desc.get("meta", "?")
    if t == "AnyRef":
        return "ЛюбаяСсылка"
    if t == "DefinedType":
        return "ОпределяемыйТип." + desc.get("name", "?")
    if t == "Unknown":
        return desc.get("raw", "Неизвестный")
    return t


def types_label(types: list[dict]) -> str:
    if not types:
        return ""
    labels = [type_descriptor_label(d) for d in types]
    if len(labels) == 1:
        return labels[0]
    return "Составной: " + ", ".join(labels)
