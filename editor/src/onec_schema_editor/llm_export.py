"""Экспорт схемы в форматы для вайб-кодинга/LLM: JSON Schema, Pydantic,
SQLAlchemy, dbt schema.yml, а также грубая оценка размера в токенах."""

from __future__ import annotations

import json

from .model import DATA_BEARING_KINDS, Schema, SchemaNode
from .translit import sql_identifier, table_name


def _objects(schema: Schema, only_selected: bool) -> list[SchemaNode]:
    return [n for n in schema.all_nodes()
            if n.kind in DATA_BEARING_KINDS and (n.selected or not only_selected)]


def _fields(obj: SchemaNode, only_selected: bool) -> list[SchemaNode]:
    return [c for c in obj.children if c.is_field and (c.selected or not only_selected)]


def _tabular(obj: SchemaNode, only_selected: bool) -> list[SchemaNode]:
    return [c for c in obj.children
            if c.kind in ("TabularSection", "StandardTabularSection")
            and (c.selected or not only_selected)]


# ---------------------------------------------------------------- JSON Schema

def json_type(types: list[dict]) -> dict:
    if not types:
        return {"type": "string"}
    if len(types) > 1:
        return {"type": "string", "description": "составной тип (JSON)"}
    d = types[0]
    t = d.get("t")
    if t == "String":
        return {"type": "string"}
    if t == "Number":
        return {"type": "number"} if d.get("fraction") else {"type": "integer"}
    if t == "Date":
        return {"type": "string", "format": "date-time"}
    if t == "Boolean":
        return {"type": "boolean"}
    if t == "UUID":
        return {"type": "string", "format": "uuid"}
    if t in ("Ref", "EnumRef"):
        return {"type": "string", "format": "uuid", "description": "→ " + d.get("meta", "")}
    return {"type": "string"}


