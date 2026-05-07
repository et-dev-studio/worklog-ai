from __future__ import annotations

import sys
from typing import Iterable

import questionary
import typer


def is_tty() -> bool:
    return sys.stdin.isatty()


def require_tty(operation: str = "this command") -> None:
    if not is_tty():
        typer.echo(
            f"Error: {operation} requires an interactive terminal. "
            f"Pass --yes or supply explicit flags to run non-interactively.",
            err=True,
        )
        raise typer.Exit(code=2)


def select_from(prompt_text: str, choices: Iterable[questionary.Choice], default: str | None = None) -> str | None:
    if not is_tty():
        return default
    answer = questionary.select(prompt_text, choices=list(choices)).ask()
    return answer if answer is not None else default


def text_input(prompt_text: str, default: str = "") -> str | None:
    if not is_tty():
        return default or None
    return questionary.text(prompt_text, default=default).ask()


def confirm_choice(prompt_text: str, choices: list[str], default: str) -> str:
    if not is_tty():
        return default
    answer = questionary.select(prompt_text, choices=choices).ask()
    return answer if answer is not None else default
