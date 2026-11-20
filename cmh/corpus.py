"""Load the contract bank and split it into chunks."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


def count_tokens(text: str) -> int:
    """Rough token count used for every config alike (about 4 characters per token).

    The harness also records the token counts the model provider reports. This
    estimate exists so context sizes can be compared without calling a model.
    """
    return max(1, round(len(text) / 4))


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    parties: str = ""
    date: str = ""
    aliases: list[str] = field(default_factory=list)
    amends: str | None = None  # parent doc_id when this document is an amendment

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]

    def header(self) -> str:
        if self.amends:
            return f"=== DOCUMENT {self.doc_id} (amends {self.amends}): {self.title}, {self.date} ==="
        return f"=== DOCUMENT {self.doc_id}: {self.title}, {self.parties}, {self.date} ==="


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    text: str


class Corpus:
    def __init__(self, docs: list[Document]):
        self.docs = {d.doc_id: d for d in docs}

    @classmethod
    def load(cls, with_amendments: bool = False, data_dir: Path = DATA) -> "Corpus":
        idx = json.loads((data_dir / "contracts" / "index.json").read_text())
        docs = [
            Document(k, v["title"], (data_dir / "contracts" / f"{k}.txt").read_text(),
                     v["parties"], v["date"], v["aliases"])
            for k, v in idx.items()
        ]
        if with_amendments:
            aidx = json.loads((data_dir / "amendments" / "index.json").read_text())
            for k, v in aidx.items():
                parent = idx[v["amends"]]
                docs.append(Document(k, v["title"], (data_dir / "amendments" / f"{k}.txt").read_text(),
                                     parent["parties"], v["date"], parent["aliases"], amends=v["amends"]))
        return cls(docs)

    def family(self, doc_id: str) -> list[Document]:
        """A contract plus every amendment to it."""
        return [d for d in self.docs.values() if d.doc_id == doc_id or d.amends == doc_id]

    def base_ids(self) -> list[str]:
        return [d.doc_id for d in self.docs.values() if not d.amends]

    def full_text(self) -> str:
        return "\n\n".join(f"{d.header()}\n{d.text}" for d in self.docs.values())

    def chunks(self, words: int = 350, overlap: int = 50) -> list[Chunk]:
        out = []
        for d in self.docs.values():
            toks = re.findall(r"\S+", d.text)
            step = words - overlap
            for n, start in enumerate(range(0, max(1, len(toks)), step)):
                piece = " ".join(toks[start:start + words])
                if piece:
                    out.append(Chunk(f"{d.doc_id}#{n}", d.doc_id, piece))
                if start + words >= len(toks):
                    break
        return out

    def mentioned(self, question: str) -> list[str]:
        """Base contracts a question names (by party or contract alias)."""
        q = question.lower()
        return [d.doc_id for d in self.docs.values()
                if not d.amends and any(a in q for a in d.aliases)]
