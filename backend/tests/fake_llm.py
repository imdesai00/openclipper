"""A scriptable, real HTTP server that speaks enough of the OpenAI protocol for resilience tests.
It records every request, so tests can assert what was (or was not) sent and which credentials arrived."""

from __future__ import annotations

import http.server
import json
import threading
from collections import deque
from typing import Any


class FakeLLM:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.script: deque[tuple[int, Any, dict[str, str]]] = deque()  # (status, body, headers) used before `default`
        self.default: tuple[int, Any, dict[str, str]] = (200, self.chat_body("ok"), {})
        self.models: list[dict[str, Any]] = [{"id": "fake-model"}]
        self.echo_auth_in_errors = False
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _reply(self, status: int, body: Any, headers: dict[str, str]) -> None:
                raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(raw)

            def _record(self) -> dict[str, Any]:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                rec = {
                    "method": self.command,
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": body,
                }
                outer.requests.append(rec)
                return rec

            def do_GET(self) -> None:  # noqa: N802
                rec = self._record()
                if rec["path"].endswith("/models"):
                    self._reply(200, {"data": outer.models}, {})
                else:
                    self._reply(404, {"error": "nope"}, {})

            def do_POST(self) -> None:  # noqa: N802
                rec = self._record()
                status, body, headers = outer.script.popleft() if outer.script else outer.default
                if status >= 400 and outer.echo_auth_in_errors:
                    body = {"error": {"message": f"bad request, you sent Authorization: {rec['auth']}"}}
                self._reply(status, body, headers)

            def log_message(self, *a: object) -> None:
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    @staticmethod
    def chat_body(text: str, tin: int = 10, tout: int = 5) -> dict[str, Any]:
        return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": tin, "completion_tokens": tout}}  # fmt: skip

    def chats(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["method"] == "POST"]

    def close(self) -> None:
        self.server.shutdown()