def to_json_schema(schema: Schema, only_selected: bool = True) -> str:
    cfg = schema.header.get("config", "") if schema.header else ""
    defs: dict = {}
    for obj in _objects(schema, only_selected):
        def_name = sql_identifier(table_name(obj.full_name or obj.name))
        props: dict = {}
        for f in _fields(obj, only_selected):
            schema_field = json_type(f.types)
            if f.synonym:
                schema_field = dict(schema_field)
                schema_field.setdefault("title", f.synonym)
            props[f.name] = schema_field
        for ts in _tabular(obj, only_selected):
            item_props = {tf.name: json_type(tf.types)
                          for tf in _fields(ts, only_selected)}
            props[ts.name] = {"type": "array",
                              "items": {"type": "object", "properties": item_props}}
        defs[def_name] = {
            "type": "object",
            "title": obj.synonym or obj.name,
            "x-fullName": obj.full_name,
            "properties": props,
        }
    doc = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": cfg,
        "$defs": defs,
    }
    return json.dumps(doc, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------ Pydantic

_PY_SCALAR = {
    "String": "str", "Number": "float", "Date": "datetime", "Boolean": "bool",
    "UUID": "str", "ValueStorage": "bytes | None",
}


def py_type(types: list[dict]) -> str:
    if not types:
        return "str | None"
    if len(types) > 1:
        return "str | None"  # составной — JSON-строка
    d = types[0]
    t = d.get("t")
    if t == "Number":
        return "int | None" if not d.get("fraction") else "float | None"
    if t in ("Ref", "EnumRef", "UUID"):
        return "str | None"
    return _PY_SCALAR.get(t, "str") + " | None"


def to_pydantic(schema: Schema, only_selected: bool = True) -> str:
    lines = ["from __future__ import annotations", "",
             "from datetime import datetime", "from pydantic import BaseModel, Field", "", ""]
    # сначала классы ТЧ, затем объекты
    ts_classes: list[str] = []
    obj_classes: list[str] = []
    for obj in _objects(schema, only_selected):
        cls = _class_name(obj.full_name or obj.name)
        body = []
        for ts in _tabular(obj, only_selected):
            ts_cls = cls + "_" + sql_identifier(ts.name)
            ts_body = [f"class {ts_cls}(BaseModel):"]
            tfields = _fields(ts, only_selected)
            if not tfields:
                ts_body.append("    pass")
            for tf in tfields:
                ts_body.append(_py_field_line(tf))
            ts_classes.append("\n".join(ts_body))
        obj_body = [f"class {cls}(BaseModel):",
                    f'    """{obj.synonym or obj.name} ({obj.full_name})"""']
        for f in _fields(obj, only_selected):
            obj_body.append(_py_field_line(f))
        for ts in _tabular(obj, only_selected):
            ts_cls = cls + "_" + sql_identifier(ts.name)
            obj_body.append(f"    {sql_identifier(ts.name)}: list[{ts_cls}] = Field(default_factory=list)")
        obj_classes.append("\n".join(obj_body))
    return "\n".join(lines + ts_classes + ["", ""] + obj_classes) + "\n"


def _py_field_line(node: SchemaNode) -> str:
    name = sql_identifier(node.name)
    descr = node.synonym or node.name
    return f'    {name}: {py_type(node.types)} = Field(None, description="{descr}")'


def _class_name(full_name: str) -> str:
    return sql_identifier(table_name(full_name))


# ---------------------------------------------------------------- SQLAlchemy

_SA_SCALAR = {
    "String": "String", "Date": "DateTime", "Boolean": "Boolean",
    "UUID": "String(36)", "ValueStorage": "LargeBinary",
}


def sa_type(types: list[dict]) -> str:
    if not types or len(types) > 1:
        return "Text"
    d = types[0]
    t = d.get("t")
    if t == "Number":
        if d.get("fraction"):
            return f"Numeric({d.get('digits', 18)}, {d.get('fraction', 0)})"
        return "BigInteger"
    if t in ("Ref", "EnumRef"):
        return "String(36)"
    if t == "String":
        ln = d.get("len", 0)
        return f"String({ln})" if ln else "Text"
    return _SA_SCALAR.get(t, "Text")


def to_sqlalchemy(schema: Schema, only_selected: bool = True) -> str:
    lines = ["from sqlalchemy import (BigInteger, Boolean, Column, DateTime, "
             "LargeBinary, Numeric, String, Text)",
             "from sqlalchemy.orm import declarative_base", "",
             "Base = declarative_base()", "", ""]
    for obj in _objects(schema, only_selected):
        cls = _class_name(obj.full_name or obj.name)
        tbl = table_name(obj.full_name or obj.name)
        ref_col = sql_identifier("Ссылка")
        body = [f"class {cls}(Base):",
                f'    __tablename__ = "{tbl}"',
                f'    {ref_col} = Column(String(36), primary_key=True)']
        for f in _fields(obj, only_selected):
            if f.name == "Ссылка":
                continue
            col = sql_identifier(f.name)
            body.append(f'    {col} = Column({sa_type(f.types)})')
        lines.append("\n".join(body))
        lines.append("")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- dbt

def to_dbt_yaml(schema: Schema, only_selected: bool = True) -> str:
    out = ["version: 2", "", "models:"]
    for obj in _objects(schema, only_selected):
        out.append(f"  - name: {table_name(obj.full_name or obj.name)}")
        if obj.synonym:
            out.append(f"    description: \"{obj.synonym}\"")
        out.append("    columns:")
        out.append(f"      - name: {sql_identifier('Ссылка')}")
        out.append("        description: \"Ссылка\"")
        for f in _fields(obj, only_selected):
            if f.name == "Ссылка":
                continue
            out.append(f"      - name: {sql_identifier(f.name)}")
            desc = (f.synonym or f.name).replace('"', "'")
            out.append(f"        description: \"{desc}\"")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------- оценка токенов

def estimate_tokens(text: str) -> int:
    """Грубая оценка количества токенов (~4 символа на токен)."""
    return max(1, len(text) // 4)
