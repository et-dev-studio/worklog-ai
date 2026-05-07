from pathlib import Path


class PromptService:
    def __init__(self, prompt_dir: str = "prompts"):
        self.prompt_dir = Path(prompt_dir)

    def load(self, name: str) -> str:
        return (self.prompt_dir / name).read_text(encoding="utf-8").strip()
