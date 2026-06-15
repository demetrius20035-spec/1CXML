"""Сравнение двух схем .1cmeta (например, между релизами конфигурации)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Schema, SchemaNode, types_label
from .profiles import node_path


@dataclass
class SchemaDiff:
    added: list[str] = field(default_factory=list)        # пути, появившиеся в new
    removed: list[str] = field(default_factory=list)      # пути, исчезнувшие
    type_changed: list[tuple[str, str, str]] = field(default_factory=list)  # (path, old, new)
    kind_changed: list[tuple[str, str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.type_changed or self.kind_changed)

    def summary(self) -> str:
        return (f"Добавлено: {len(self.added)}  |  Удалено: {len(self.removed)}  |  "
                f"Сменился тип: {len(self.type_changed)}  |  "
                f"Сменился вид: {len(self.kind_changed)}")


def _index(schema: Schema) -> dict[str, SchemaNode]:
    return {node_path(n): n for n in schema.all_nodes()}


def diff_schemas(old: Schema, new: Schema) -> SchemaDiff:
    old_idx = _index(old)
    new_idx = _index(new)
    result = SchemaDiff()

    for path in new_idx:
        if path not in old_idx:
            result.added.append(path)
    for path in old_idx:
        if path not in new_idx:
            result.removed.append(path)

    for path, new_node in new_idx.items():
        old_node = old_idx.get(path)
        if old_node is None:
            continue
        if old_node.kind != new_node.kind:
            result.kind_changed.append((path, old_node.kind, new_node.kind))
        old_t = types_label(old_node.types)
        new_t = types_label(new_node.types)
        if old_t != new_t:
            result.type_changed.append((path, old_t, new_t))

    for lst in (result.added, result.removed):
        lst.sort()
    return result
