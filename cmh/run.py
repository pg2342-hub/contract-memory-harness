"""Run the eval.

  python -m cmh.run --model claude-cli:sonnet --out runs/latest      live run, records every call
  python -m cmh.run --replay runs/latest                              re-grade recorded calls, no model needed

Part 1 asks 25 questions of the original contract bank with four configs.
Part 2 adds four amendments that change five answers, then asks those five questions again with the
baselines and three versions of the precomputed memory: stale, guarded, and rebuilt.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .agent import OneShot, ToolAgent, Trace
from .corpus import DATA, Corpus
from .grade import grade
from .memory import FullContextMemory, GuardedMemory, RetrievalMemory, StudyNotesMemory
from .models import ModelClient, Recorder, Replay, make_client

PART1 = ["long_context", "retrieval", "agent", "study_notes"]
PART2 = ["long_context", "retrieval", "agent", "notes_stale", "notes_guarded", "notes_rebuilt"]


def load_questions(name: str) -> list[dict]:
    return json.loads((DATA / name).read_text())


def run_set(harnesses: dict, questions: list[dict], workers: int, log) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for cname, h in harnesses.items():
        def one(q):
            t: Trace = h.answer(q["id"], q["question"])
            g = grade(q, t.output)
            return {"id": q["id"], "kind": q["kind"], "question": q["question"], "output": t.output,
                    "grade": g, "totals": t.totals(), "steps": t.steps, "note": t.note}
        with ThreadPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(one, questions))
        out[cname] = rows
        ok = sum(r["grade"]["pass"] for r in rows)
        log(f"  {cname:14s} {ok}/{len(rows)} pass")
    return out


def summarize(rows: list[dict], build_tokens: int = 0) -> dict:
    n = len(rows)
    kinds = {}
    for r in rows:
        k = kinds.setdefault(r["kind"], [0, 0])
        k[0] += r["grade"]["pass"]
        k[1] += 1
    mean = lambda key: round(statistics.mean(r["totals"][key] for r in rows), 1) if rows else 0
    return {
        "passed": sum(r["grade"]["pass"] for r in rows), "total": n,
        "by_kind": {k: f"{v[0]}/{v[1]}" for k, v in kinds.items()},
        "fabricated": sum(r["grade"]["label"] == "fabricated" for r in rows),
        "stale": sum(r["grade"]["label"] == "stale" for r in rows),
        "mean_context_tokens": mean("context_tokens"),
        "mean_billed_input_tokens": mean("billed_input_tokens"),
        "mean_output_tokens": mean("output_tokens"),
        "mean_latency_s": round(statistics.mean(r["totals"]["latency_s"] for r in rows), 2) if rows else 0,
        "mean_calls": mean("calls"),
        "total_cost_usd": round(sum(r["totals"]["cost_usd"] for r in rows), 4),
        "build_tokens": build_tokens,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-cli:sonnet")
    ap.add_argument("--out", default="runs/latest")
    ap.add_argument("--replay", help="run directory with calls.jsonl to re-grade without a model")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--part", choices=["1", "2", "both"], default="both")
    args = ap.parse_args(argv)

    out_dir = Path(args.replay or args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.replay:
        client: ModelClient = Replay([out_dir / "calls.jsonl"])
    else:
        client = Recorder(make_client(args.model), out_dir / "calls.jsonl")
    log = lambda m: print(m, flush=True)

    base = Corpus.load(with_amendments=False)
    changed = Corpus.load(with_amendments=True)
    results = {"model": client.name, "corpus": {
        "contracts": {d.doc_id: {"title": d.title, "parties": d.parties, "tokens": round(len(d.text) / 4)}
                      for d in base.docs.values()},
        "bank_tokens": round(len(base.full_text()) / 4),
        "amendments": [d.doc_id for d in changed.docs.values() if d.amends]}}

    # offline step for the precomputed memory (on the original bank)
    log("building study notes on the original bank ...")
    notes = StudyNotesMemory()
    notes.build(base, client)
    (out_dir / "notes_v1.json").write_text(json.dumps(notes.to_json(), indent=1))
    build_v1 = notes.build_tokens

    if args.part in ("1", "both"):
        log("part 1: 25 questions, original bank")
        lc, rt, rt_agent = FullContextMemory(), RetrievalMemory(), RetrievalMemory()
        for m in (lc, rt, rt_agent):
            m.build(base)
        h = {"long_context": OneShot(lc, client), "retrieval": OneShot(rt, client),
             "agent": ToolAgent(rt_agent, client), "study_notes": OneShot(notes, client)}
        rows = run_set(h, load_questions("questions.json"), args.workers, log)
        results["part1"] = {c: {"summary": summarize(r, build_v1 if c == "study_notes" else 0), "rows": r}
                            for c, r in rows.items()}

    if args.part in ("2", "both"):
        log("part 2: amendments added, 5 changed questions")
        stale_list = notes.stale_docs(changed)
        lc, rt, rt_agent, rt_fb = FullContextMemory(), RetrievalMemory(), RetrievalMemory(), RetrievalMemory()
        for m in (lc, rt, rt_agent):
            m.build(changed)
        notes.corpus = changed
        guarded = GuardedMemory(notes, rt_fb)
        guarded.build(changed)
        h = {"long_context": OneShot(lc, client), "retrieval": OneShot(rt, client),
             "agent": ToolAgent(rt_agent, client)}
        stale_h = OneShot(notes, client)
        stale_h.name = "notes_stale"
        h["notes_stale"] = stale_h
        g = OneShot(guarded, client)
        g.name = "notes_guarded"
        h["notes_guarded"] = g
        cq = load_questions("changed_questions.json")
        rows = run_set(h, cq, args.workers, log)
        before = notes.build_tokens
        rebuilt_ids = notes.refresh(changed, client)
        (out_dir / "notes_v2.json").write_text(json.dumps(notes.to_json(), indent=1))
        nr = OneShot(notes, client)
        nr.name = "notes_rebuilt"
        rows.update(run_set({"notes_rebuilt": nr}, cq, args.workers, log))
        results["part2"] = {c: {"summary": summarize(r, (notes.build_tokens - before) if c == "notes_rebuilt" else 0),
                                "rows": r} for c, r in rows.items()}
        results["staleness"] = {
            "stale_after_amendments": stale_list,
            "rebuilt": rebuilt_ids,
            "stale_after_rebuild": notes.stale_docs(changed),
            "rebuild_tokens": notes.build_tokens - before,
            "full_build_tokens": build_v1,
        }

    results["model"] = client.name.removeprefix("replay:")  # the model that made the recorded calls
    (out_dir / "results.json").write_text(json.dumps(results, indent=1))
    log(f"wrote {out_dir / 'results.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
