from services.inference_service import InferenceService
from services.prompt_service import PromptService


class ReflectionAgent:
    def __init__(self, inference: InferenceService, prompts: PromptService):
        self.inference = inference
        self.prompts = prompts

    async def generate_questions(self, event_text: str) -> str:
        template = self.prompts.load("reflection.txt")
        prompt = f"{template}\n\nEvent:\n{event_text}"
        return await self.inference.generate(prompt)
