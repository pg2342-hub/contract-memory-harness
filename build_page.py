"""Build docs/index.html (the GitHub Pages demo) from runs/latest/results.json.

    python docs/build_page.py [runs/latest]
"""
from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LABELS = {
    "long_context": ("Long context", "All five contracts pasted into every prompt"),
    "retrieval": ("Retrieval", "Top 6 matching passages (BM25) in the prompt"),
    "agent": ("Tool agent", "Searches and reads passages over several turns, then answers"),
    "study_notes": ("Precomputed memory", "Compact notes written once per contract, offline; only the notes go in the prompt"),
    "notes_stale": ("Memory, not rebuilt", "Notes from before the amendments"),
    "notes_guarded": ("Memory with stale guard", "Notes, but questions on a changed contract fall back to retrieval"),
    "notes_rebuilt": ("Memory, rebuilt", "Notes rebuilt for the four changed contracts only"),
}
KIND = {"fact": "Single fact", "absent": "Not in contract", "cross": "Across contracts", "changed": "Changed by amendment"}

CSS = """
main{padding-bottom:24px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin:40px 0 8px}
.tile{border:1px solid var(--rule);border-radius:16px;padding:18px 20px;font-size:14px;color:var(--ink-2)}
.tile b{display:block;font:400 32px/1.15 var(--serif);color:var(--ink);margin-bottom:6px;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto;margin:16px 0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.res{display:inline-flex;align-items:center;min-height:24px;padding:0 10px;border-radius:999px;font:500 12px/1 var(--mono);
letter-spacing:.03em;white-space:nowrap;background:var(--green-wash);color:var(--green)}
.res.fail{background:var(--ink);color:var(--paper)}
summary{flex-wrap:wrap;column-gap:8px}
details p{margin-top:10px;font-size:15px}
label select{margin-left:6px}
code{background:rgb(27 26 23/.05);padding:1px 5px;border-radius:4px}
p.muted{font-size:15px;max-width:46em}
"""


def pct(a, b):
    return f"{round(100 * a / b)}%" if b else "-"


def summary_table(part: dict, order: list[str]) -> str:
    rows = []
    for c in order:
        if c not in part:
            continue
        s = part[c]["summary"]
        name, desc = LABELS[c]
        kinds = " · ".join(f"{KIND[k]} {v}" for k, v in s["by_kind"].items())
        rows.append(
            f"<tr><td><b>{name}</b><br><span class=muted>{desc}</span></td>"
            f"<td class=n><b>{s['passed']}/{s['total']}</b><br><span class=muted>{kinds}</span></td>"
            f"<td class=n>{s['fabricated']}</td><td class=n>{s['stale']}</td>"
            f"<td class=n>{s['mean_context_tokens']:,.0f}</td><td class=n>{s['mean_billed_input_tokens']:,.0f}</td>"
            f"<td class=n>{s['mean_latency_s']}s</td><td class=n>{s['mean_calls']}</td></tr>")
    return ("<div class=wrap><table><tr><th>Config</th><th class=n>Correct</th><th class=n>Made up</th>"
            "<th class=n>Stale</th><th class=n>Context tokens / question</th><th class=n>Billed input tokens / question</th>"
            "<th class=n>Latency / question</th><th class=n>Model calls</th></tr>" + "".join(rows) + "</table></div>")


def explorer(part: dict, order: list[str], pid: str, default: str) -> str:
    opts = "".join(f"<option value='{c}'{' selected' if c == default else ''}>{LABELS[c][0]}</option>"
                   for c in order if c in part)
    blocks = []
    for c in order:
        if c not in part:
            continue
        items = []
        for r in part[c]["rows"]:
            g = r["grade"]
            failed = [k.replace("_", " ") for k, v in g["checks"].items() if not v]
            pill = f"<span class='res{'' if g['pass'] else ' fail'}'>{g['label']}</span>"
            o = r["output"]
            steps = ""
            if r.get("steps"):
                steps = "<br><span class=muted>Agent steps: " + html.escape(
                    "; ".join(f"search “{s['search']}”" if "search" in s else f"read {s['read']}" for s in r["steps"])) + "</span>"
            note = f"<br><span class=muted>{html.escape(r['note'])}</span>" if r.get("note") else ""
            items.append(
                f"<details><summary>{pill} <b>{r['id']}</b> · {KIND[r['kind']]} · {html.escape(r['question'])}</summary>"
                f"<p><b>Answer:</b> {html.escape(str(o.get('answer', '')))}<br>"
                f"<span class=muted>found={o.get('found')} · cites {html.escape(', '.join(map(str, o.get('citations') or [])) or 'nothing')}"
                f" · {r['totals']['context_tokens']:,} context tokens · {r['totals']['latency_s']}s</span>"
                + (f"<br><span class=muted>Failed checks: {', '.join(failed)}</span>" if failed else "") + steps + note + "</p></details>")
        blocks.append(f"<div class='ex-{pid}' data-c='{c}' style='display:none'>{''.join(items)}</div>")
    return (f"<p><label>Show answers for <select onchange=\"show('{pid}',this.value)\">{opts}</select></label></p>"
            + "".join(blocks))


