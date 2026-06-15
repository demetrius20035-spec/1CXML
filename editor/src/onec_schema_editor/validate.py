"""Валидатор ссылочной целостности схемы .1cmeta."""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import Schema
from .profiles import node_path


@dataclass
class ValidationIssue:
    path: str
    message: str
    severity: str = "warning"  # warning | error


@dataclass
class ValidationReport:
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == "warning"]

    def ok(self) -> bool:
        return not self.errors

    def summary(self) -> str:
        return f"Ошибок: {len(self.errors)}  |  Предупреждений: {len(self.warnings)}"


def validate_schema(schema: Schema) -> ValidationReport:
    report = ValidationReport()

    # множество существующих объектов (по fullName)
    known = {n.full_name for n in schema.all_nodes() if n.full_name}

    for node in schema.all_nodes():
        path = node_path(node)
        for desc in node.types:
            t = desc.get("t")
            if t in ("Ref", "EnumRef"):
                meta = desc.get("meta", "")
                if meta and meta not in known:
                    report.issues.append(ValidationIssue(
                        path,
                        f"Ссылочный тип указывает на отсутствующий объект: {meta}",
                        "error"))
            elif t == "Unknown":
                report.issues.append(ValidationIssue(
                    path, f"Нераспознанный тип: {desc.get('raw', '')}", "warning"))

        # отмеченный объект без отмеченных полей — подозрительно
        if node.kind in ("Catalog", "Document", "InformationRegister",
                         "AccumulationRegister") and node.selected:
            sel_children = [c for c in node.children
                            if c.is_field and c.selected]
            if not sel_children and node.children:
                report.issues.append(ValidationIssue(
                    path, "Объект отмечен, но ни одно поле не выбрано", "warning"))

    return report
