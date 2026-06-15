"""Экспорт компактного «паспорта конфигурации для LLM».

Берётся только отмеченное (sel) подмножество и сворачивается в компактный
Markdown или JSON, удобный для вставки в контекст нейросети при вайб-кодинге.
"""

from __future__ import annotations

import json

from .model import DATA_BEARING_KINDS, Schema, SchemaNode, types_label


def _selected_objects(schema: Schema) -> list[SchemaNode]:
    return [n for n in schema.all_nodes()
            if n.kind in DATA_BEARING_KINDS and n.selected]


def export_markdown(schema: Schema, only_selected: bool = True) -> str:
    cfg = schema.header.get("config", "") if schema.header else ""
    lines = [f"# Паспорт конфигурации: {cfg}", ""]
    for obj in _selected_objects(schema) if only_selected else \
            [n for n in schema.all_nodes() if n.kind in DATA_BEARING_KINDS]:
        lines.append(f"## {obj.title()}: {obj.full_name or obj.name}")
        if obj.synonym:
            lines.append(f"*{obj.synonym}*")
        lines.append("")
        _emit_fields_md(obj, lines, only_selected)
        lines.append("")
    return "\n".join(lines)


def _emit_fields_md(obj: SchemaNode, lines: list[str], only_selected: bool) -> None:
    fields = [c for c in obj.children if c.is_field and (c.selected or not only_selected)]
    if fields:
        lines.append("| Поле | Тип | Синоним |")
        lines.append("|------|-----|---------|")
        for f in fields:
            lines.append(f"| {f.name} | {types_label(f.types) or '—'} | {f.synonym or ''} |")
    for ts in obj.children:
        if ts.kind in ("TabularSection", "StandardTabularSection") and \
                (ts.selected or not only_selected):
            lines.append("")
            lines.append(f"### ТЧ {ts.name}")
            lines.append("| Поле | Тип |")
            lines.append("|------|-----|")
            for f in ts.children:
                if f.is_field and (f.selected or not only_selected):
                    lines.append(f"| {f.name} | {types_label(f.types) or '—'} |")


def export_json(schema: Schema, only_selected: bool = True) -> str:
    cfg = schema.header.get("config", "") if schema.header else ""
    out = {"config": cfg, "objects": []}
    objs = _selected_objects(schema) if only_selected else \
        [n for n in schema.all_nodes() if n.kind in DATA_BEARING_KINDS]
    for obj in objs:
        item = {
            "kind": obj.kind,
            "name": obj.name,
            "fullName": obj.full_name,
            "synonym": obj.synonym,
            "fields": [],
            "tabularSections": [],
        }
        for c in obj.children:
            if c.is_field and (c.selected or not only_selected):
                item["fields"].append({
                    "name": c.name, "type": types_label(c.types), "synonym": c.synonym})
            elif c.kind in ("TabularSection", "StandardTabularSection") and \
                    (c.selected or not only_selected):
                item["tabularSections"].append({
                    "name": c.name,
                    "fields": [{"name": f.name, "type": types_label(f.types)}
                               for f in c.children
                               if f.is_field and (f.selected or not only_selected)],
                })
        out["objects"].append(item)
    return json.dumps(out, ensure_ascii=False, indent=2)
