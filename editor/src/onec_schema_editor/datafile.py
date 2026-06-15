"""Потоковое чтение файлов данных .1cdata (NDJSON). См. FORMAT.md §7."""

from __future__ import annotations

import glob
import json
import os
from dataclasses import dataclass, field
from typing import Iterator, Optional


@dataclass
class DataObject:
    full_name: str
    kind: str = ""
    columns: list[str] = field(default_factory=list)


def iter_records(path: str) -> Iterator[dict]:
    """Возвращает все JSON-записи файла .1cdata по порядку."""
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def iter_rows(path: str) -> Iterator[tuple[DataObject, dict]]:
    """Возвращает пары (текущий объект, данные строки) по всему файлу.

    Маркеры object переключают текущий объект; строки row отдают данные.
    """
    current = DataObject("")
    objects: dict[str, DataObject] = {}
    for rec in iter_records(path):
        rtype = rec.get("$")
        if rtype == "object":
            name = rec.get("obj", "")
            obj = objects.get(name)
            if obj is None:
                obj = DataObject(name, rec.get("kind", ""), rec.get("columns", []) or [])
                objects[name] = obj
            elif rec.get("columns"):
                obj.columns = rec.get("columns")
            current = obj
        elif rtype == "row":
            name = rec.get("obj", current.full_name)
            obj = objects.get(name, current)
            yield obj, rec.get("data", {})


def discover_volumes(path: str) -> list[str]:
    """По одному файлу/маске находит все тома набора (*.vNNN.1cdata и т.п.)."""
    if os.path.isdir(path):
        return sorted(glob.glob(os.path.join(path, "*.1cdata")))
    if os.path.exists(path):
        return [path]
    return sorted(glob.glob(path))


def iter_rows_multi(paths_or_dir: str) -> Iterator[tuple[DataObject, dict]]:
    for vol in discover_volumes(paths_or_dir):
        yield from iter_rows(vol)


@dataclass
class DataSummary:
    files: int = 0
    rows: int = 0
    objects: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


def summarize(paths_or_dir: str) -> DataSummary:
    summary = DataSummary()
    for vol in discover_volumes(paths_or_dir):
        summary.files += 1
        for rec in iter_records(vol):
            rtype = rec.get("$")
            if rtype == "row":
                summary.rows += 1
                obj = rec.get("obj", "?")
                summary.objects[obj] = summary.objects.get(obj, 0) + 1
            elif rtype == "error":
                summary.errors.append(f"{rec.get('obj')}: {rec.get('message')}")
    return summary


def ref_value(value) -> Optional[str]:
    """Извлекает id из значения-ссылки {'$ref':...} либо отдаёт как есть."""
    if isinstance(value, dict) and "$ref" in value:
        return value.get("$ref")
    return value


def ref_presentation(value) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("p")
    return None
