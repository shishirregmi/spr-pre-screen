"""Client for a local language model served by Ollama. Nothing is sent outside the machine."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Protocol

from app import config


class LLMClient(Protocol):
    name: str

    def chat_json(self, system: str, user: str, schema: dict) -> dict: ...


class OllamaClient:
    def __init__(self, url: str = config.LLM_URL, model: str = config.LLM_MODEL, timeout: int = config.LLM_TIMEOUT):
        self.url = url.rstrip("/")
        self.name = model
        self.timeout = timeout
        # Reasoning models (e.g. qwen3) "think" before answering by default; off keeps them fast enough here.
        self.think = False if model.startswith(("qwen3", "deepseek-r1")) else None

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(self.url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def available(self) -> tuple[bool, str | None]:
        try:
            with urllib.request.urlopen(self.url + "/api/tags", timeout=5) as resp:
                names = {m["name"] for m in json.loads(resp.read()).get("models", [])}
        except (urllib.error.URLError, OSError, ValueError) as e:
            return False, f"model server not reachable at {self.url}: {e}"
        if self.name not in names and f"{self.name}:latest" not in names:
            return False, f"model {self.name} not downloaded (run: ollama pull {self.name})"
        return True, None

    def chat_json(self, system: str, user: str, schema: dict) -> dict:
        body = {
            "model": self.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,                      # structured output: the reply must match the schema
            # num_predict caps the answer length so one call can never run long
            "options": {"temperature": 0, "num_ctx": 6144, "seed": 7, "num_predict": 700},
        }
        if self.think is not None:
            body["think"] = self.think
        out = self._post("/api/chat", body)
        return json.loads(out["message"]["content"])
