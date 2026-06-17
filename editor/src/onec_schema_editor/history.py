"""Undo/Redo: стек команд для операций редактора."""

from __future__ import annotations

import copy
from typing import Optional

from .model import Schema, SchemaNode


class Command:
    label = "операция"

    def undo(self) -> None: ...
    def redo(self) -> None: ...


class FieldCommand(Command):
    """Изменение скалярного поля узла (name/kind/synonym)."""

    def __init__(self, node: SchemaNode, attr: str, old, new, label="изменение поля"):
        self.node, self.attr, self.old, self.new, self.label = node, attr, old, new, label

    def undo(self):
        self.node.data[self.attr] = self.old

    def redo(self):
        self.node.data[self.attr] = self.new


class TypesCommand(Command):
    """Изменение массива типов узла."""

    def __init__(self, node: SchemaNode, old_types: list, new_types: list, label="изменение типа"):
        self.node = node
        self.old = copy.deepcopy(old_types)
        self.new = copy.deepcopy(new_types)
        self.label = label

    def undo(self):
        self.node.types = copy.deepcopy(self.old)

    def redo(self):
        self.node.types = copy.deepcopy(self.new)


class SelectionCommand(Command):
    """Изменение галок (sel) у набора узлов."""

    def __init__(self, changes: list[tuple[SchemaNode, bool, bool]], label="изменение галок"):
        self.changes = changes  # (node, old, new)
        self.label = label

    def undo(self):
        for node, old, _new in self.changes:
            node.selected = old

    def redo(self):
        for node, _old, new in self.changes:
            node.selected = new


class SubtreeCommand(Command):
    """Структурное изменение детей родителя (удаление/высушивание-split)."""

    def __init__(self, schema: Schema, parent: SchemaNode,
                 before: list, after: list, label="структурное изменение"):
        self.schema, self.parent = schema, parent
        self.before, self.after, self.label = before, after, label

    def undo(self):
        self.schema.restore_children(self.parent, self.before)

    def redo(self):
        self.schema.restore_children(self.parent, self.after)


class CompositeCommand(Command):
    def __init__(self, commands: list[Command], label="групповая операция"):
        self.commands, self.label = commands, label

    def undo(self):
        for cmd in reversed(self.commands):
            cmd.undo()

    def redo(self):
        for cmd in self.commands:
            cmd.redo()


class History:
    def __init__(self, limit: int = 200):
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        self._limit = limit

    def push(self, command: Command) -> None:
        """Регистрирует уже применённую команду."""
        self._undo.append(command)
        self._redo.clear()
        if len(self._undo) > self._limit:
            self._undo.pop(0)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> Optional[Command]:
        if not self._undo:
            return None
        cmd = self._undo.pop()
        cmd.undo()
        self._redo.append(cmd)
        return cmd

    def redo(self) -> Optional[Command]:
        if not self._redo:
            return None
        cmd = self._redo.pop()
        cmd.redo()
        self._undo.append(cmd)
        return cmd

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
