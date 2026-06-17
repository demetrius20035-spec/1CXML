"""Сверка схемы с реальной БД и обратная инженерия каркаса из таблиц.

Чистая логика сравнения (expected vs actual) тестируется без БД. Интроспекция
живой БД (ClickHouse/MariaDB) использует опциональные драйверы.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Schema
from .sqlgen import DDLOptions, build_tables
from .translit import sql_identifier, table_name


# Семейство типа по дескриптору 1С.
def _family(descriptors: list[dict]) -> str:
    if not descriptors or len(descriptors) > 1:
        return "string"
    t = descriptors[0].get("t")
    if t == "Number":
        return "number"
    if t == "Date":
        return "date"
    if t == "Boolean":
        return "bool"
    if t in ("Ref", "EnumRef", "UUID"):
        return "uuid"
    return "string"


def expected_columns(schema: Schema, opts: DDLOptions | None = None) -> dict[str, dict[str, str]]:
    """Ожидаемые таблицы → {колонка: семейство типа} по DDL-логике редактора."""
    opts = opts or DDLOptions()
    result: dict[str, dict[str, str]] = {}
    for table in build_tables(schema, opts):
        tname = sql_identifier(table_name(table.full_name, opts.transliterate), opts.transliterate)
        cols: dict[str, str] = {}
        for col in table.columns:
            cname = sql_identifier(col.source_name, opts.transliterate)
            if col.role == "self_ref" or col.is_ref:
                cols[cname] = "uuid"
                if opts.include_presentation and col.role != "line_number":
                    cols[sql_identifier(col.source_name + "_view", opts.transliterate)] = "string"
            elif col.composite:
                cols[cname] = "string"
            elif col.role == "line_number":
                cols[cname] = "number"
            else:
                cols[cname] = _family(col.descriptors)
        result[tname] = cols
    return result


def _actual_family(sql_type: str) -> str:
    t = sql_type.lower()
    # снять обёртки ClickHouse
    for w in ("nullable(", "lowcardinality("):
        if t.startswith(w):
            t = t[len(w):].rstrip(")")
    if "uuid" in t:
        return "uuid"
    if any(k in t for k in ("char", "text", "string", "json", "blob", "binary")):
        return "string"
    if any(k in t for k in ("int", "decimal", "numeric", "float", "double", "real")):
        # tinyint(1) часто булево — оставим число, bool совместим ниже
        return "number"
    if any(k in t for k in ("date", "time")):
        return "date"
    if "bool" in t:
        return "bool"
    return "other"


def _family_compatible(expected: str, actual_type: str) -> bool:
    actual = _actual_family(actual_type)
    if actual == "string":
        return True  # текст хранит что угодно
    if expected == "uuid":
        return actual in ("uuid", "string")
    if expected == "number":
        return actual in ("number", "bool")
    if expected == "bool":
        return actual in ("bool", "number")
    if expected == "date":
        return actual == "date"
    if expected == "string":
        return actual == "string"
    return True


@dataclass
class CompareReport:
    missing_tables: list[str] = field(default_factory=list)
    missing_columns: list[tuple[str, str]] = field(default_factory=list)
    type_mismatches: list[tuple[str, str, str, str]] = field(default_factory=list)
    extra_tables: list[str] = field(default_factory=list)

    def ok(self) -> bool:
        return not (self.missing_tables or self.missing_columns)

    def summary(self) -> str:
        return (f"Нет таблиц: {len(self.missing_tables)}  |  "
                f"Нет колонок: {len(self.missing_columns)}  |  "
                f"Несовпадение типов: {len(self.type_mismatches)}  |  "
                f"Лишних таблиц: {len(self.extra_tables)}")


def compare(schema: Schema, actual: dict[str, dict[str, str]],
            opts: DDLOptions | None = None, check_types: bool = True) -> CompareReport:
    """Сравнивает ожидаемую структуру со снимком реальной БД ``actual``
    (таблица → {колонка: тип-строка})."""
    expected = expected_columns(schema, opts)
    report = CompareReport()
    actual_lower = {k.lower(): v for k, v in actual.items()}

    for table, cols in expected.items():
        act_cols = actual.get(table) or actual_lower.get(table.lower())
        if act_cols is None:
            report.missing_tables.append(table)
            continue
        act_lower = {c.lower(): t for c, t in act_cols.items()}
        for col, family in cols.items():
            act_type = act_cols.get(col) or act_lower.get(col.lower())
            if act_type is None:
                report.missing_columns.append((table, col))
            elif check_types and not _family_compatible(family, act_type):
                report.type_mismatches.append((table, col, family, act_type))

    exp_lower = {t.lower() for t in expected}
    for table in actual:
        if table.lower() not in exp_lower:
            report.extra_tables.append(table)
    return report


# ---------------------------------------------------------------- интроспекция

def introspect_clickhouse(config) -> dict[str, dict[str, str]]:
    from .backends.clickhouse_backend import ClickHouseConnection
    conn = ClickHouseConnection(config)
    try:
        rows = conn._client.query(
            "SELECT table, name, type FROM system.columns WHERE database = %(db)s",
            parameters={"db": config.database}).result_rows
    finally:
        conn.close()
    result: dict[str, dict[str, str]] = {}
    for table, name, ctype in rows:
        result.setdefault(table, {})[name] = ctype
    return result


def introspect_mariadb(config) -> dict[str, dict[str, str]]:
    from .backends.mariadb_backend import MariaDBConnection
    conn = MariaDBConnection(config)
    try:
        cur = conn._conn.cursor()
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s", (config.database,))
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    result: dict[str, dict[str, str]] = {}
    for table, name, ctype in rows:
        result.setdefault(table, {})[name] = ctype
    return result


# ------------------------------------------------------- обратная инженерия

def schema_from_db_tables(actual: dict[str, dict[str, str]],
                          config_name: str = "ImportedFromDB") -> Schema:
    """Строит каркас .1cmeta из снимка БД (по одному объекту на таблицу).

    Семантика 1С из SQL не восстановима, поэтому объекты получают вид Catalog,
    а колонки — тип, угаданный по SQL-типу (для дальнейшей ручной правки)."""
    from .model import SchemaNode

    header = {"$": "header", "format": "1cmeta-ndjson", "version": 1,
              "config": config_name, "source": "reverse-engineered from DB"}
    schema = Schema(header=header)
    root = SchemaNode({"id": 1, "parent": 0, "kind": "ConfigRoot",
                       "name": config_name, "sel": True})
    schema.nodes_by_id[1] = root
    schema.roots.append(root)
    cls = SchemaNode({"id": 2, "parent": 1, "kind": "MetadataClass",
                      "name": "Таблицы БД", "sel": True})
    cls.parent = root
    root.children.append(cls)
    schema.nodes_by_id[2] = cls
    schema.max_id = 2

    for table, cols in sorted(actual.items()):
        obj = schema.add_child(cls, {"kind": "Catalog", "name": table,
                                     "fullName": "Справочник." + table, "sel": True})
        for col, sql_type in cols.items():
            obj_child = schema.add_child(obj, {
                "kind": "Attribute", "name": col, "sel": True,
                "types": [_descriptor_from_sql(sql_type)],
            })
            _ = obj_child
    return schema


def _descriptor_from_sql(sql_type: str) -> dict:
    fam = _actual_family(sql_type)
    if fam == "number":
        return {"t": "Number", "digits": 18, "fraction": 0}
    if fam == "date":
        return {"t": "Date", "dateParts": "DateTime"}
    if fam == "bool":
        return {"t": "Boolean"}
    if fam == "uuid":
        return {"t": "UUID"}
    return {"t": "String", "len": 0}
