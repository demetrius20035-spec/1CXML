"""Профили выгрузки: сохранение/применение пресетов галок и настроек.

Профиль привязывается к узлам по «пути» (последовательность имён от корня),
а не по id — это позволяет переносить профиль на повторно выгруженную схему.
"""

from __future__ import annotations

import datetime
import json

from .model import Schema, SchemaNode


def node_path(node: SchemaNode) -> str:
    parts = []
    cur = node
    while cur is not None:
        parts.append(cur.name)
        cur = cur.parent
    return "/".join(reversed(parts))


def build_profile(schema: Schema, name: str = "", settings: dict | None = None) -> dict:
    selection = {node_path(n): n.selected for n in schema.all_nodes()}
    return {
        "format": "1cmeta-profile",
        "version": 1,
        "name": name,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "config": schema.header.get("config", "") if schema.header else "",
        "settings": settings or {},
        "selection": selection,
    }


def save_profile(schema: Schema, path: str, name: str = "", settings: dict | None = None) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build_profile(schema, name, settings), fh, ensure_ascii=False, indent=2)


def load_profile(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def apply_profile(schema: Schema, profile: dict) -> tuple[int, int]:
    """Применяет галки профиля. Возвращает (изменено, не найдено в схеме)."""
    selection = profile.get("selection", {})
    changed = 0
    matched_paths = set()
    for node in schema.all_nodes():
        path = node_path(node)
        if path in selection:
            matched_paths.add(path)
            if node.selected != selection[path]:
                node.selected = selection[path]
                changed += 1
    missing = len(set(selection) - matched_paths)
    return changed, missing
