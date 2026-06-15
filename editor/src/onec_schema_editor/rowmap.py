"""Сопоставление строк .1cdata колонкам таблиц СУБД (общее для всех бэкендов)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .datafile import ref_presentation, ref_value
from .model import Schema
from .sqlgen import DDLOptions, Table, build_tables
from .translit import sql_identifier, table_name as make_table_name


@dataclass
class TableMapper:
    full_name: str
    sql_table: str
    columns: list[str]            # имена SQL-колонок в порядке вставки
    _plan: list[tuple]            # (sql_col, source_key, mode)

    def extract(self, data: dict) -> list:
        row = []
        for sql_col, source_key, mode in self._plan:
            val = data.get(source_key)
            if mode == "ref_id":
                row.append(ref_value(val))
            elif mode == "ref_view":
                row.append(ref_presentation(val))
            elif mode == "composite":
                row.append(json.dumps(val, ensure_ascii=False) if val is not None else None)
            else:
                row.append(val)
        return row


def build_mappers(schema: Schema, opts: DDLOptions) -> dict[str, TableMapper]:
    mappers: dict[str, TableMapper] = {}
    for table in build_tables(schema, opts):
        mappers[table.full_name] = _mapper_for_table(table, opts)
    return mappers


def _mapper_for_table(table: Table, opts: DDLOptions) -> TableMapper:
    sql_table = sql_identifier(make_table_name(table.full_name, opts.transliterate),
                               opts.transliterate)
    columns: list[str] = []
    plan: list[tuple] = []
    for col in table.columns:
        cname = sql_identifier(col.source_name, opts.transliterate)
        is_ref_like = col.is_ref or col.role == "self_ref"
        if is_ref_like and col.role != "line_number":
            columns.append(cname)
            plan.append((cname, col.source_name, "ref_id"))
            if opts.include_presentation:
                vname = sql_identifier(col.source_name + "_view", opts.transliterate)
                columns.append(vname)
                plan.append((vname, col.source_name, "ref_view"))
        elif col.composite:
            columns.append(cname)
            plan.append((cname, col.source_name, "composite"))
        else:
            columns.append(cname)
            plan.append((cname, col.source_name, "scalar"))
    return TableMapper(table.full_name, sql_table, columns, plan)
