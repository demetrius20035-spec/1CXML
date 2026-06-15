"""Генерация DDL (schema.sql) для MariaDB, ClickHouse и YDB по схеме .1cmeta.

Сначала строится диалект-независимая логическая модель таблиц (:class:`Table`),
затем конкретный :class:`Dialect` рендерит её в SQL/YQL.

Соответствие объектов таблицам:
* ссылочный объект (справочник, документ, план…) -> одна таблица; первичный
  ключ — собственная ссылка (колонка ``Ссылка``);
* каждая табличная часть -> отдельная таблица с колонками ``Ссылка`` (ссылка
  владельца) + ``НомерСтроки`` + реквизиты; PK (Ссылка, НомерСтроки);
* регистр -> одна таблица; ключ — период + измерения (если есть), иначе суррогат.

Ссылочное поле раскладывается в две колонки: ``<имя>`` (id, CHAR(36)/UUID/Utf8)
и ``<имя>_view`` (представление) — если включено ``include_presentation``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .model import Schema, SchemaNode
from .translit import sql_identifier, table_name as make_table_name

REFERENCE_KINDS = {
    "Catalog", "Document", "ChartOfCharacteristicTypes", "ChartOfAccounts",
    "ChartOfCalculationTypes", "Task", "BusinessProcess", "ExchangePlan",
}
REGISTER_KINDS = {
    "InformationRegister", "AccumulationRegister", "AccountingRegister",
    "CalculationRegister",
}
FIELD_KINDS = {
    "Attribute", "StandardAttribute", "Dimension", "Resource",
    "AccountingFlag", "ExtDimensionAccountingFlag",
}


@dataclass
class Column:
    source_name: str            # имя поля в 1С
    descriptors: list[dict]     # типы (см. FORMAT.md §5)
    is_ref: bool = False        # одиночный ссылочный тип
    composite: bool = False
    role: str = "scalar"        # scalar | self_ref | line_number | dimension
    nullable: bool = True


@dataclass
class Table:
    full_name: str              # Справочник.Номенклатура / …Товары
    kind: str
    columns: list[Column] = field(default_factory=list)
    pk: list[str] = field(default_factory=list)
    is_register: bool = False


@dataclass
class DDLOptions:
    transliterate: bool = True
    include_presentation: bool = True
    default_string_len: int = 255
    database: str = "onec"
    only_selected: bool = True


# ---------------------------------------------------------------------------
# Построение логической модели
# ---------------------------------------------------------------------------

def _selected(node: SchemaNode, opts: DDLOptions) -> bool:
    return (not opts.only_selected) or node.selected


def _column_from_node(node: SchemaNode, role: str = "scalar") -> Column:
    descs = node.types
    is_ref = len(descs) == 1 and descs[0].get("t") in ("Ref", "EnumRef")
    return Column(
        source_name=node.name,
        descriptors=descs,
        is_ref=is_ref,
        composite=len(descs) > 1,
        role=role,
    )


def build_tables(schema: Schema, opts: DDLOptions) -> list[Table]:
    tables: list[Table] = []
    for node in schema.all_nodes():
        if node.kind in REFERENCE_KINDS and _selected(node, opts):
            tables.append(_build_reference_table(node, opts))
            tables.extend(_build_tabular_tables(node, opts))
        elif node.kind in REGISTER_KINDS and _selected(node, opts):
            tables.append(_build_register_table(node, opts))
    return [t for t in tables if t.columns]


def _build_reference_table(node: SchemaNode, opts: DDLOptions) -> Table:
    table = Table(full_name=node.full_name or node.name, kind=node.kind)
    # собственная ссылка -> PK
    table.columns.append(Column(
        source_name="Ссылка",
        descriptors=[{"t": "UUID"}],
        role="self_ref",
        nullable=False,
    ))
    table.pk = ["Ссылка"]
    for child in node.children:
        if child.kind not in FIELD_KINDS or not _selected(child, opts):
            continue
        if child.name == "Ссылка":
            continue
        table.columns.append(_column_from_node(child))
    return table


def _build_tabular_tables(node: SchemaNode, opts: DDLOptions) -> list[Table]:
    result = []
    for ts in node.children:
        if ts.kind not in ("TabularSection", "StandardTabularSection"):
            continue
        if not _selected(ts, opts):
            continue
        table = Table(
            full_name=(node.full_name or node.name) + "." + ts.name,
            kind="TabularSection",
        )
        table.columns.append(Column("Ссылка", [{"t": "UUID"}], role="self_ref", nullable=False))
        table.columns.append(Column("НомерСтроки", [{"t": "Number", "digits": 9, "fraction": 0}],
                                    role="line_number", nullable=False))
        table.pk = ["Ссылка", "НомерСтроки"]
        for child in ts.children:
            if child.kind in FIELD_KINDS and _selected(child, opts):
                table.columns.append(_column_from_node(child))
        if len(table.columns) > 2:
            result.append(table)
    return result


def _build_register_table(node: SchemaNode, opts: DDLOptions) -> Table:
    table = Table(full_name=node.full_name or node.name, kind=node.kind, is_register=True)
    dims = []
    has_period = False
    for child in node.children:
        if child.kind not in FIELD_KINDS or not _selected(child, opts):
            continue
        role = "dimension" if child.kind == "Dimension" else "scalar"
        if child.name == "Период":
            has_period = True
        col = _column_from_node(child, role=role)
        col.nullable = child.kind not in ("Dimension",)
        table.columns.append(col)
        if child.kind == "Dimension" or child.name == "Период":
            dims.append(child.name)
    # ключ регистра: период + измерения (если есть)
    table.pk = dims
    return table


# ---------------------------------------------------------------------------
# Диалекты
# ---------------------------------------------------------------------------

class Dialect:
    name = "generic"

    def __init__(self, opts: DDLOptions):
        self.opts = opts

    def ident(self, name: str) -> str:
        return sql_identifier(name, self.opts.transliterate)

    def quote(self, ident: str) -> str:
        return f'"{ident}"'

    def table_name(self, full_name: str) -> str:
        return self.ident(make_table_name(full_name, self.opts.transliterate))

    # тип одной (не составной) колонки
    def map_scalar(self, desc: dict, nullable: bool) -> str:
        raise NotImplementedError

    def map_column_type(self, col: Column, nullable: bool) -> str:
        if col.role in ("self_ref",):
            return self.ref_id_type(nullable=False)
        if col.role == "line_number":
            return self.int_type(nullable=False)
        if col.composite:
            return self.json_type(nullable)
        if col.is_ref:
            return self.ref_id_type(nullable)
        if col.descriptors:
            return self.map_scalar(col.descriptors[0], nullable)
        return self.text_type(nullable)

    # примитивы (переопределяются)
    def ref_id_type(self, nullable: bool) -> str: ...
    def int_type(self, nullable: bool) -> str: ...
    def json_type(self, nullable: bool) -> str: ...
    def text_type(self, nullable: bool) -> str: ...
    def presentation_type(self) -> str: ...

    def render_script(self, tables: list[Table]) -> str:
        raise NotImplementedError

    # общий рендер списка колонок (id + view для ссылок)
    def _column_lines(self, table: Table) -> list[str]:
        lines = []
        for col in table.columns:
            cname = self.ident(col.source_name)
            ctype = self.map_column_type(col, col.nullable)
            lines.append(f"  {self.quote(cname)} {ctype}")
            if (col.is_ref or col.role == "self_ref") and self.opts.include_presentation \
                    and col.role != "line_number":
                vname = self.ident(col.source_name + "_view")
                lines.append(f"  {self.quote(vname)} {self.presentation_type()}")
        return lines


class MariaDBDialect(Dialect):
    name = "mariadb"

    def quote(self, ident: str) -> str:
        return f"`{ident}`"

    def ref_id_type(self, nullable):
        return "CHAR(36)" + ("" if nullable else " NOT NULL")

    def int_type(self, nullable):
        return "INT" + ("" if nullable else " NOT NULL")

    def json_type(self, nullable):
        return "JSON" + ("" if nullable else " NOT NULL")

    def text_type(self, nullable):
        return "TEXT" + ("" if nullable else " NOT NULL")

    def presentation_type(self):
        return "VARCHAR(500)"

    def map_scalar(self, desc, nullable):
        t = desc.get("t")
        suffix = "" if nullable else " NOT NULL"
        if t == "String":
            ln = desc.get("len", 0)
            base = f"VARCHAR({ln})" if 0 < ln <= 16383 else "TEXT"
            return base + suffix
        if t == "Number":
            d = max(1, desc.get("digits", 18))
            f = desc.get("fraction", 0)
            return f"DECIMAL({d},{f})" + suffix
        if t == "Date":
            return "DATETIME" + suffix
        if t == "Boolean":
            return "TINYINT(1)" + suffix
        if t == "UUID":
            return "CHAR(36)" + suffix
        if t == "ValueStorage":
            return "LONGBLOB" + suffix
        return "TEXT" + suffix

    def render_script(self, tables):
        out = [f"CREATE DATABASE IF NOT EXISTS `{self.opts.database}` "
               f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;",
               f"USE `{self.opts.database}`;", ""]
        for t in tables:
            tn = self.table_name(t.full_name)
            out.append(f"-- {t.full_name}")
            out.append(f"CREATE TABLE IF NOT EXISTS `{tn}` (")
            lines = self._column_lines(t)
            if t.pk:
                pk = ", ".join(self.quote(self.ident(c)) for c in t.pk)
                lines.append(f"  PRIMARY KEY ({pk})")
            out.append(",\n".join(lines))
            out.append(") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
            out.append("")
        return "\n".join(out)


class ClickHouseDialect(Dialect):
    name = "clickhouse"

    def _wrap_null(self, base, nullable):
        return f"Nullable({base})" if nullable else base

    def ref_id_type(self, nullable):
        return self._wrap_null("UUID", nullable)

    def int_type(self, nullable):
        return self._wrap_null("Int64", nullable)

    def json_type(self, nullable):
        return self._wrap_null("String", nullable)

    def text_type(self, nullable):
        return self._wrap_null("String", nullable)

    def presentation_type(self):
        return "Nullable(String)"

    def map_scalar(self, desc, nullable):
        t = desc.get("t")
        if t == "String":
            return self._wrap_null("String", nullable)
        if t == "Number":
            d = max(1, min(76, desc.get("digits", 18)))
            f = desc.get("fraction", 0)
            return self._wrap_null(f"Decimal({d},{f})", nullable)
        if t == "Date":
            return self._wrap_null("DateTime64(3)", nullable)
        if t == "Boolean":
            return self._wrap_null("UInt8", nullable)
        if t == "UUID":
            return self._wrap_null("UUID", nullable)
        if t == "ValueStorage":
            return self._wrap_null("String", nullable)
        return self._wrap_null("String", nullable)

    def render_script(self, tables):
        out = [f"CREATE DATABASE IF NOT EXISTS {self.ident(self.opts.database)};", ""]
        db = self.ident(self.opts.database)
        for t in tables:
            tn = self.table_name(t.full_name)
            out.append(f"-- {t.full_name}")
            out.append(f"CREATE TABLE IF NOT EXISTS {db}.{tn} (")
            out.append(",\n".join(self._column_lines(t)))
            order = t.pk if t.pk else []
            order_cols = ", ".join(self.quote(self.ident(c)) for c in order) if order else "tuple()"
            out.append(f") ENGINE = MergeTree ORDER BY ({order_cols});"
                       if order else f") ENGINE = MergeTree ORDER BY tuple();")
            out.append("")
        return "\n".join(out)


class YDBDialect(Dialect):
    name = "ydb"

    def ref_id_type(self, nullable):
        return "Utf8"

    def int_type(self, nullable):
        return "Int64"

    def json_type(self, nullable):
        return "Json"

    def text_type(self, nullable):
        return "Utf8"

    def presentation_type(self):
        return "Utf8"

    def map_scalar(self, desc, nullable):
        t = desc.get("t")
        if t == "String":
            return "Utf8"
        if t == "Number":
            f = desc.get("fraction", 0)
            return "Decimal(22,9)" if f > 0 else "Int64"
        if t == "Date":
            return "Timestamp"
        if t == "Boolean":
            return "Bool"
        if t == "UUID":
            return "Utf8"
        if t == "ValueStorage":
            return "String"
        return "Utf8"

    def render_script(self, tables):
        out = []
        for t in tables:
            tn = self.table_name(t.full_name)
            out.append(f"-- {t.full_name}")
            out.append(f"CREATE TABLE {tn} (")
            lines = self._column_lines(t)
            # YDB требует PRIMARY KEY; при отсутствии — суррогат _id
            pk = list(t.pk)
            if not pk:
                lines.insert(0, '  "_id" Utf8 NOT NULL')
                pk = ["_id"]
            pk_cols = ", ".join(self.quote(self.ident(c)) for c in pk)
            lines.append(f"  PRIMARY KEY ({pk_cols})")
            out.append(",\n".join(lines))
            out.append(");")
            out.append("")
        return "\n".join(out)


DIALECTS = {
    "mariadb": MariaDBDialect,
    "clickhouse": ClickHouseDialect,
    "ydb": YDBDialect,
}


def generate_ddl(schema: Schema, dialect: str, opts: Optional[DDLOptions] = None) -> str:
    opts = opts or DDLOptions()
    tables = build_tables(schema, opts)
    return DIALECTS[dialect](opts).render_script(tables)
