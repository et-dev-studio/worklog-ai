from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_URL = "http://127.0.0.1:8080/v1"
DEFAULT_TIMEOUT = 120.0


class InferenceUnavailable(RuntimeError):
    """Raised when the inference server is unreachable, returned a non-2xx
    response, or produced an empty completion."""


@dataclass
class InferenceHealth:
    configured: bool
    runnable: bool
    detail: str
    url: str | None = None
    model: str | None = None
    latency_ms: float | None = None


class InferenceService:
    """OpenAI-compatible Chat Completions client.

    Configuration via env:
        WORKLOG_INFERENCE_URL      Base URL ending in /v1 (default http://127.0.0.1:8080/v1)
        WORKLOG_INFERENCE_MODEL    Model id; auto-detected from /v1/models if unset
        WORKLOG_INFERENCE_API_KEY  Bearer token (optional)
        WORKLOG_INFERENCE_TIMEOUT  Per-call timeout in seconds (default 120)
    """

    def __init__(
        self,
        url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.url = (url or os.getenv("WORKLOG_INFERENCE_URL") or DEFAULT_URL).rstrip("/")
        self._configured_model = model or os.getenv("WORKLOG_INFERENCE_MODEL")
        self.api_key = api_key or os.getenv("WORKLOG_INFERENCE_API_KEY")
        self.timeout = timeout if timeout is not None else float(
            os.getenv("WORKLOG_INFERENCE_TIMEOUT", DEFAULT_TIMEOUT)
        )
        self._cached_model: str | None = self._configured_model

    @property
    def model(self) -> str | None:
        return self._cached_model

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "worklog-ai/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _request(self, path: str, method: str = "GET", payload: dict | None = None) -> Any:
        url = f"{self.url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise InferenceUnavailable(f"HTTP {exc.code} from {url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise InferenceUnavailable(f"cannot reach {url}: {exc.reason}") from exc
        except TimeoutError as exc:
            raise InferenceUnavailable(f"timeout calling {url}") from exc
        if not body:
            raise InferenceUnavailable(f"empty response from {url}")
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise InferenceUnavailable(f"invalid JSON from {url}: {body[:200]!r}") from exc

    async def healthcheck(self) -> InferenceHealth:
        import time

        start = time.perf_counter()
        try:
            payload = await asyncio.to_thread(self._request, "/models", "GET", None)
        except InferenceUnavailable as exc:
            return InferenceHealth(
                configured=True, runnable=False, detail=str(exc), url=self.url
            )

        latency_ms = (time.perf_counter() - start) * 1000.0
        models = payload.get("data") or []
        if not models:
            return InferenceHealth(
                configured=True,
                runnable=False,
                detail="server returned empty model list",
                url=self.url,
                latency_ms=latency_ms,
            )
        detected = self._configured_model or models[0].get("id")
        if not detected:
            return InferenceHealth(
                configured=True,
                runnable=False,
                detail="no model id available",
                url=self.url,
                latency_ms=latency_ms,
            )
        self._cached_model = detected
        return InferenceHealth(
            configured=True,
            runnable=True,
            detail="ok",
            url=self.url,
            model=detected,
            latency_ms=latency_ms,
        )

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 512,
        **extra: Any,
    ) -> str:
        if self._cached_model is None:
            health = await self.healthcheck()
            if not health.runnable or self._cached_model is None:
                raise InferenceUnavailable(
                    f"inference server unavailable at {self.url}: {health.detail}"
                )
        payload: dict[str, Any] = {
            "model": self._cached_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            **extra,
        }
        body = await asyncio.to_thread(self._request, "/chat/completions", "POST", payload)
        choices = body.get("choices") or []
        if not choices:
            raise InferenceUnavailable(f"chat completion returned no choices: {body!r}")
        content = (choices[0].get("message") or {}).get("content")
        if not content:
            raise InferenceUnavailable("chat completion returned empty content")
        return content.strip()
