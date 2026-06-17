"""Тесты групп 1–3: LLM-экспорт, фильтры/бейджи, массовые операции,
сверка с БД и обратная инженерия, undo/redo. Без GUI."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onec_schema_editor import dbcompare, filters, history, llm_export  # noqa: E402
from onec_schema_editor.model import Schema  # noqa: E402
from onec_schema_editor.operations import (  # noqa: E402
    bulk_delete, bulk_set_kind, bulk_set_selected,
)
from onec_schema_editor.sqlgen import DDLOptions  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "sample.1cmeta")


def load():
    return Schema.load(SAMPLE)


# ---------------------------------------------------------------- LLM экспорт
def test_json_schema():
    import json
    doc = json.loads(llm_export.to_json_schema(load()))
    assert "$defs" in doc
    assert "Spravochnik_Nomenklatura" in doc["$defs"]
    props = doc["$defs"]["Spravochnik_Nomenklatura"]["properties"]
    assert "Артикул" in props


def test_pydantic_and_sqlalchemy_and_dbt():
    sch = load()
    py = llm_export.to_pydantic(sch)
    assert "class Spravochnik_Nomenklatura(BaseModel):" in py
    assert "BaseModel" in py
    sa = llm_export.to_sqlalchemy(sch)
    assert "__tablename__" in sa and "primary_key=True" in sa
    dbt = llm_export.to_dbt_yaml(sch)
    assert "version: 2" in dbt and "models:" in dbt


def test_estimate_tokens():
    assert llm_export.estimate_tokens("x" * 400) == 100


# ------------------------------------------------------------------- фильтры
def test_filter_matches_and_badges():
    sch = load()
    known = filters.known_object_names(sch)
    prod = sch.nodes_by_id[8]  # Производитель — составной + dangling (Контрагенты/Бренды)
    badges = filters.node_badges(prod, known)
    assert "composite" in badges and "dangling" in badges

    f = filters.NodeFilter(only_composite=True)
    assert f.matches(prod, known)
    art = sch.nodes_by_id[7]  # Артикул — String
    assert not f.matches(art, known)

    f2 = filters.NodeFilter(text="номенклатура")
    assert f2.matches(sch.nodes_by_id[3], known)


def test_dangling_refs():
    sch = load()
    known = filters.known_object_names(sch)
    # Номенклатура существует — не dangling
    assert filters.dangling_refs(sch.nodes_by_id[19], known) == []  # ТЧ.Номенклатура
    # Контрагенты/Бренды отсутствуют
    assert filters.dangling_refs(sch.nodes_by_id[8], known)


# -------------------------------------------------------------- массовые опы
def test_bulk_ops():
    sch = load()
    nodes = [sch.nodes_by_id[7], sch.nodes_by_id[9]]
    n = bulk_set_selected(nodes, False)
    assert n == 2 and not sch.nodes_by_id[7].selected

    assert bulk_set_kind([sch.nodes_by_id[7]], "Dimension") == 1
    assert sch.nodes_by_id[7].kind == "Dimension"

    # удаление: объект + его поле — поле не удаляется отдельно
    obj = sch.nodes_by_id[3]
    field = sch.nodes_by_id[7]
    roots = bulk_delete(sch, [obj, field])
    assert len(roots) == 1 and roots[0] is obj
    assert 7 not in sch.nodes_by_id and 3 not in sch.nodes_by_id


# --------------------------------------------------------------- сверка с БД
def test_expected_columns_and_compare():
    sch = load()
    expected = dbcompare.expected_columns(sch, DDLOptions())
    assert "Spravochnik_Nomenklatura" in expected
    cols = expected["Spravochnik_Nomenklatura"]
    assert cols["Ssylka"] == "uuid"
    assert "StavkaNDS" in cols and cols["StavkaNDS"] == "uuid"

    # actual: одной колонки нет, у одной — неверный тип
    actual = {}
    for tbl, c in expected.items():
        actual[tbl] = {col: ("String" if fam != "number" else "Int64") for col, fam in c.items()}
    # сломаем: уберём колонку и испортим тип даты
    del actual["Spravochnik_Nomenklatura"]["Artikul"]
    report = dbcompare.compare(sch, actual, DDLOptions())
    assert ("Spravochnik_Nomenklatura", "Artikul") in report.missing_columns
    assert not report.missing_tables  # все таблицы на месте


def test_compare_missing_table():
    sch = load()
    report = dbcompare.compare(sch, {}, DDLOptions())
    assert report.missing_tables  # БД пустая — все таблицы отсутствуют
    assert not report.ok()


def test_reverse_engineer():
    actual = {"Spravochnik_Tovary": {"Ssylka": "UUID", "Cena": "Decimal(15, 2)",
                                      "Data": "DateTime", "Flag": "Bool"}}
    sch = dbcompare.schema_from_db_tables(actual, "DBImport")
    by_full = {n.full_name: n for n in sch.all_nodes() if n.full_name}
    assert "Справочник.Spravochnik_Tovary" in by_full
    obj = by_full["Справочник.Spravochnik_Tovary"]
    cols = {c.name: c.types[0]["t"] for c in obj.children}
    assert cols["Cena"] == "Number" and cols["Data"] == "Date" and cols["Flag"] == "Boolean"


# ------------------------------------------------------------------ undo/redo
def test_history_field_and_selection():
    sch = load()
    h = history.History()
    node = sch.nodes_by_id[7]
    old, new = node.name, "АртикулНовый"
    node.name = new
    h.push(history.FieldCommand(node, "name", old, new))
    h.undo()
    assert node.name == old
    h.redo()
    assert node.name == new

    changes = [(node, node.selected, not node.selected)]
    node.selected = not node.selected
    h.push(history.SelectionCommand(changes))
    cur = node.selected
    h.undo()
    assert node.selected != cur


def test_history_subtree_delete():
    sch = load()
    h = history.History()
    obj = sch.nodes_by_id[3]
    parent = obj.parent
    before = sch.snapshot_children(parent)
    sch.remove_node(obj)
    after = sch.snapshot_children(parent)
    h.push(history.SubtreeCommand(sch, parent, before, after))
    assert 3 not in sch.nodes_by_id
    h.undo()
    assert 3 in sch.nodes_by_id  # объект восстановлен
    assert any(c.id == 3 for c in parent.children)
    h.redo()
    assert 3 not in sch.nodes_by_id
