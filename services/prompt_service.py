from pathlib import Path

from services.paths_service import prompts_dir


class PromptService:
    def __init__(self, prompt_dir: str | None = None):
        self.prompt_dir = Path(prompt_dir) if prompt_dir else prompts_dir()

    def load(self, name: str) -> str:
        target = self.prompt_dir / name
        if not target.exists():
            raise FileNotFoundError(
                f"Prompt template missing: {target}. "
                f"Set WORKLOG_PROMPTS or restore the prompts/ directory."
            )
        return target.read_text(encoding="utf-8").strip()
