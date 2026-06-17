"""Фильтрация дерева и пометки проблемных узлов (бейджи)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import DATA_BEARING_KINDS, FIELD_KINDS, Schema, SchemaNode


def known_object_names(schema: Schema) -> set[str]:
    """Множество полных имён объектов (для поиска «висячих» ссылок)."""
    return {n.full_name for n in schema.all_nodes() if n.full_name}


def dangling_refs(node: SchemaNode, known: set[str]) -> list[str]:
    """Ссылочные типы узла, указывающие на отсутствующий объект."""
    bad = []
    for d in node.types:
        if d.get("t") in ("Ref", "EnumRef"):
            meta = d.get("meta", "")
            if meta and meta not in known:
                bad.append(meta)
    return bad


def node_badges(node: SchemaNode, known: set[str]) -> list[str]:
    """Список «проблем» узла для визуальной индикации."""
    badges = []
    if len(node.types) > 1:
        badges.append("composite")
    if dangling_refs(node, known):
        badges.append("dangling")
    if node.kind in ("Catalog", "Document", "InformationRegister",
                     "AccumulationRegister") and node.selected:
        if node.children and not any(c.is_field and c.selected for c in node.children):
            badges.append("no-fields")
    return badges


@dataclass
class NodeFilter:
    text: str = ""
    kinds: Optional[set] = None
    only_selected: bool = False
    only_composite: bool = False
    only_dangling: bool = False
    only_with_data: bool = False

    def is_active(self) -> bool:
        return bool(self.text or self.kinds or self.only_selected
                    or self.only_composite or self.only_dangling or self.only_with_data)

    def matches(self, node: SchemaNode, known: set[str]) -> bool:
        """Совпадает ли сам узел (без учёта потомков/предков)."""
        if self.kinds and node.kind not in self.kinds:
            return False
        if self.only_selected and not node.selected:
            return False
        if self.only_composite and len(node.types) <= 1:
            return False
        if self.only_dangling and not dangling_refs(node, known):
            return False
        if self.only_with_data and node.kind not in DATA_BEARING_KINDS \
                and node.kind not in FIELD_KINDS:
            return False
        if self.text:
            hay = (node.name + " " + node.synonym + " " + node.full_name).lower()
            if self.text.lower() not in hay:
                return False
        return True
