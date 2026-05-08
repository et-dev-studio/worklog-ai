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

        grouped = await self.inference.chat(
            messages=[
                {"role": "system", "content": grouping_prompt},
                {"role": "user", "content": raw_context},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        categorized = await self.inference.chat(
            messages=[
                {"role": "system", "content": categorize_prompt},
                {"role": "user", "content": grouped},
            ],
            temperature=0.2,
            max_tokens=400,
        )
        result = await self.inference.chat(
            messages=[
                {"role": "system", "content": summarize_prompt},
                {"role": "user", "content": categorized},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        lines = [ln for ln in result.splitlines() if ln.strip()]
        if not all(ln.startswith("-") or ln.startswith("###") for ln in lines):
            lines = [f"- {ln.lstrip('- ').strip()}" for ln in lines]
        return "\n".join(lines)
