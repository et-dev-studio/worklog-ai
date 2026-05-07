from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass


class InferenceUnavailable(RuntimeError):
    pass


@dataclass
class InferenceHealth:
    configured: bool
    runnable: bool
    detail: str


class InferenceService:
    async def healthcheck(self) -> InferenceHealth:
        cmd = os.getenv("BITNET_CMD")
        if not cmd:
            return InferenceHealth(configured=False, runnable=False, detail="BITNET_CMD not set")

        proc = await asyncio.create_subprocess_shell(
            f"{cmd} --help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            return InferenceHealth(configured=True, runnable=False, detail="healthcheck timed out")
        if proc.returncode != 0:
            return InferenceHealth(configured=True, runnable=False, detail=stderr.decode("utf-8", errors="ignore")[:200])
        return InferenceHealth(configured=True, runnable=True, detail="ok")

    async def generate(self, prompt: str, temperature: float = 0.3) -> str:
        cmd = os.getenv("BITNET_CMD")
        if not cmd:
            raise InferenceUnavailable(
                "BITNET_CMD not set. Configure the local inference binary or run `wl doctor` for setup."
            )

        last_error = ""
        for _ in range(2):
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode("utf-8")), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                last_error = "inference timed out after 30s"
                continue
            if proc.returncode == 0:
                return stdout.decode("utf-8", errors="ignore").strip()
            last_error = stderr.decode("utf-8", errors="ignore")
        raise InferenceUnavailable(last_error or "BITNET subprocess failed")
