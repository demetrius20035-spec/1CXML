"""Оценка объёмов: ширина строки по схеме и прогноз размера по факту .1cdata."""

from __future__ import annotations

from dataclasses import dataclass

from .datafile import DataSummary
from .model import Schema
from .sqlgen import DDLOptions, Table, build_tables

# Приблизительные размеры значений в байтах по типу.
_TYPE_BYTES = {
    "String": None,   # зависит от len
    "Number": 9,
    "Date": 8,
    "Boolean": 1,
    "UUID": 36,
    "ValueStorage": 64,
    "Ref": 36,
    "EnumRef": 36,
}


def _column_bytes(col) -> int:
    if col.composite:
        return 64
    if not col.descriptors:
        return 16
    d = col.descriptors[0]
    t = d.get("t")
    if t == "String":
        return min(max(d.get("len", 50) or 50, 1), 4000)
    return _TYPE_BYTES.get(t, 16)


def estimate_row_width(table: Table, opts: DDLOptions) -> int:
    width = 0
    for col in table.columns:
        width += _column_bytes(col)
        if (col.is_ref or col.role == "self_ref") and opts.include_presentation:
            width += 60  # колонка представления
    return width


@dataclass
class TableEstimate:
    full_name: str
    columns: int
    row_width: int
    rows: int = 0
    est_bytes: int = 0


def estimate(schema: Schema, opts: DDLOptions | None = None,
             summary: DataSummary | None = None) -> list[TableEstimate]:
    """Оценка по каждой таблице.

    Если передан ``summary`` (из datafile.summarize по факту выгрузки) — берётся
    реальное число строк, иначе только ширина строки (rows=0).
    """
    opts = opts or DDLOptions()
    result = []
    rows_by_obj = summary.objects if summary else {}
    for table in build_tables(schema, opts):
        width = estimate_row_width(table, opts)
        rows = rows_by_obj.get(table.full_name, 0)
        result.append(TableEstimate(
            full_name=table.full_name,
            columns=len(table.columns),
            row_width=width,
            rows=rows,
            est_bytes=width * rows,
        ))
    result.sort(key=lambda e: -e.est_bytes)
    return result


def human_size(num_bytes: int) -> str:
    val = float(num_bytes)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} ПБ"
