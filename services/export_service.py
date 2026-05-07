from __future__ import annotations

import json
from datetime import date
from enum import Enum
from pathlib import Path


class ExportFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    TERMINAL = "terminal"
    ALL = "all"


class ExportService:
    def export(self, day: date, content: str, fmt: ExportFormat) -> Path | None:
        if fmt == ExportFormat.MARKDOWN:
            return self.export_markdown(day, content)
        if fmt == ExportFormat.JSON:
            return self.export_json(day, content)
        if fmt == ExportFormat.TERMINAL:
            print(content)
            return None
        if fmt == ExportFormat.ALL:
            self.export_markdown(day, content)
            self.export_json(day, content)
            print(content)
            return None
        raise ValueError(f"Unsupported format: {fmt}")

    def export_markdown(self, day: date, content: str) -> Path:
        output_dir = Path("exports") / f"{day.year:04d}" / f"{day.month:02d}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{day.isoformat()}.md"
        output_file.write_text(content, encoding="utf-8")
        return output_file

    def export_json(self, day: date, content: str) -> Path:
        output_dir = Path("exports") / f"{day.year:04d}" / f"{day.month:02d}"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{day.isoformat()}.json"
        output_file.write_text(json.dumps({"date": day.isoformat(), "content": content}, indent=2), encoding="utf-8")
        return output_file
