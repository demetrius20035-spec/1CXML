"""Тесты новых возможностей: импорт XML, DDL, маппинг данных, профили, diff,
валидатор, паспорт, оценка, транслитерация. Без GUI."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onec_schema_editor.model import Schema  # noqa: E402
from onec_schema_editor import (  # noqa: E402
    diff, estimate, exporters, importers, profiles, sqlgen, validate,
)
from onec_schema_editor.datafile import iter_rows, summarize  # noqa: E402
from onec_schema_editor.rowmap import build_mappers  # noqa: E402
from onec_schema_editor.translit import sql_identifier, table_name  # noqa: E402

HERE = os.path.dirname(__file__)
EX = os.path.join(HERE, "..", "..", "examples")
SAMPLE = os.path.join(EX, "sample.1cmeta")
XMLDUMP = os.path.join(EX, "xmldump")
SAMPLE_DATA = os.path.join(EX, "sample.1cdata")


def load_sample():
    return Schema.load(SAMPLE)


# ---------------------------------------------------------------- транслит
def test_translit():
    assert table_name("Справочник.Номенклатура") == "Spravochnik_Nomenklatura"
    assert sql_identifier("Производитель") == "Proizvoditel"
    assert sql_identifier("Код", transliterate=False) == "Код"


# ------------------------------------------------------------------ импорт
def test_import_configuration_xml():
    schema = importers.import_configuration(XMLDUMP)
    assert schema.header["config"] == "ДемоКонфигурация"
    by_full = {n.full_name: n for n in schema.all_nodes() if n.full_name}
    assert "Справочник.Номенклатура" in by_full
    assert "Документ.РеализацияТоваровУслуг" in by_full
    assert "РегистрНакопления.ТоварыНаСкладах" in by_full

    nom = by_full["Справочник.Номенклатура"]
    names = {c.name for c in nom.children}
    # синтезированные стандартные реквизиты
    assert {"Ссылка", "Код", "Наименование", "ПометкаУдаления", "Родитель", "ЭтоГруппа"} <= names
    # составной тип Производитель
    prod = next(c for c in nom.children if c.name == "Производитель")
    assert prod.is_composite
    metas = {t.get("meta") for t in prod.types}
    assert "Справочник.Контрагенты" in metas and "Справочник.Бренды" in metas

    # документ с табличной частью
    doc = by_full["Документ.РеализацияТоваровУслуг"]
    ts = next(c for c in doc.children if c.kind == "TabularSection")
    assert ts.name == "Товары"
    assert {c.name for c in ts.children} >= {"Номенклатура", "Количество", "Цена"}

    # регистр с измерениями и ресурсом
    reg = by_full["РегистрНакопления.ТоварыНаСкладах"]
    kinds = {c.kind for c in reg.children}
    assert "Dimension" in kinds and "Resource" in kinds


# -------------------------------------------------------------------- DDL
def test_ddl_mariadb():
    schema = load_sample()
    sql = sqlgen.generate_ddl(schema, "mariadb")
    assert "CREATE DATABASE" in sql
    assert "`Spravochnik_Nomenklatura`" in sql
    assert "PRIMARY KEY" in sql
    # одиночное ссылочное поле -> id + view; составное -> JSON-колонка
    assert "`StavkaNDS`" in sql and "`StavkaNDS_view`" in sql
    assert "`Proizvoditel` JSON" in sql
    # ТЧ документа -> отдельная таблица
    assert "Dokument_RealizaciyaTovarovUslug_Tovary" in sql


def test_ddl_clickhouse_and_ydb():
    schema = load_sample()
    ch = sqlgen.generate_ddl(schema, "clickhouse")
    assert "ENGINE = MergeTree" in ch
    assert "Nullable(" in ch
    ydb = sqlgen.generate_ddl(schema, "ydb")
    assert "PRIMARY KEY" in ydb
    assert "Utf8" in ydb


def test_ddl_no_translit():
    schema = load_sample()
    opts = sqlgen.DDLOptions(transliterate=False)
    sql = sqlgen.generate_ddl(schema, "mariadb", opts)
    assert "Справочник_Номенклатура" in sql


# ----------------------------------------------------------- данные/маппинг
def test_datafile_iter_rows():
    rows = list(iter_rows(SAMPLE_DATA))
    objs = {obj.full_name for obj, _ in rows}
    assert "Справочник.Номенклатура" in objs
    assert len(rows) == 3


def test_summarize():
    s = summarize(SAMPLE_DATA)
    assert s.rows == 3
    assert s.objects["Справочник.Номенклатура"] == 2


def test_rowmap_extract():
    schema = load_sample()
    mappers = build_mappers(schema, sqlgen.DDLOptions())
    m = mappers["Справочник.Номенклатура"]
    # колонки содержат id и представление одиночной ссылки
    assert "Ssylka" in m.columns
    assert "StavkaNDS" in m.columns and "StavkaNDS_view" in m.columns
    # составной тип -> одна JSON-колонка
    assert "Proizvoditel" in m.columns and "Proizvoditel_view" not in m.columns
    # извлечение значений
    data = next(d for o, d in iter_rows(SAMPLE_DATA)
                if o.full_name == "Справочник.Номенклатура")
    row = m.extract(data)
    assert len(row) == len(m.columns)
    idx = m.columns.index("StavkaNDS")
    assert row[idx] == "00000000-0000-0000-0000-0000000000a1"
    idx_v = m.columns.index("StavkaNDS_view")
    assert row[idx_v] == "НДС 20%"


# -------------------------------------------------------------- профили
def test_profiles(tmp_path):
    schema = load_sample()
    # снимем галку с одного узла
    schema.nodes_by_id[7].selected = False
    p = tmp_path / "prof.json"
    profiles.save_profile(schema, str(p), name="test", settings={"split": "single"})

    schema2 = load_sample()
    prof = profiles.load_profile(str(p))
    changed, missing = profiles.apply_profile(schema2, prof)
    assert changed == 1
    assert schema2.nodes_by_id[7].selected is False


# ----------------------------------------------------------------- diff
def test_diff():
    old = load_sample()
    new = load_sample()
    # изменим тип и удалим узел
    new.nodes_by_id[7].types = [{"t": "String", "len": 50}]
    new.remove_node(new.nodes_by_id[9])
    d = diff.diff_schemas(old, new)
    assert any("Артикул" in p for p, *_ in d.type_changed)
    assert any("СтавкаНДС" in p for p in d.removed)


# ------------------------------------------------------------- валидатор
def test_validate():
    schema = load_sample()
    report = validate.validate_schema(schema)
    # в sample есть ссылки на отсутствующие объекты (Контрагенты, Бренды, …)
    assert not report.ok()
    msgs = " ".join(i.message for i in report.errors)
    assert "Контрагенты" in msgs or "Бренды" in msgs


# --------------------------------------------------------------- паспорт
def test_export_passport():
    schema = load_sample()
    md = exporters.export_markdown(schema)
    assert "# Паспорт конфигурации" in md
    assert "Справочник.Номенклатура" in md
    js = exporters.export_json(schema)
    assert '"objects"' in js


# ----------------------------------------------------------------- оценка
def test_estimate():
    schema = load_sample()
    s = summarize(SAMPLE_DATA)
    est = estimate.estimate(schema, sqlgen.DDLOptions(), s)
    by_name = {e.full_name: e for e in est}
    assert by_name["Справочник.Номенклатура"].rows == 2
    assert by_name["Справочник.Номенклатура"].est_bytes > 0
    assert estimate.human_size(2048) == "2.0 КБ"