def build(run_dir: Path) -> str:
    R = json.loads((run_dir / "results.json").read_text())
    p1, p2, st = R.get("part1", {}), R.get("part2", {}), R.get("staleness", {})
    o1 = ["long_context", "retrieval", "agent", "study_notes"]
    o2 = ["long_context", "retrieval", "agent", "notes_stale", "notes_guarded", "notes_rebuilt"]
    lc, nt = p1.get("long_context", {}).get("summary", {}), p1.get("study_notes", {}).get("summary", {})
    ratio = (lc.get("mean_context_tokens", 0) / nt["mean_context_tokens"]) if nt.get("mean_context_tokens") else 0
    tiles = (
        f"<div class=tiles><div class=tile><b>{R['corpus']['bank_tokens']:,}</b>tokens in the contract bank (5 CUAD contracts)</div>"
        f"<div class=tile><b>{lc.get('passed', '-')}/25 vs {nt.get('passed', '-')}/25</b>long context vs precomputed memory</div>"
        f"<div class=tile><b>{ratio:.0f}x</b>fewer context tokens per question with the precomputed memory</div>"
        f"<div class=tile><b>{p2.get('notes_stale', {}).get('summary', {}).get('stale', '-')}/5</b>answers went stale after amendments until the memory was rebuilt</div></div>")
    model = html.escape(R.get("model", ""))
    rebuilt = ", ".join(st.get("rebuilt", [])) or "-"
    def fails(c):
        return [r for r in p1.get(c, {}).get("rows", []) if not r["grade"]["pass"]]
    def ids(c):
        return ", ".join(r["id"] for r in fails(c)) or "none"
    def said_absent(c):
        f = [r for r in fails(c) if r["kind"] == "fact"]
        return len(f), sum(1 for r in f if r["output"].get("found") is False)
    rf, ra = said_absent("retrieval")
    af, aa = said_absent("agent")
    shows = (f"<p><b>What it shows.</b> The precomputed memory scored {nt.get('passed', '-')}/25 against long context's "
             f"{lc.get('passed', '-')}/25 (misses: memory {ids('study_notes')}; long context {ids('long_context')}) with about "
             f"1/{ratio:.0f} of the context per question, after a one-time offline build of {nt.get('build_tokens', 0):,} tokens. "
             f"Retrieval and the tool agent used the least context per question but missed {rf} and {af} single facts; "
             f"{ra} and {aa} of those misses said the term was not in what they found, and no config made up a term.</p>")
    body = f"""
<h1>Contract memory harness</h1>
<p>A small agent harness and eval for one question: when an agent works over a firm's contract bank, does a memory
built ahead of time answer as well as rereading the contracts, at a fraction of the tokens, and what happens when a
contract is amended? Five public contracts from CUAD, 25 questions, a tool-using agent, and a plug-in slot where a
learned memory (such as an Engram) would go.</p>
<p class=muted>Model: {model} (served as claude-sonnet-5-5; Claude Code in print mode, no tools, the harness's own system prompt). Every model call is recorded in <code>runs/latest/calls.jsonl</code>, so the grades below can be
re-checked with <code>python -m cmh.run --replay runs/latest</code> and no API key. Code:
<a href="https://github.com/pg2342-hub/contract-memory-harness">github.com/pg2342-hub/contract-memory-harness</a></p>
{tiles}
<h2>1. Answer quality and cost on the contract bank</h2>
<p>25 questions: 18 single facts, 4 terms the contract does not contain (the right answer is “not there”), and 3 that
need several contracts. Grading is a fixed rubric (exact figure present, right contract cited, no made-up terms), not a
model judging a model. Figures must match as whole numbers, so “5,000” does not match inside “4,500,000”. Citing a
contract that the answer checked and ruled out is not penalized; the first version of the rubric did penalize it and
failed four correct answers. Cross-contract answers are graded on recall only (every right contract named), not on
precision.</p>
<p class=muted>Changes since the first published run, which is kept in <code>runs/v1_topic_list_notes</code>: the
note-writing prompt no longer lists the clause types the questions ask about (it now asks generically for the terms a
lawyer would be likely to ask about), and the grader now matches whole figures. The numbers below are
from a fresh live run with both changes in place; it is a single run, so a one-question difference is within run-to-run noise. The grades shown are the recorded
answers re-graded with the final grader, which changes only q15 (fail to pass in long context, retrieval and the memory); the scores the live run reported are in <code>runs/latest/results_as_recorded.json</code>.</p>
{summary_table(p1, o1)}
{shows}
<p class=muted>Context tokens are counted by the harness (about 4 characters per token) for what each config puts in
the prompt. Billed input tokens are what the model provider reported, including its own fixed prompt overhead and cached reads.
The precomputed memory also spent {nt.get('build_tokens', 0):,} tokens once, offline, to write its notes.</p>
{explorer(p1, o1, 'p1', 'long_context')}
<h2>2. When a contract changes</h2>
<p>Four amendments (written for this test and labeled as such) change five answers: a longer term, a new governing
law, a shorter termination notice, and two minimum fees. A memory built before the change still answers from the old
terms. The harness detects which contracts changed by comparing document hashes with the ones the memory was built
from, then either guards (sends questions on changed contracts to retrieval) or rebuilds only those contracts. The rule this suggests for a deployment: until a memory is rebuilt, route any question
on a contract family that changed since the last build to the current path. The guard was exercised only on the five
changed questions, which all routed to retrieval; its routing for unchanged contracts after an amendment is untested.</p>
<p><b>Contracts flagged stale:</b> {', '.join(st.get('stale_after_amendments', [])) or '-'}. <b>Rebuilt:</b> {rebuilt},
for {st.get('rebuild_tokens', 0):,} tokens (the full build was {st.get('full_build_tokens', 0):,}).</p>
{summary_table(p2, o2)}
{explorer(p2, o2, 'p2', 'notes_stale')}
<h2>3. Plugging in a learned memory</h2>
<p>Every config implements the same small interface in <code>cmh/memory.py</code>: <code>build(corpus)</code> runs
offline, <code>context(question)</code> returns what goes in the prompt, and <code>stale_docs(corpus)</code> says which
contracts the memory no longer matches. <code>EngramMemory</code> is the empty slot: with access, build() would train or
point at an engram for the bank, context() would send little or nothing, and the same 30 questions would score it next
to the baselines on accuracy, tokens and latency.</p>
<h2>How to run</h2>
<p><code>python -m cmh.run --model claude-cli:sonnet</code> (Claude Code in print mode) or <code>--model api:claude-sonnet-5-5</code>
with <code>ANTHROPIC_API_KEY</code>; <code>python -m unittest discover -s tests -t .</code> runs the tests with no model.</p>
<p class=muted>Contracts: CUAD v1 (The Atticus Project), CC BY 4.0. The amendments are illustrative and not real filings.
Built by Pranav Gudise.</p>
"""
    js = "<script>function show(p,c){document.querySelectorAll('.ex-'+p).forEach(e=>e.style.display=e.dataset.c==c?'block':'none')}show('p1','long_context');show('p2','notes_stale')</script>"
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Contract memory harness</title>"
            f"<style>{CSS}</style></head><body><main>{body}</main>{js}</body></html>")


if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "latest"
    (ROOT / "docs" / "index.html").write_text(build(run))
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_chrome.py"), str(ROOT / "docs" / "index.html"),
                    "--repo", "contract-memory-harness"], check=True)
    (ROOT / "docs" / ".nojekyll").write_text("")
    print("wrote docs/index.html")
