"""Deterministic rubric. No model judges another model here, so every grade can be re-checked by hand.

Per question kind:
  fact     found=true, every required fact present (any listed wording), cites the right contract
  absent   found=false (the contract has no such term); answering with a term counts as fabricated
  cross    every required fact present, cites every expected contract. Citing contracts the answer
           checked and ruled out is not penalized (rubric v2; v1 failed correct answers for it).
  changed  the amended value is present; "stale" when only the old value appears
"""
from __future__ import annotations

import re


def norm(s: str) -> str:
    s = s.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)  # 4,500,000 -> 4500000
    return re.sub(r"\s+", " ", s)


def has_any(text: str, alts: list[str]) -> bool:
    t = norm(text)
    return any(norm(a) in t for a in alts)


def cited_bases(citations) -> set[str]:
    out = set()
    for c in citations or []:
        c = str(c).lower().split("#")[0].strip()
        out.add(c.split("_amendment")[0])
    return out


def grade(q: dict, output: dict) -> dict:
    ans = str(output.get("answer", ""))
    found = output.get("found")
    cites = cited_bases(output.get("citations"))
    checks: dict[str, bool] = {}
    kind = q["kind"]
    if output.get("parse_error"):
        checks["valid_json"] = False
    if kind in ("fact", "cross", "changed"):
        checks["found"] = found is True
        for i, alts in enumerate(q["must"]):
            checks[f"fact_{i + 1}"] = has_any(ans, alts)
        checks["cites_expected"] = set(q["docs"]) <= cites
    elif kind == "absent":
        checks["says_not_found"] = found is False
    passed = all(checks.values())
    label = "pass" if passed else "fail"
    if kind == "absent" and found is True:
        label = "fabricated"
    if kind == "changed" and not passed and has_any(ans, q["old"]) and not has_any(ans, q["must"][0]):
        label = "stale"
    return {"pass": passed, "label": label, "checks": checks}
