"""Tiny OpenAI-compatible HTTP server used by tests instead of llama-server.

Spawns on an ephemeral port; returns canned /v1/models and /v1/chat/completions
JSON. Each chat call returns a deterministic response derived from the last
user message so assertions can pin specific text.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    server_version = "FakeInference/1.0"

    def log_message(self, format: str, *args) -> None:  # silence stderr in tests
        return

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/v1/models":
            self._send(
                200,
                {
                    "object": "list",
                    "data": [{"id": "fake-bitnet-2b", "object": "model", "created": 0}],
                },
            )
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            self._send(400, {"error": "invalid json"})
            return

        if self.path == "/v1/chat/completions":
            messages = payload.get("messages") or []
            user_text = ""
            for msg in messages:
                if msg.get("role") == "user":
                    user_text = msg.get("content") or ""
            system_text = "\n".join(
                m.get("content", "") for m in messages if m.get("role") == "system"
            ).lower()
            if "reflection question" in system_text:
                content = (
                    "- What is currently blocking progress?\n"
                    "- What is the next concrete action?\n"
                    "- How will you know this work is complete?"
                )
            elif "standup summary" in system_text:
                content = "- summary line one\n- summary line two"
            elif user_text:
                content = f"- echoed: {user_text[:60]}"
            else:
                content = "- placeholder bullet"
            self._send(
                200,
                {
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload.get("model") or "fake-bitnet-2b",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                },
            )
            return
        self._send(404, {"error": "not found"})


class FakeInferenceServer:
    def __init__(self) -> None:
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 0

    def start(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"
