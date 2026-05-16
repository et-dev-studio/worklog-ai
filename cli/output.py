from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import typer


def _default(obj: Any) -> Any:
    import uuid

    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, uuid.UUID):
        return str(obj)
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"not serializable: {type(obj).__name__}")


def emit_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, default=_default, indent=2))


def emit(payload: Any, json_mode: bool, text: str) -> None:
    if json_mode:
        emit_json(payload)
    else:
        typer.echo(text)
