from services.inference_service import InferenceService
from services.prompt_service import PromptService


class ReflectionAgent:
    def __init__(self, inference: InferenceService, prompts: PromptService):
        self.inference = inference
        self.prompts = prompts

    async def generate_questions(self, event_text: str) -> str:
        system = self.prompts.load("reflection.txt")
        return await self.inference.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": event_text},
            ],
            temperature=0.4,
            max_tokens=200,
        )
