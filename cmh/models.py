"""Model clients. Every client returns text plus the usage the provider reported.

- ClaudeCLI     runs `claude -p` (Claude Code in print mode). This is how the recorded runs were made.
- AnthropicAPI  calls the Messages API directly when ANTHROPIC_API_KEY is set.
- Recorder      wraps any client and writes every call to calls.jsonl.
- Replay        answers from a calls.jsonl file, so results can be re-graded with no model or key.
- Scripted      a fake model for tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class Reply:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    latency_s: float = 0.0
    cost_usd: float = 0.0
    meta: dict = field(default_factory=dict)

    @property
    def billed_input(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


def call_key(system: str, prompt: str) -> str:
    return hashlib.sha256((system + "\x00" + prompt).encode()).hexdigest()[:20]


class ModelClient:
    name = "base"

    def complete(self, system: str, prompt: str) -> Reply:  # pragma: no cover
        raise NotImplementedError


class ClaudeCLI(ModelClient):
    """Claude Code print mode with its own system prompt and no tools, so only our context is sent."""

    def __init__(self, model: str = "sonnet", binary: str = "claude", timeout: int = 300):
        self.model, self.binary, self.timeout = model, binary, timeout
        self.name = f"claude-cli:{model}"
        default = "--tools '' --strict-mcp-config --setting-sources ''"
        self.extra = shlex.split(os.environ.get("CMH_CLAUDE_ARGS", default))

    def complete(self, system: str, prompt: str) -> Reply:
        cmd = [self.binary, "-p", "--output-format", "json", "--model", self.model,
               "--system-prompt", system, *self.extra]
        t0 = time.time()
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=self.timeout)
        wall = time.time() - t0
        if p.returncode != 0 and not p.stdout.strip():
            raise RuntimeError(f"claude -p failed: {p.stderr[:500]}")
        out = json.loads(p.stdout)
        if out.get("is_error"):
            raise RuntimeError(f"claude -p error: {str(out.get('result'))[:500]}")
        u = out.get("usage", {}) or {}
        return Reply(
            text=out.get("result", ""),
            input_tokens=u.get("input_tokens", 0),
            output_tokens=u.get("output_tokens", 0),
            cache_read_tokens=u.get("cache_read_input_tokens", 0),
            cache_write_tokens=u.get("cache_creation_input_tokens", 0),
            latency_s=round(out.get("duration_api_ms", out.get("duration_ms", wall * 1000)) / 1000, 3),
            cost_usd=out.get("total_cost_usd", 0.0) or 0.0,
            meta={"model_usage": list((out.get("modelUsage") or {}).keys())},
        )


class AnthropicAPI(ModelClient):
    def __init__(self, model: str = "claude-sonnet-5-5", max_tokens: int = 1024):
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        self.model, self.max_tokens = model, max_tokens
        self.name = f"api:{model}"

    def complete(self, system: str, prompt: str) -> Reply:
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "system": system,
                           "messages": [{"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, headers={
            "x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=300) as r:
            out = json.loads(r.read())
        u = out.get("usage", {})
        text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
        return Reply(text, u.get("input_tokens", 0), u.get("output_tokens", 0),
                     u.get("cache_read_input_tokens", 0) or 0, u.get("cache_creation_input_tokens", 0) or 0,
                     round(time.time() - t0, 3))


class Recorder(ModelClient):
    def __init__(self, inner: ModelClient, path: Path, tag: str = ""):
        self.inner, self.path, self.tag = inner, Path(path), tag
        self.name = inner.name
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def complete(self, system: str, prompt: str) -> Reply:
        r = self.inner.complete(system, prompt)
        rec = {"key": call_key(system, prompt), "tag": self.tag, "model": self.inner.name,
               "text": r.text, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
               "cache_read_tokens": r.cache_read_tokens, "cache_write_tokens": r.cache_write_tokens,
               "latency_s": r.latency_s, "cost_usd": r.cost_usd, "meta": r.meta}
        with self.lock, self.path.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        return r


class Replay(ModelClient):
    def __init__(self, paths: list[Path]):
        self.calls: dict[str, dict] = {}
        for p in paths:
            for line in Path(p).read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.calls[rec["key"]] = rec
        self.name = "replay"

    def complete(self, system: str, prompt: str) -> Reply:
        k = call_key(system, prompt)
        if k not in self.calls:
            raise KeyError(f"no recorded call for key {k}")
        c = self.calls[k]
        self.name = f"replay:{c.get('model', '?')}"
        return Reply(c["text"], c["input_tokens"], c["output_tokens"], c["cache_read_tokens"],
                     c["cache_write_tokens"], c["latency_s"], c.get("cost_usd", 0.0), c.get("meta", {}))


class Scripted(ModelClient):
    """Test double: fn(system, prompt) -> text."""

    def __init__(self, fn: Callable[[str, str], str]):
        self.fn = fn
        self.name = "scripted"
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, prompt: str) -> Reply:
        self.calls.append((system, prompt))
        return Reply(self.fn(system, prompt), input_tokens=len(prompt) // 4, output_tokens=20, latency_s=0.01)


def make_client(spec: str) -> ModelClient:
    kind, _, model = spec.partition(":")
    if kind == "claude-cli":
        return ClaudeCLI(model or "sonnet")
    if kind == "api":
        return AnthropicAPI(model or "claude-sonnet-5-5")
    raise ValueError(f"unknown model spec {spec!r} (use claude-cli[:model] or api[:model])")
