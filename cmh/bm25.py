"""Small BM25 index, no dependencies."""
from __future__ import annotations

import math
import re
from collections import Counter

STOP = set("the a an of to and or in for on by with is are be any this that as at from it its shall such other which under each party parties agreement".split())


def terms(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9$%.,]+", text.lower().replace(",", "")) if t not in STOP]


class BM25:
    def __init__(self, items: list[tuple[str, str]], k1: float = 1.4, b: float = 0.75):
        self.ids = [i for i, _ in items]
        self.texts = dict(items)
        self.tf = [Counter(terms(t)) for _, t in items]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(items)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        self.k1, self.b = k1, b

    def search(self, query: str, k: int = 5) -> list[tuple[str, float]]:
        q = terms(query)
        scores = []
        for i, c in enumerate(self.tf):
            s = 0.0
            for t in q:
                if t in c:
                    f = c[t]
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg))
            scores.append((self.ids[i], s))
        scores.sort(key=lambda x: -x[1])
        return [x for x in scores[:k] if x[1] > 0]
