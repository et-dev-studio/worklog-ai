"""Worklog services package.

Importing any ``services.*`` module triggers a one-shot ``.env`` load
via python-dotenv. Shell exports always win over ``.env`` contents
(python-dotenv default), so a real export is never overridden by a
stale file.
"""

from __future__ import annotations

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))
