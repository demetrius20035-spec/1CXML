"""Прикладные операции над схемой: статистика, поиск/замена, высушивание типов."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from .model import DATA_BEARING_KINDS, Schema, SchemaNode, type_descriptor_label


# ---------------------------------------------------------------------------
# Статистика
# ---------------------------------------------------------------------------

@dataclass
class Statistics:
    total_nodes: int = 0
    selected_nodes: int = 0
    objects: int = 0
    composite_fields: int = 0
    by_kind: Counter = None              # type: ignore
    ref_targets: Counter = None          # type: ignore
    primitive_types: Counter = None      # type: ignore

    def __post_init__(self):
        self.by_kind = self.by_kind or Counter()
        self.ref_targets = self.ref_targets or Counter()
        self.primitive_types = self.primitive_types or Counter()


def compute_statistics(schema: Schema) -> Statistics:
    st = Statistics()
    for node in schema.all_nodes():
        st.total_nodes += 1
        if node.selected:
            st.selected_nodes += 1
        st.by_kind[node.kind] += 1
        if node.kind in DATA_BEARING_KINDS:
            st.objects += 1
        types = node.types
        if len(types) > 1:
            st.composite_fields += 1
        for desc in types:
            t = desc.get("t")
            if t in ("Ref", "EnumRef"):
                st.ref_targets[desc.get("meta", "?")] += 1
            else:
                st.primitive_types[t or "?"] += 1
    return st


# ---------------------------------------------------------------------------
# Поиск / замена
# ---------------------------------------------------------------------------

@dataclass
class SearchOptions:
    text: str = ""
    field: str = "name"        # name | synonym | type | fullname | all
    case_sensitive: bool = False
    regex: bool = False
    only_selected: bool = False
    kinds: Optional[set] = None


def _haystacks(node: SchemaNode, field: str) -> list[str]:
    if field == "name":
        return [node.name]
    if field == "synonym":
        return [node.synonym]
    if field == "fullname":
        return [node.full_name]
    if field == "type":
        return [d.get("meta", "") for d in node.types] + [
            d.get("t", "") for d in node.types]
    # all
    return [node.name, node.synonym, node.full_name] + [
        d.get("meta", "") for d in node.types]


def _matcher(opts: SearchOptions):
    if opts.regex:
        flags = 0 if opts.case_sensitive else re.IGNORECASE
        pattern = re.compile(opts.text, flags)
        return lambda s: bool(pattern.search(s))
    needle = opts.text if opts.case_sensitive else opts.text.lower()

    def plain(s: str) -> bool:
        hay = s if opts.case_sensitive else s.lower()
        return needle in hay

    return plain


def find_matches(schema: Schema, opts: SearchOptions) -> list[SchemaNode]:
    if not opts.text:
        return []
    match = _matcher(opts)
    result = []
    for node in schema.all_nodes():
        if opts.only_selected and not node.selected:
            continue
        if opts.kinds and node.kind not in opts.kinds:
            continue
        if any(match(h) for h in _haystacks(node, opts.field) if h):
            result.append(node)
    return result


def replace_in_nodes(nodes: list[SchemaNode], opts: SearchOptions, replacement: str) -> int:
    """Заменяет текст в указанном поле найденных узлов. Возвращает число замен."""
    count = 0
    if opts.regex:
        flags = 0 if opts.case_sensitive else re.IGNORECASE
        pattern = re.compile(opts.text, flags)

        def sub(s: str) -> tuple[str, int]:
            return pattern.subn(replacement, s)
    else:
        def sub(s: str) -> tuple[str, int]:
            if opts.case_sensitive:
                n = s.count(opts.text)
                return s.replace(opts.text, replacement), n
            # case-insensitive plain replace
            pat = re.compile(re.escape(opts.text), re.IGNORECASE)
            return pat.subn(replacement, s)

    for node in nodes:
        if opts.field in ("name", "all"):
            new, n = sub(node.name)
            if n:
                node.name = new
                count += n
        if opts.field in ("synonym", "all"):
            new, n = sub(node.synonym)
            if n:
                node.synonym = new
                count += n
        if opts.field in ("type", "all"):
            for desc in node.types:
                meta = desc.get("meta")
                if meta:
                    new, n = sub(meta)
                    if n:
                        desc["meta"] = new
                        count += n
    return count


# ---------------------------------------------------------------------------
# Высушивание составных типов
# ---------------------------------------------------------------------------

def dryout_keep_subset(node: SchemaNode, keep_indices: list[int]) -> None:
    """Оставляет в узле только выбранные типы (по индексам)."""
    kept = [node.types[i] for i in keep_indices]
    if kept:
        node.types = kept


def dryout_split(schema: Schema, node: SchemaNode, split_indices: list[int]) -> list[SchemaNode]:
    """Разносит составной тип на отдельные узлы (по одному на тип).

    Исходный узел заменяется набором узлов с суффиксами в имени.
    Возвращает список созданных узлов.
    """
    if node.parent is None:
        return []
    parent = node.parent
    row = parent.children.index(node)
    base_name = node.name

    created: list[SchemaNode] = []
    insert_at = row
    for idx in split_indices:
        desc = node.types[idx]
        suffix = _type_suffix(desc)
        data = dict(node.data)
        data.pop("id", None)
        data["name"] = f"{base_name}_{suffix}"
        data["types"] = [dict(desc)]
        data.pop("composite", None)
        new_node = schema.add_child(parent, data, index=insert_at + 1)
        created.append(new_node)
        insert_at += 1

    schema.remove_node(node)
    return created


# ---------------------------------------------------------------------------
# Массовые операции над выделенными узлами
# ---------------------------------------------------------------------------

def bulk_set_selected(nodes: list[SchemaNode], value: bool, cascade: bool = True) -> int:
    """Ставит/снимает галку у узлов (с каскадом на поддерево)."""
    count = 0
    for node in nodes:
        if node.selected != value:
            node.selected = value
            count += 1
        if cascade:
            for desc in node.iter_descendants():
                if desc.selected != value:
                    desc.selected = value
                    count += 1
    return count


def bulk_set_kind(nodes: list[SchemaNode], kind: str) -> int:
    count = 0
    for node in nodes:
        if node.kind != kind:
            node.data["kind"] = kind
            count += 1
    return count


def bulk_delete(schema: Schema, nodes: list[SchemaNode]) -> list[SchemaNode]:
    """Удаляет узлы; если выбран и предок, и потомок — потомок отсекается вместе
    с предком (без двойного удаления). Возвращает фактически удалённые корни."""
    ids = {n.id for n in nodes}

    def has_selected_ancestor(node: SchemaNode) -> bool:
        p = node.parent
        while p is not None:
            if p.id in ids:
                return True
            p = p.parent
        return False

    roots = [n for n in nodes if not has_selected_ancestor(n)]
    for node in roots:
        schema.remove_node(node)
    return roots


def _type_suffix(desc: dict) -> str:
    if desc.get("t") in ("Ref", "EnumRef"):
        meta = desc.get("meta", "")
        return meta.split(".")[-1] if meta else "Ссылка"
    return type_descriptor_label(desc).split("(")[0].replace(" ", "").replace("→", "")
