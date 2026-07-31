"""Declarative operation registry, separate from providers and policy."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class OperationConfigError(ValueError):
    pass


DEFAULT_OPERATIONS_PATH = Path(__file__).resolve().parents[1] / "etc" / "operations.toml"


@dataclass(frozen=True)
class Operation:
    name: str
    description: str
    handler: str
    default_model: Optional[str]
    max_prompt_bytes: int
    max_tokens_ceiling: int
    deadline_ceiling_s: int
    artifact_media_type: str


@dataclass(frozen=True)
class ExecutionPolicy:
    max_tokens: Optional[int]
    deadline_s: int
    priority: int = 0


class OperationRegistry:
    def __init__(self, operations: tuple[Operation, ...]) -> None:
        names = [operation.name for operation in operations]
        if len(names) != len(set(names)):
            raise OperationConfigError("operation names must be unique")
        self.operations = operations

    def get(self, name: str) -> Operation:
        for operation in self.operations:
            if operation.name == name:
                return operation
        raise OperationConfigError(f"unknown operation: {name}")

    def policy_for(self, operation: Operation, raw: Optional[dict] = None) -> ExecutionPolicy:
        supplied = raw or {}
        unknown = set(supplied) - {"max_tokens", "deadline_s", "priority"}
        if unknown:
            raise OperationConfigError(
                f"unknown execution policy fields: {', '.join(sorted(unknown))}"
            )
        max_tokens = supplied.get("max_tokens")
        if max_tokens is not None and (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens <= 0
            or max_tokens > operation.max_tokens_ceiling
        ):
            raise OperationConfigError(
                f"max_tokens must be between 1 and {operation.max_tokens_ceiling}"
            )
        deadline_s = supplied.get("deadline_s", operation.deadline_ceiling_s)
        if (
            not isinstance(deadline_s, int)
            or isinstance(deadline_s, bool)
            or deadline_s <= 0
            or deadline_s > operation.deadline_ceiling_s
        ):
            raise OperationConfigError(
                f"deadline_s must be between 1 and {operation.deadline_ceiling_s}"
            )
        priority = supplied.get("priority", 0)
        if (
            not isinstance(priority, int)
            or isinstance(priority, bool)
            or priority < -10
            or priority > 10
        ):
            raise OperationConfigError("priority must be between -10 and 10")
        return ExecutionPolicy(max_tokens=max_tokens, deadline_s=deadline_s, priority=priority)


def load_operations(path: Optional[Path | str] = None) -> OperationRegistry:
    resolved = Path(
        path or os.environ.get("HEARTH_OPERATIONS", DEFAULT_OPERATIONS_PATH)
    )
    try:
        with resolved.open("rb") as stream:
            document = tomllib.load(stream)
    except FileNotFoundError as exc:
        raise OperationConfigError(f"operations registry not found: {resolved}") from exc
    table = document.get("operation")
    if not isinstance(table, dict) or not table:
        raise OperationConfigError("operations registry requires [operation.*] tables")
    operations = []
    for name, raw in table.items():
        if not isinstance(raw, dict):
            raise OperationConfigError(f"operation {name!r} must be a table")
        try:
            operation = Operation(
                name=name,
                description=str(raw["description"]),
                handler=str(raw["handler"]),
                default_model=(
                    str(raw["default_model"]) if raw.get("default_model") is not None else None
                ),
                max_prompt_bytes=int(raw["max_prompt_bytes"]),
                max_tokens_ceiling=int(raw["max_tokens_ceiling"]),
                deadline_ceiling_s=int(raw["deadline_ceiling_s"]),
                artifact_media_type=str(raw["artifact_media_type"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OperationConfigError(f"invalid operation {name!r}: {exc}") from exc
        if (
            not operation.description
            or not operation.handler
            or operation.max_prompt_bytes <= 0
            or operation.max_tokens_ceiling <= 0
            or operation.deadline_ceiling_s <= 0
            or not operation.artifact_media_type
        ):
            raise OperationConfigError(f"operation {name!r} has invalid limits or metadata")
        operations.append(operation)
    return OperationRegistry(tuple(operations))
