from services.inference_service import InferenceService
from services.prompt_service import PromptService


class SummaryAgent:
    def __init__(self, inference: InferenceService, prompts: PromptService):
        self.inference = inference
        self.prompts = prompts

    async def summarize(self, raw_context: str) -> str:
        grouping_prompt = self.prompts.load("grouping.txt")
        categorize_prompt = self.prompts.load("categorize.txt")
        summarize_prompt = self.prompts.load("summarize.txt")

        grouped = await self.inference.generate(f"{grouping_prompt}\n\n{raw_context}")
        categorized = await self.inference.generate(f"{categorize_prompt}\n\n{grouped}")
        final_prompt = f"{summarize_prompt}\n\nContext:\n{categorized}"
        result = await self.inference.generate(final_prompt)
        lines = [ln for ln in result.splitlines() if ln.strip()]
        if not all(ln.startswith("-") or ln.startswith("###") for ln in lines):
            lines = [f"- {ln.lstrip('- ').strip()}" for ln in lines]
        return "\n".join(lines)
