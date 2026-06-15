"""Тесты модели и операций (без GUI). Запуск: python -m pytest editor/tests."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from onec_schema_editor.model import Schema, types_label  # noqa: E402
from onec_schema_editor.operations import (  # noqa: E402
    SearchOptions, compute_statistics, dryout_keep_subset, dryout_split,
    find_matches, replace_in_nodes,
)

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "examples", "sample.1cmeta")


def load():
    return Schema.load(SAMPLE)


def test_load_tree():
    schema = load()
    assert schema.header["config"] == "ДемоКонфигурация"
    assert len(schema.nodes_by_id) == 32
    # корень один
    assert len(schema.roots) == 1
    root = schema.roots[0]
    assert root.kind == "ConfigRoot"
    # у Номенклатуры есть стандартные и обычные реквизиты
    nom = schema.nodes_by_id[3]
    assert nom.name == "Номенклатура"
    assert any(c.kind == "StandardAttribute" for c in nom.children)
    assert any(c.kind == "Attribute" for c in nom.children)


def test_roundtrip(tmp_path):
    schema = load()
    out = tmp_path / "out.1cmeta"
    schema.save(str(out))
    reloaded = Schema.load(str(out))
    assert len(reloaded.nodes_by_id) == len(schema.nodes_by_id)
    assert reloaded.nodes_by_id[8].is_composite


def test_statistics():
    st = compute_statistics(load())
    assert st.total_nodes == 32
    assert st.objects == 3  # Справочник + Документ + РегистрНакопления
    assert st.composite_fields == 1
    assert st.ref_targets["Справочник.Номенклатура"] >= 2


def test_search():
    schema = load()
    opts = SearchOptions(text="номенклатура", field="all")
    matches = find_matches(schema, opts)
    assert matches, "должны найтись узлы по подстроке"
    names = {m.name for m in matches}
    assert "Номенклатура" in names


def test_replace():
    schema = load()
    opts = SearchOptions(text="Контрагенты", field="type")
    matches = find_matches(schema, opts)
    n = replace_in_nodes(matches, opts, "Партнеры")
    assert n >= 1
    # проверяем, что meta изменилась
    prod = schema.nodes_by_id[8]
    metas = [t.get("meta") for t in prod.types]
    assert "Справочник.Партнеры" in metas


def test_dryout_keep_subset():
    schema = load()
    prod = schema.nodes_by_id[8]
    assert prod.is_composite
    dryout_keep_subset(prod, [0])
    assert not prod.is_composite
    assert len(prod.types) == 1


def test_dryout_split():
    schema = load()
    prod = schema.nodes_by_id[8]
    parent = prod.parent
    before = len(parent.children)
    created = dryout_split(schema, prod, [0, 1])
    assert len(created) == 2
    # исходный узел удалён, добавлено 2
    assert len(parent.children) == before - 1 + 2
    for node in created:
        assert not node.is_composite


def test_types_label():
    assert types_label([{"t": "String", "len": 25}]) == "Строка(25)"
    assert types_label([{"t": "Ref", "meta": "Справочник.Х"}]) == "→ Справочник.Х"
    label = types_label([{"t": "String"}, {"t": "Boolean"}])
    assert label.startswith("Составной:")
