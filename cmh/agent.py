"""Two harnesses that answer a question: one-shot with a memory's context, or a tool-using agent."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from .memory import Memory, RetrievalMemory
from .models import ModelClient, Reply

ANSWER_SYSTEM = (
    "You answer questions about a bank of commercial contracts for a legal team. Use only the material "
    "provided. Amendments control over the contracts they amend. If the material does not contain the "
    "answer, say it is not there; do not guess. Reply with JSON only."
)
ANSWER_FORMAT = (
    'Reply with exactly one JSON object: {"found": true|false, "answer": "<one or two sentences with the '
    'exact figures>", "citations": ["<document id>", ...]}. "found" is false when the contracts do not '
    "contain the term asked about. Document ids are the names after DOCUMENT or NOTES (for example corio, "
    "bnl_amendment_1); for chunks, use the part before #."
)


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"found": None, "answer": text.strip(), "citations": [], "parse_error": True}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"found": None, "answer": text.strip(), "citations": [], "parse_error": True}


@dataclass
class Trace:
    qid: str
    config: str
    output: dict
    calls: list[Reply] = field(default_factory=list)
    context_tokens: int = 0
    steps: list[dict] = field(default_factory=list)
    note: str = ""

    def totals(self) -> dict:
        return {
            "calls": len(self.calls),
            "context_tokens": self.context_tokens,
            "billed_input_tokens": sum(c.billed_input for c in self.calls),
            "output_tokens": sum(c.output_tokens for c in self.calls),
            "latency_s": round(sum(c.latency_s for c in self.calls), 2),
            "cost_usd": round(sum(c.cost_usd for c in self.calls), 5),
        }


class OneShot:
    """Put the memory's context in the prompt and ask once."""

    def __init__(self, memory: Memory, client: ModelClient):
        self.memory, self.client = memory, client
        self.name = memory.name

    def answer(self, qid: str, question: str) -> Trace:
        ctx = self.memory.context(question)
        prompt = f"{ctx.text}\n\nQUESTION: {question}\n\n{ANSWER_FORMAT}"
        r = self.client.complete(ANSWER_SYSTEM, prompt)
        return Trace(qid, self.name, parse_json(r.text), [r], ctx.tokens, note=ctx.note)


AGENT_SYSTEM = ANSWER_SYSTEM.replace("Use only the material provided.",
                                     "Use the search and read tools to find the material; use only what you read.")
AGENT_TOOLS = """You can take one action per turn. Reply with JSON only, one of:
{"action": "search", "query": "<keywords>"}      returns the 5 best-matching chunk ids with a short preview
{"action": "read", "chunk": "<chunk id>"}         returns the full chunk text
{"action": "answer", "found": true|false, "answer": "...", "citations": ["<document id>", ...]}
Documents in the bank: {docs}
Search for amendments too: an amendment changes the contract it amends. You have {steps} turns; on the last turn you must answer."""


class ToolAgent:
    """A small tool-using agent: search and read chunks, then answer. Each turn is one model call
    with the whole transcript so far, so tokens grow with the number of turns."""

    name = "agent"

    def __init__(self, memory: RetrievalMemory, client: ModelClient, max_steps: int = 6):
        self.memory, self.client, self.max_steps = memory, client, max_steps

    def answer(self, qid: str, question: str) -> Trace:
        docs = ", ".join(f"{d.doc_id} ({d.title})" for d in self.memory.corpus.docs.values())
        head = AGENT_TOOLS.replace("{docs}", docs).replace("{steps}", str(self.max_steps))
        transcript: list[str] = []
        trace = Trace(qid, self.name, {})
        for step in range(1, self.max_steps + 1):
            last = step == self.max_steps
            prompt = (f"{head}\n\nQUESTION: {question}\n\n" + "\n\n".join(transcript) +
                      f"\n\nTurn {step} of {self.max_steps}." + (" Answer now." if last else ""))
            r = self.client.complete(AGENT_SYSTEM, prompt)
            trace.calls.append(r)
            trace.context_tokens += max(1, round(len(prompt) / 4))
            act = parse_json(r.text)
            kind = act.get("action", "answer" if "found" in act else None)
            if kind == "search" and not last:
                ids = self.memory.search(str(act.get("query", "")), 5)
                obs = "\n".join(f"{i}: {self.memory.chunks[i].text[:160]}..." for i in ids) or "no results"
                trace.steps.append({"search": act.get("query"), "results": ids})
                transcript.append(f"ACTION: search {json.dumps(act.get('query'))}\nRESULT:\n{obs}")
            elif kind == "read" and not last:
                cid = str(act.get("chunk", ""))
                obs = self.memory.render(cid) if cid in self.memory.chunks else f"unknown chunk {cid}"
                trace.steps.append({"read": cid})
                transcript.append(f"ACTION: read {cid}\nRESULT:\n{obs}")
            else:
                act.pop("action", None)
                trace.output = act
                break
        if not trace.output:
            trace.output = {"found": None, "answer": "", "citations": [], "parse_error": True}
        return trace
