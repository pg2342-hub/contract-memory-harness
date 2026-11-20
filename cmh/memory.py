"""Memory backends. Each one decides what context the model sees for a question.

Every backend implements the same interface, so a learned memory (for example an
Engram endpoint) can be dropped in and scored on the same questions:

    build(corpus, client)   offline step, run before any question is asked
    context(question)       -> Context(text, tokens, sources) sent with the question
    stale_docs(corpus)      -> contracts whose memory no longer matches the documents
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .bm25 import BM25
from .corpus import Corpus, count_tokens
from .models import ModelClient


@dataclass
class Context:
    text: str
    sources: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def tokens(self) -> int:
        return count_tokens(self.text) if self.text else 0


class Memory:
    name = "base"
    build_tokens = 0  # tokens spent in the offline step

    def build(self, corpus: Corpus, client: ModelClient | None = None) -> None:
        self.corpus = corpus

    def context(self, question: str) -> Context:  # pragma: no cover
        raise NotImplementedError

    def stale_docs(self, corpus: Corpus) -> list[str]:
        return []


class FullContextMemory(Memory):
    """Baseline 1: paste every document into the prompt (what agents do when they reread files)."""
    name = "long_context"

    def context(self, question: str) -> Context:
        return Context(self.corpus.full_text(), sources=list(self.corpus.docs))


class RetrievalMemory(Memory):
    """Baseline 2: BM25 over 350-word chunks, top k chunks in the prompt."""
    name = "retrieval"

    def __init__(self, k: int = 6):
        self.k = k

    def build(self, corpus: Corpus, client: ModelClient | None = None) -> None:
        super().build(corpus, client)
        self.chunks = {c.chunk_id: c for c in corpus.chunks()}
        self.index = BM25([(c.chunk_id, c.text) for c in self.chunks.values()])

    def search(self, query: str, k: int | None = None) -> list[str]:
        return [cid for cid, _ in self.index.search(query, k or self.k)]

    def render(self, chunk_id: str) -> str:
        c = self.chunks[chunk_id]
        return f"--- {chunk_id} ({self.corpus.docs[c.doc_id].header().strip('= ')}) ---\n{c.text}"

    def context(self, question: str) -> Context:
        ids = self.search(question)
        return Context("\n\n".join(self.render(i) for i in ids), sources=ids)


NOTES_SYSTEM = (
    "You write compact reference notes about one commercial contract so a colleague can answer "
    "questions later without the contract. Be exact and complete on numbers, dates, durations and names."
)
NOTES_PROMPT = """Write reference notes on the contract below (and any amendments to it, which control over the
original) so a colleague can answer questions about it later without the contract. Cover whatever terms a lawyer
would be likely to ask about. Quote exact numbers, durations, dollar amounts, dates, names and jurisdictions.
Keep it under 500 words.

{docs}"""


class StudyNotesMemory(Memory):
    """A text stand-in for a learned memory: the model studies each contract once, offline, and
    later questions see only the compact notes. It is cheap per question, and it goes stale when
    the documents change until it is rebuilt. Engram trains this kind of memory into model weights;
    this class only mimics the shape (study ahead of time, small context at question time)."""
    name = "study_notes"

    def __init__(self):
        self.notes: dict[str, str] = {}
        self.built_from: dict[str, list[str]] = {}

    def _study(self, corpus: Corpus, doc_id: str, client: ModelClient) -> None:
        fam = corpus.family(doc_id)
        docs = "\n\n".join(f"{d.header()}\n{d.text}" for d in fam)
        r = client.complete(NOTES_SYSTEM, NOTES_PROMPT.format(docs=docs))
        self.notes[doc_id] = r.text.strip()
        self.built_from[doc_id] = sorted(d.sha for d in fam)
        self.build_tokens += r.billed_input + r.output_tokens

    def build(self, corpus: Corpus, client: ModelClient | None = None) -> None:
        super().build(corpus, client)
        for doc_id in corpus.base_ids():
            self._study(corpus, doc_id, client)

    def stale_docs(self, corpus: Corpus) -> list[str]:
        return [d for d in corpus.base_ids()
                if sorted(x.sha for x in corpus.family(d)) != self.built_from.get(d)]

    def refresh(self, corpus: Corpus, client: ModelClient) -> list[str]:
        """Rebuild notes only for contracts whose documents changed."""
        stale = self.stale_docs(corpus)
        for d in stale:
            self._study(corpus, d, client)
        self.corpus = corpus
        return stale

    def context(self, question: str) -> Context:
        parts = [f"=== NOTES {d}: {self.corpus.docs[d].title}, {self.corpus.docs[d].parties} ===\n{n}"
                 for d, n in self.notes.items()]
        return Context("\n\n".join(parts), sources=list(self.notes))

    def to_json(self) -> dict:
        return {"notes": self.notes, "built_from": self.built_from, "build_tokens": self.build_tokens}

    @classmethod
    def from_json(cls, data: dict) -> "StudyNotesMemory":
        m = cls()
        m.notes, m.built_from, m.build_tokens = data["notes"], data["built_from"], data.get("build_tokens", 0)
        return m


class GuardedMemory(Memory):
    """Deployment guard: answer from the precomputed memory unless the question touches a contract
    whose memory is stale, in which case fall back to retrieval over the current documents until
    the memory is rebuilt."""
    name = "notes_guarded"

    def __init__(self, primary: StudyNotesMemory, fallback: RetrievalMemory):
        self.primary, self.fallback = primary, fallback

    def build(self, corpus: Corpus, client: ModelClient | None = None) -> None:
        super().build(corpus, client)
        self.primary.corpus = corpus
        self.fallback.build(corpus)
        self.stale = set(self.primary.stale_docs(corpus))

    def context(self, question: str) -> Context:
        named = set(self.corpus.mentioned(question))
        hit = named & self.stale
        if hit or not named and self.stale:
            ctx = self.fallback.context(question)
            ctx.note = f"fallback: memory stale for {', '.join(sorted(hit or self.stale))}"
            return ctx
        return self.primary.context(question)


class EngramMemory(Memory):
    """Slot for a learned memory served by Engram. Not runnable without access.

    To plug it in: build() would send the contract bank for training (or point at an existing
    engram id), context() would return an empty or tiny prompt because the knowledge lives in the
    model, and the client would be the Engram-served model. stale_docs() would compare the document
    hashes the engram was trained on with the current ones, same as StudyNotesMemory does.
    """
    name = "engram"

    def build(self, corpus: Corpus, client: ModelClient | None = None) -> None:
        raise NotImplementedError("Engram access needed: see README, 'Plugging in a learned memory'")

    def context(self, question: str) -> Context:
        raise NotImplementedError
