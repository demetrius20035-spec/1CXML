"""Импорт структуры метаданных из XML-выгрузки конфигурации 1С.

Поддерживается формат «Выгрузить конфигурацию в файлы…» / EDT: каталог с
``Configuration.xml`` и подкаталогами Catalogs/, Documents/, Enums/ … где каждый
объект описан файлом ``<Имя>.xml`` (корень ``MetaDataObject``).

XML-выгрузка НЕ содержит стандартные реквизиты (Ссылка, Код, Дата, …) — они
синтезируются эвристически по виду объекта и его свойствам, чтобы итог совпадал
по полноте с выгрузкой обработкой 1С.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

from .model import Schema, SchemaNode

# Каталог выгрузки -> вид узла (kind == английскому тегу объекта).
DIR_TO_KIND = {
    "Constants": "Constant",
    "Catalogs": "Catalog",
    "Documents": "Document",
    "DocumentJournals": "DocumentJournal",
    "Enums": "Enum",
    "ChartsOfCharacteristicTypes": "ChartOfCharacteristicTypes",
    "ChartsOfAccounts": "ChartOfAccounts",
    "ChartsOfCalculationTypes": "ChartOfCalculationTypes",
    "InformationRegisters": "InformationRegister",
    "AccumulationRegisters": "AccumulationRegister",
    "AccountingRegisters": "AccountingRegister",
    "CalculationRegisters": "CalculationRegister",
    "BusinessProcesses": "BusinessProcess",
    "Tasks": "Task",
    "ExchangePlans": "ExchangePlan",
    "Reports": "Report",
    "DataProcessors": "DataProcessor",
}

# Порядок и русские имена классов (как в выгрузке обработкой).
CLASS_ORDER = [
    ("Constants", "Константы"),
    ("Catalogs", "Справочники"),
    ("Documents", "Документы"),
    ("DocumentJournals", "ЖурналыДокументов"),
    ("Enums", "Перечисления"),
    ("ChartsOfCharacteristicTypes", "ПланыВидовХарактеристик"),
    ("ChartsOfAccounts", "ПланыСчетов"),
    ("ChartsOfCalculationTypes", "ПланыВидовРасчета"),
    ("InformationRegisters", "РегистрыСведений"),
    ("AccumulationRegisters", "РегистрыНакопления"),
    ("AccountingRegisters", "РегистрыБухгалтерии"),
    ("CalculationRegisters", "РегистрыРасчета"),
    ("BusinessProcesses", "БизнесПроцессы"),
    ("Tasks", "Задачи"),
    ("ExchangePlans", "ПланыОбмена"),
]

# Вид -> русский префикс полного имени.
KIND_TO_RU = {
    "Constant": "Константа",
    "Catalog": "Справочник",
    "Document": "Документ",
    "DocumentJournal": "ЖурналДокументов",
    "Enum": "Перечисление",
    "ChartOfCharacteristicTypes": "ПланВидовХарактеристик",
    "ChartOfAccounts": "ПланСчетов",
    "ChartOfCalculationTypes": "ПланВидовРасчета",
    "InformationRegister": "РегистрСведений",
    "AccumulationRegister": "РегистрНакопления",
    "AccountingRegister": "РегистрБухгалтерии",
    "CalculationRegister": "РегистрРасчета",
    "BusinessProcess": "БизнесПроцесс",
    "Task": "Задача",
    "ExchangePlan": "ПланОбмена",
    "Report": "Отчет",
    "DataProcessor": "Обработка",
}

# Английский Ref-класс (из cfg:XxxRef.Name) -> русский префикс.
REFCLASS_TO_RU = {
    "CatalogRef": "Справочник",
    "DocumentRef": "Документ",
    "EnumRef": "Перечисление",
    "ChartOfCharacteristicTypesRef": "ПланВидовХарактеристик",
    "ChartOfAccountsRef": "ПланСчетов",
    "ChartOfCalculationTypesRef": "ПланВидовРасчета",
    "BusinessProcessRef": "БизнесПроцесс",
    "TaskRef": "Задача",
    "ExchangePlanRef": "ПланОбмена",
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_local(elem, name):
    for child in elem:
        if _local(child.tag) == name:
            return child
    return None


def _find_all_local(elem, name):
    return [c for c in elem if _local(c.tag) == name]


def _text(elem, name, default=""):
    child = _find_local(elem, name)
    return (child.text or default).strip() if child is not None else default


def _synonym(props):
    syn = _find_local(props, "Synonym")
    if syn is None:
        return ""
    for item in syn:
        content = _find_local(item, "content")
        if content is not None and content.text:
            return content.text.strip()
    return ""


# ---------------------------------------------------------------------------
# Разбор типа
# ---------------------------------------------------------------------------

def _parse_type(type_elem) -> list[dict]:
    if type_elem is None:
        return []
    tokens = []
    str_q = num_q = date_q = None
    for child in type_elem:
        lname = _local(child.tag)
        if lname == "Type":
            if child.text:
                tokens.append(child.text.strip())
        elif lname == "StringQualifiers":
            str_q = child
        elif lname == "NumberQualifiers":
            num_q = child
        elif lname == "DateQualifiers":
            date_q = child
    descriptors = []
    for token in tokens:
        descriptors.append(_token_to_descriptor(token, str_q, num_q, date_q))
    return descriptors


def _token_to_descriptor(token: str, str_q, num_q, date_q) -> dict:
    bare = token.split(":", 1)[-1]  # отбрасываем xs:/v8:/cfg:
    if bare == "string":
        d = {"t": "String"}
        if str_q is not None:
            d["len"] = int(_text(str_q, "Length", "0") or 0)
            al = _text(str_q, "AllowedLength", "Variable")
            d["allowedLength"] = "Fixed" if al == "Fixed" else "Variable"
        return d
    if bare == "decimal":
        d = {"t": "Number"}
        if num_q is not None:
            d["digits"] = int(_text(num_q, "Digits", "0") or 0)
            d["fraction"] = int(_text(num_q, "FractionDigits", "0") or 0)
            d["nonNegative"] = _text(num_q, "AllowedSign", "Any") == "Nonnegative"
        return d
    if bare in ("dateTime", "date"):
        parts = "DateTime"
        if date_q is not None:
            df = _text(date_q, "DateFractions", "DateTime")
            parts = {"Date": "Date", "Time": "Time", "DateTime": "DateTime"}.get(df, "DateTime")
        return {"t": "Date", "dateParts": parts}
    if bare == "boolean":
        return {"t": "Boolean"}
    if bare == "ValueStorage":
        return {"t": "ValueStorage"}
    if bare in ("UUID", "uuid"):
        return {"t": "UUID"}
    if bare == "AnyRef":
        return {"t": "AnyRef"}
    if bare.startswith("DefinedType."):
        return {"t": "DefinedType", "name": bare.split(".", 1)[1]}
    if bare.startswith("Characteristic."):
        return {"t": "Characteristic", "name": bare.split(".", 1)[1]}
    if "Ref." in bare or bare.endswith("Ref"):
        cls, _, name = bare.partition(".")
        ru = REFCLASS_TO_RU.get(cls)
        if ru:
            t = "EnumRef" if cls == "EnumRef" else "Ref"
            return {"t": t, "meta": f"{ru}.{name}"}
    return {"t": "Unknown", "raw": token}


# ---------------------------------------------------------------------------
# Стандартные реквизиты (синтез)
# ---------------------------------------------------------------------------

def _std(name, descs):
    return {"name": name, "kind": "StandardAttribute", "types": descs}


def _standard_attributes(kind: str, full_name: str, props) -> list[dict]:
    self_ref = [{"t": "Ref", "meta": full_name}]
    result = []
    if kind in ("Catalog", "ChartOfCharacteristicTypes", "ChartOfAccounts",
                "ChartOfCalculationTypes"):
        result.append(_std("Ссылка", self_ref))
        result.append(_std("ПометкаУдаления", [{"t": "Boolean"}]))
        result.append(_std("Предопределенный", [{"t": "Boolean"}]))
        code_len = int(_text(props, "CodeLength", "0") or 0) if props is not None else 0
        if code_len:
            code_type = _text(props, "CodeType", "String") if props is not None else "String"
            if code_type == "Number":
                result.append(_std("Код", [{"t": "Number", "digits": code_len, "fraction": 0}]))
            else:
                result.append(_std("Код", [{"t": "String", "len": code_len}]))
        descr_len = int(_text(props, "DescriptionLength", "0") or 0) if props is not None else 0
        if descr_len:
            result.append(_std("Наименование", [{"t": "String", "len": descr_len}]))
        if props is not None and _text(props, "Hierarchical", "false") == "true":
            result.append(_std("Родитель", self_ref))
            if _text(props, "HierarchyType", "") == "HierarchyFoldersAndItems":
                result.append(_std("ЭтоГруппа", [{"t": "Boolean"}]))
    elif kind == "Document":
        result.append(_std("Ссылка", self_ref))
        result.append(_std("ПометкаУдаления", [{"t": "Boolean"}]))
        result.append(_std("Дата", [{"t": "Date", "dateParts": "DateTime"}]))
        num_len = int(_text(props, "NumberLength", "0") or 0) if props is not None else 0
        if num_len:
            num_type = _text(props, "NumberType", "String") if props is not None else "String"
            if num_type == "Number":
                result.append(_std("Номер", [{"t": "Number", "digits": num_len, "fraction": 0}]))
            else:
                result.append(_std("Номер", [{"t": "String", "len": num_len}]))
        result.append(_std("Проведен", [{"t": "Boolean"}]))
    elif kind in ("InformationRegister", "AccumulationRegister",
                  "AccountingRegister", "CalculationRegister"):
        if kind == "InformationRegister" and props is not None \
                and _text(props, "InformationRegisterPeriodicity", "Nonperiodical") != "Nonperiodical":
            result.append(_std("Период", [{"t": "Date", "dateParts": "DateTime"}]))
        if kind in ("AccumulationRegister", "AccountingRegister", "CalculationRegister"):
            result.append(_std("Период", [{"t": "Date", "dateParts": "DateTime"}]))
            result.append(_std("Регистратор", [{"t": "AnyRef"}]))
            result.append(_std("Активность", [{"t": "Boolean"}]))
    elif kind in ("BusinessProcess", "Task", "ExchangePlan"):
        result.append(_std("Ссылка", self_ref))
    return result


# ---------------------------------------------------------------------------
# Разбор одного объекта
# ---------------------------------------------------------------------------

class _Builder:
    def __init__(self):
        self.nodes: list[dict] = []
        self.next_id = 1

    def add(self, parent_id: int, kind: str, name: str, **extra) -> int:
        nid = self.next_id
        self.next_id += 1
        data = {"$": "node", "id": nid, "parent": parent_id, "kind": kind,
                "name": name, "sel": True}
        for k, v in extra.items():
            if v not in (None, "", []):
                data[k] = v
        if "types" in data and len(data["types"]) > 1:
            data["composite"] = True
        self.nodes.append(data)
        return nid

    def add_field(self, parent_id: int, kind: str, name: str, descs: list[dict],
                  synonym: str = "") -> int:
        return self.add(parent_id, kind, name, types=descs, synonym=synonym)


def _import_object(builder: _Builder, class_id: int, obj_elem, kind: str) -> None:
    props = _find_local(obj_elem, "Properties")
    name = _text(props, "Name") if props is not None else ""
    if not name:
        return
    full_name = f"{KIND_TO_RU.get(kind, kind)}.{name}"
    synonym = _synonym(props) if props is not None else ""
    comment = _text(props, "Comment") if props is not None else ""

    extra = {"synonym": synonym, "fullName": full_name, "comment": comment}
    obj_id = builder.add(class_id, kind, name, **extra)

    # стандартные реквизиты
    for std in _standard_attributes(kind, full_name, props):
        builder.add_field(obj_id, std["kind"], std["name"], std["types"])

    if kind == "Constant" and props is not None:
        descs = _parse_type(_find_local(props, "Type"))
        builder.add_field(obj_id, "Attribute", "Значение", descs, synonym)
        return

    children = _find_local(obj_elem, "ChildObjects")
    if children is None:
        return
    for child in children:
        cname = _local(child.tag)
        if cname in ("Attribute", "Dimension", "Resource", "AccountingFlag",
                     "ExtDimensionAccountingFlag"):
            cprops = _find_local(child, "Properties")
            if cprops is None:
                continue
            fname = _text(cprops, "Name")
            descs = _parse_type(_find_local(cprops, "Type"))
            builder.add_field(obj_id, cname, fname, descs, _synonym(cprops))
        elif cname == "EnumValue":
            cprops = _find_local(child, "Properties")
            if cprops is not None:
                builder.add(obj_id, "EnumValue", _text(cprops, "Name"),
                            synonym=_synonym(cprops))
        elif cname == "TabularSection":
            _import_tabular_section(builder, obj_id, child)


def _import_tabular_section(builder: _Builder, obj_id: int, ts_elem) -> None:
    props = _find_local(ts_elem, "Properties")
    ts_name = _text(props, "Name") if props is not None else ""
    ts_id = builder.add(obj_id, "TabularSection", ts_name,
                        synonym=_synonym(props) if props is not None else "")
    children = _find_local(ts_elem, "ChildObjects")
    if children is None:
        return
    for child in _find_all_local(children, "Attribute"):
        cprops = _find_local(child, "Properties")
        if cprops is None:
            continue
        descs = _parse_type(_find_local(cprops, "Type"))
        builder.add_field(ts_id, "Attribute", _text(cprops, "Name"), descs,
                          _synonym(cprops))


# ---------------------------------------------------------------------------
# Точка входа
# ---------------------------------------------------------------------------

def import_configuration(path: str, include_reports: bool = False) -> Schema:
    """Строит :class:`Schema` из каталога XML-выгрузки конфигурации."""
    root_dir = path
    if os.path.isfile(path):
        root_dir = os.path.dirname(path)

    config_xml = os.path.join(root_dir, "Configuration.xml")
    config_name, config_version, config_synonym = "Конфигурация", "", ""
    if os.path.exists(config_xml):
        try:
            croot = ET.parse(config_xml).getroot()
            cobj = next(iter(croot), None)
            cprops = _find_local(cobj, "Properties") if cobj is not None else None
            if cprops is not None:
                config_name = _text(cprops, "Name") or config_name
                config_version = _text(cprops, "Version")
                config_synonym = _synonym(cprops)
        except ET.ParseError:
            pass

    builder = _Builder()
    root_id = builder.add(0, "ConfigRoot", config_name,
                          synonym=config_synonym, fullName="Конфигурация")

    order = list(CLASS_ORDER)
    if include_reports:
        order += [("Reports", "Отчеты"), ("DataProcessors", "Обработки")]

    for dir_name, class_title in order:
        kind = DIR_TO_KIND[dir_name]
        class_dir = os.path.join(root_dir, dir_name)
        if not os.path.isdir(class_dir):
            continue
        xml_files = sorted(f for f in os.listdir(class_dir) if f.endswith(".xml"))
        if not xml_files:
            continue
        class_id = builder.add(root_id, "MetadataClass", class_title)
        for fn in xml_files:
            try:
                obj_root = ET.parse(os.path.join(class_dir, fn)).getroot()
            except ET.ParseError:
                continue
            obj_elem = next(iter(obj_root), None)
            if obj_elem is not None and _local(obj_elem.tag) == kind:
                _import_object(builder, class_id, obj_elem, kind)

    header = {
        "$": "header", "format": "1cmeta-ndjson", "version": 1,
        "config": config_name, "configSynonym": config_synonym,
        "configVersion": config_version, "vendor": "", "platform": "",
        "generated": "", "source": "Configuration.xml import",
    }
    return _build_schema(header, builder.nodes)


def _build_schema(header: dict, node_dicts: list[dict]) -> Schema:
    schema = Schema(header=header)
    for data in node_dicts:
        node = SchemaNode(data)
        schema.nodes_by_id[node.id] = node
        schema.max_id = max(schema.max_id, node.id)
    for node in schema.nodes_by_id.values():
        parent = schema.nodes_by_id.get(node.parent_id)
        if parent is not None:
            parent.children.append(node)
            node.parent = parent
        else:
            schema.roots.append(node)
    return schema
