"""Build docs/index.html (the GitHub Pages demo) from runs/latest/results.json.

    python docs/build_page.py [runs/latest]
"""
from __future__ import annotations

import html
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/pg2342-hub/contract-memory-harness"

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
KIND_SHORT = {"fact": "Single facts", "absent": "Not there", "cross": "Across"}

CSS = """
.head h1{margin:40px 0 14px}
.head .lead{max-width:40em}
.verdict{margin:32px 0 0;border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;overflow:hidden}
.vhead{padding:20px 24px;border-bottom:1px solid var(--rule)}
.vhead h2{all:unset;display:block;font:400 30px/1.15 var(--serif);text-wrap:balance}
.vhead p{margin:8px 0 0;color:var(--ink-2);font-size:15px;max-width:52em}
.vhead p b{color:var(--ink)}
.vsub{display:flex;justify-content:space-between;gap:16px;padding:14px 24px 0;font:400 12.5px/1.4 var(--mono);
color:var(--ink-2);letter-spacing:.02em}
.vrow{display:grid;grid-template-columns:minmax(0,14em) minmax(0,1fr) 5.5em 8.5em;align-items:center;gap:20px;
padding:16px 24px}
.vrow+.vrow{border-top:1px solid var(--rule-soft)}
.vrow.win{background:var(--green-wash)}
.vwho b{display:block;font:400 19px/1.2 var(--serif)}
.vwho span{display:block;color:var(--ink-2);font-size:13px;line-height:1.4;margin-top:3px}
.strip{display:grid;gap:10px}
.strip.q25{grid-template-columns:minmax(0,18fr) minmax(0,4fr) minmax(0,3fr)}
.strip.q5{grid-template-columns:minmax(0,5fr) minmax(0,20fr)}
.sg{display:grid;gap:3px}
.sg.k18{grid-template-columns:repeat(18,minmax(0,1fr))}.sg.k4{grid-template-columns:repeat(4,minmax(0,1fr))}
.sg.k3{grid-template-columns:repeat(3,minmax(0,1fr))}.sg.k5{grid-template-columns:repeat(5,minmax(0,1fr))}
.sgl{display:block;font:400 11.5px/1.3 var(--mono);color:var(--ink-2);margin-top:6px;white-space:nowrap}
.cell{display:block;aspect-ratio:1;border-radius:4px;border:1.5px solid transparent;
transition:transform .2s var(--ease),box-shadow .2s}
.cell.pass{background:var(--green)}
.cell.fail{background:var(--paper);border-color:var(--ink)}
.cell.crit{background:var(--ink)}
.cell:hover,.cell:focus-visible{transform:translateY(-3px);box-shadow:0 6px 12px -6px rgb(27 26 23/.5)}
.vscore b{font:400 34px/1 var(--serif);font-variant-numeric:tabular-nums}
.vscore span{color:var(--ink-2);font-size:14px}
.vtok{text-align:right;font-variant-numeric:tabular-nums}
.vtok b{display:block;font:400 22px/1 var(--serif)}
.vtok span{display:block;font-size:12.5px;line-height:1.3;color:var(--ink-2);margin-top:4px}
.vpart{padding:14px 24px 10px;border-top:1px solid var(--rule);font:400 20px/1.2 var(--serif)}
.vpart span{display:block;font:400 14px/1.4 var(--sans);color:var(--ink-2);margin-top:4px}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;padding:14px 24px;border-top:1px solid var(--rule);color:var(--ink-2);
font-size:13px}
.legend span{display:inline-flex;align-items:center;gap:8px}
.legend i{width:12px;height:12px;border-radius:3px;border:1.5px solid transparent}
.legend .pass{background:var(--green)}.legend .fail{border-color:var(--ink)}.legend .crit{background:var(--ink)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 270px;gap:56px;align-items:start;margin-top:8px}
.side{position:sticky;top:24px;margin-top:64px;display:grid;gap:22px;font-size:14px;color:var(--ink-2)}
.side h3{margin:0 0 6px;font:500 13px/1.3 var(--sans);color:var(--ink)}
.side p{margin:0 0 6px}
.side nav a{display:block;padding:5px 0;color:var(--ink-2);text-decoration:none;border-bottom:1px solid var(--rule-soft)}
.side nav a:hover{color:var(--ink)}
.side pre{font-size:12px;margin:6px 0 0}
.note{color:var(--ink-2);font-size:15px;max-width:44em;margin-top:14px}
.point{font-size:17px;max-width:42em}
.chart{margin:0 0 20px;border:1px solid var(--rule);border-radius:16px;background:#fbfaf6;padding:16px 16px 8px}
.chart svg{display:block;width:100%;height:auto;font-variant-numeric:tabular-nums}
.chart text{font-family:var(--sans);font-size:13px;fill:var(--ink-2)}
.chart .lab{font-size:14px;fill:var(--ink);font-weight:500}
.chart .ax{font-family:var(--mono);font-size:11.5px}
.chart .big{font-family:var(--serif);font-size:17px;fill:var(--green)}
.wrap{overflow-x:auto;margin:16px 0}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.wrap td:first-child{min-width:11em}
.wrap td,.wrap th{padding-right:10px}
.wrap td .muted{font-size:13px;color:var(--ink-2)}
.wrap td.n .muted{white-space:nowrap}
.wrap th{white-space:normal}
.res{display:inline-flex;align-items:center;min-height:24px;padding:0 10px;border-radius:999px;font:500 12px/1 var(--mono);
letter-spacing:.03em;white-space:nowrap;background:var(--green-wash);color:var(--green)}
.res.fail{background:transparent;color:var(--ink);box-shadow:inset 0 0 0 1.5px var(--ink)}
.res.stale{background:var(--ink);color:var(--paper)}
summary{flex-wrap:wrap;column-gap:8px}
details p{margin-top:10px;font-size:15px}
details.lit{background:var(--green-wash)}
details{scroll-margin-top:24px}
.seg{display:flex;width:fit-content;max-width:100%;flex-wrap:wrap;gap:2px;padding:3px;border:1px solid var(--rule);
border-radius:999px;margin:0 0 12px;background:#fbfaf6}
.seg button{min-height:34px;padding:0 14px;border:0;border-radius:999px;background:transparent;color:var(--ink-2);
font:500 14px/1 var(--sans);cursor:pointer}
.seg button span{font-weight:400;opacity:.8}
.seg button[aria-checked=true]{background:var(--ink);color:var(--paper)}
code{background:rgb(27 26 23/.05);padding:1px 5px;border-radius:4px}
@media (max-width:1000px){
 .layout{grid-template-columns:minmax(0,1fr);gap:0}
 .side{position:static;margin-top:48px}
 .vrow{grid-template-columns:minmax(0,1fr) auto auto;gap:12px 16px}
 .vrow .strip{grid-column:1/-1;grid-row:2}
 .vsub{display:none}
}
@media (max-width:640px){
 .vrow{padding:14px 18px}.vhead,.vpart,.legend{padding-left:18px;padding-right:18px}
 .vhead h2{font-size:25px}
 .strip{gap:6px}.sg{gap:2px}.cell{border-radius:3px}
 .vscore b{font-size:28px}
 .sgl{display:none}
 .chart{overflow-x:auto;padding:12px 8px 4px}.chart svg{min-width:520px}
}
@media (prefers-reduced-motion:reduce){.cell{transition:none}}
"""

e = html.escape


def pct(a, b):
    return f"{round(100 * a / b)}%" if b else "-"


def summary_table(part: dict, order: list[str]) -> str:
    rows = []
    for c in order:
        if c not in part:
            continue
        s = part[c]["summary"]
        name, desc = LABELS[c]
        kinds = "<br>".join(f"{KIND[k]} {v}" for k, v in s["by_kind"].items())
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
    btns = "".join(
        f'<button type="button" role="radio" aria-checked="{"true" if c == default else "false"}" data-c="{c}">'
        f"{LABELS[c][0]}&nbsp; <span>{part[c]['summary']['passed']}/{part[c]['summary']['total']}</span></button>"
        for c in order if c in part)
    blocks = []
    for c in order:
        if c not in part:
            continue
        items = []
        for r in part[c]["rows"]:
            g = r["grade"]
            failed = [k.replace("_", " ") for k, v in g["checks"].items() if not v]
            kind = "stale" if g["label"] == "stale" else ("" if g["pass"] else "fail")
            pill = f"<span class='res {kind}'>{g['label']}</span>"
            o = r["output"]
            steps = ""
            if r.get("steps"):
                steps = "<br><span class=muted>Agent steps: " + html.escape(
                    "; ".join(f"search “{s['search']}”" if "search" in s else f"read {s['read']}" for s in r["steps"])) + "</span>"
            note = f"<br><span class=muted>{html.escape(r['note'])}</span>" if r.get("note") else ""
            items.append(
                f"<details id='{pid}-{c}-{r['id']}'><summary>{pill} <b>{r['id']}</b> · {KIND[r['kind']]} · {html.escape(r['question'])}</summary>"
                f"<p><b>Answer:</b> {html.escape(str(o.get('answer', '')))}<br>"
                f"<span class=muted>found={o.get('found')} · cites {html.escape(', '.join(map(str, o.get('citations') or [])) or 'nothing')}"
                f" · {r['totals']['context_tokens']:,} context tokens · {r['totals']['latency_s']}s</span>"
                + (f"<br><span class=muted>Failed checks: {', '.join(failed)}</span>" if failed else "") + steps + note + "</p></details>")
        blocks.append(f"<div class='ex' data-p='{pid}' data-c='{c}'{'' if c == default else ' hidden'}>{''.join(items)}</div>")
    return (f'<div class="seg" role="radiogroup" aria-label="Show answers for" data-p="{pid}">{btns}</div>'
            + "".join(blocks))


def cell(pid, c, r):
    g = r["grade"]
    st = "crit" if g["label"] == "stale" else ("pass" if g["pass"] else "fail")
    words = {"pass": "correct", "fail": "wrong", "crit": "stale, answered from the old terms"}[st]
    return (f'<a class="cell {st}" href="#{pid}-{c}-{r["id"]}" data-p="{pid}" data-c="{c}" '
            f'aria-label="{LABELS[c][0]}, {r["id"]}: {words}" title="{r["id"]}: {e(r["question"])}"></a>')


def vrow(part, pid, c, win=False):
    s = part[c]["summary"]
    rows = part[c]["rows"]
    name, desc = LABELS[c]
    if pid == "p1":
        groups = "".join(
            f'<div><div class="sg k{n}">{"".join(cell(pid, c, r) for r in rows if r["kind"] == k)}</div>'
            f'<span class="sgl">{KIND_SHORT[k]} {n}</span></div>'
            for k, n in (("fact", 18), ("absent", 4), ("cross", 3)))
        strip = f'<div class="strip q25">{groups}</div>'
    else:
        strip = f'<div class="strip q5"><div class="sg k5">{"".join(cell(pid, c, r) for r in rows)}</div><div></div></div>'
    return (f'<div class="vrow{" win" if win else ""}"><div class="vwho"><b>{name}</b><span>{desc}</span></div>{strip}'
            f'<div class="vscore"><b>{s["passed"]}</b><span>/{s["total"]}</span></div>'
            f'<div class="vtok"><b>{s["mean_context_tokens"]:,.0f}</b><span>tokens / question</span></div></div>')


def scatter(p1: dict) -> str:
    """Correct answers against context tokens per question, log x scale, drawn to scale."""
    W, H, L, R, T, B = 720, 350, 56, 28, 56, 52
    xmin, xmax, ymin, ymax = 3, 5, 15, 25  # log10 tokens: 1,000 to 100,000; answers 15 to 25

    def X(tok):
        return L + (math.log10(tok) - xmin) / (xmax - xmin) * (W - L - R)

    def Y(v):
        return T + (ymax - v) / (ymax - ymin) * (H - T - B)

    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-labelledby="sc-t sc-d">'
           '<title id="sc-t">Correct answers against context tokens per question</title>'
           '<desc id="sc-d">' + "; ".join(
               f"{LABELS[c][0]}: {p1[c]['summary']['passed']} of 25 at {p1[c]['summary']['mean_context_tokens']:,.0f} tokens"
               for c in ("long_context", "retrieval", "agent", "study_notes")) + "</desc>"]
    for v in (15, 20, 25):
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" stroke="rgb(27 26 23/.1)"/>'
                   f'<text class="ax" x="{L - 10}" y="{Y(v) + 4:.1f}" text-anchor="end">{v}</text>')
    for t, lab in ((1000, "1k"), (3000, "3k"), (10000, "10k"), (30000, "30k"), (100000, "100k")):
        out.append(f'<line x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{T}" y2="{H - B}" stroke="rgb(27 26 23/.06)"/>'
                   f'<text class="ax" x="{X(t):.1f}" y="{H - B + 18}" text-anchor="middle">{lab}</text>')
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" text-anchor="middle">Context tokens per question '
               "(log scale)</text>")
    out.append(f'<text x="14" y="{(T + H - B) / 2}" text-anchor="middle" transform="rotate(-90 14 {(T + H - B) / 2})">'
               "Correct of 25</text>")
    lc, nt = p1["long_context"]["summary"], p1["study_notes"]["summary"]
    x1, x2, y = X(lc["mean_context_tokens"]), X(nt["mean_context_tokens"]), Y(25)
    out.append(f'<line x1="{x1 - 12:.1f}" x2="{x2 + 14:.1f}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--green)" '
               'stroke-width="1.5" stroke-dasharray="4 4"/>'
               f'<path d="M{x2 + 14:.1f} {y:.1f} l8 -5 v10 z" fill="var(--green)"/>'
               f'<text class="big" x="{(x1 + x2) / 2:.1f}" y="{y + 26:.1f}" text-anchor="middle">'
               f'{lc["mean_context_tokens"] / nt["mean_context_tokens"]:.0f}x fewer tokens, same 25 of 25</text>')
    pts = {
        "long_context": ("var(--ink)", "var(--ink)", "middle", 0, -34),
        "study_notes": ("var(--green)", "var(--green)", "middle", 0, -34),
        "retrieval": ("var(--paper)", "var(--ink)", "end", -12, 22),
        "agent": ("var(--paper)", "var(--ink)", "start", 12, 22),
    }
    for c, (fill, stroke, anchor, dx, dy) in pts.items():
        s = p1[c]["summary"]
        cx, cy = X(s["mean_context_tokens"]), Y(s["passed"])
        out.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
                   f'<text class="lab" x="{cx + dx:.1f}" y="{cy + dy:.1f}" text-anchor="{anchor}">{LABELS[c][0]}'
                   f'</text><text x="{cx + dx:.1f}" y="{cy + dy + 16:.1f}" text-anchor="{anchor}">'
                   f'{s["passed"]}/25 · {s["mean_context_tokens"]:,.0f}</text>')
    return '<figure class="chart">' + "".join(out) + "</svg></figure>"


def build(run_dir: Path) -> str:
    R = json.loads((run_dir / "results.json").read_text())
    p1, p2, st = R.get("part1", {}), R.get("part2", {}), R.get("staleness", {})
    o1 = ["long_context", "retrieval", "agent", "study_notes"]
    o2 = ["long_context", "retrieval", "agent", "notes_stale", "notes_guarded", "notes_rebuilt"]
    lc, nt = p1.get("long_context", {}).get("summary", {}), p1.get("study_notes", {}).get("summary", {})
    ratio = (lc.get("mean_context_tokens", 0) / nt["mean_context_tokens"]) if nt.get("mean_context_tokens") else 0
    stale_n = p2.get("notes_stale", {}).get("summary", {}).get("stale", "-")
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
    shows = (f"<p class=point>The precomputed memory scored <b>{nt.get('passed', '-')}/25</b> against long context's "
             f"<b>{lc.get('passed', '-')}/25</b> (misses: memory {ids('study_notes')}; long context {ids('long_context')}) "
             f"with about 1/{ratio:.0f} of the context per question, after a one-time offline build of "
             f"{nt.get('build_tokens', 0):,} tokens.</p>"
             f"<p class=note>Retrieval and the tool agent used the least context per question but missed {rf} and {af} "
             f"single facts; {ra} and {aa} of those misses said the term was not in what they found, and no config "
             "made up a term.</p>")
    verdict = f"""
<section class="verdict" aria-labelledby="verdict-h">
  <div class="vhead"><h2 id="verdict-h">Same answers at {ratio:.0f}x fewer tokens, but {stale_n} of 5 answers went
  stale after amendments until the memory was rebuilt</h2>
  <p>The precomputed memory matched long context, <b>{nt.get('passed', '-')}/25</b> to
  <b>{lc.get('passed', '-')}/25</b>, on a {R['corpus']['bank_tokens']:,}-token bank of five public contracts.</p></div>
  <div class="vsub"><span>25 questions, one square each</span><span>Correct · context tokens per question</span></div>
  {''.join(vrow(p1, 'p1', c, win=(c == 'study_notes')) for c in o1)}
  <div class="vpart">After four amendments<span>Five questions whose answers the amendments changed.</span></div>
  {''.join(vrow(p2, 'p2', c) for c in ('notes_stale', 'notes_guarded', 'notes_rebuilt'))}
  <div class="legend"><span><i class="pass"></i>Correct</span><span><i class="fail"></i>Wrong</span>
  <span><i class="crit"></i>Stale: answered from the old terms</span><span>Select a square to read the answer.</span></div>
</section>"""
    body = f"""
<div class="head">
<h1>Contract memory harness</h1>
<p class="lead">A small agent harness and eval for one question: when an agent works over a firm's contract bank, does
a memory built ahead of time answer as well as rereading the contracts, at a fraction of the tokens, and what happens
when a contract is amended? Five public contracts from CUAD, 25 questions, a tool-using agent, and a plug-in slot where a
learned memory (such as an Engram) would go.</p>
</div>
{verdict}
<div class="layout">
<div class="content">

<h2 id="cost">Answer quality and cost</h2>
{scatter(p1)}
{shows}
{summary_table(p1, o1)}
<p class=note>Context tokens are counted by the harness (about 4 characters per token) for what each config puts in
the prompt. Billed input tokens are what the model provider reported, including its own fixed prompt overhead and cached
reads. The precomputed memory also spent {nt.get('build_tokens', 0):,} tokens once, offline, to write its notes.</p>

<h2 id="grading">How it is graded</h2>
<p>25 questions: 18 single facts, 4 terms the contract does not contain (the right answer is “not there”), and 3 that
need several contracts. Grading is a fixed rubric (exact figure present, right contract cited, no made-up terms), not a
model judging a model. Figures must match as whole numbers, so “5,000” does not match inside “4,500,000”. Citing a
contract that the answer checked and ruled out is not penalized; the first version of the rubric did penalize it and
failed four correct answers. Cross-contract answers are graded on recall only (every right contract named), not on
precision.</p>
<p class=note>Changes since the first published run, which is kept in <code>runs/v1_topic_list_notes</code>: the
note-writing prompt no longer lists the clause types the questions ask about (it now asks generically for the terms a
lawyer would be likely to ask about), and the grader now matches whole figures. The numbers on this page are from a
fresh live run with both changes in place; it is a single run, so a one-question difference is within run-to-run
noise. The grades shown are the recorded answers re-graded with the final grader, which changes only q15 (fail to pass
in long context, retrieval and the memory); the scores the live run reported are in
<code>runs/latest/results_as_recorded.json</code>.</p>

<h2 id="answers">Every answer</h2>
{explorer(p1, o1, 'p1', 'long_context')}

<h2 id="changes">When a contract changes</h2>
<p>Four amendments (written for this test and labeled as such) change five answers: a longer term, a new governing
law, a shorter termination notice, and two minimum fees. A memory built before the change still answers from the old
terms. The harness detects which contracts changed by comparing document hashes with the ones the memory was built
from, then either guards (sends questions on changed contracts to retrieval) or rebuilds only those contracts.</p>
<p class=point>The rule this suggests for a deployment: until a memory is rebuilt, route any question on a contract
family that changed since the last build to the current path.</p>
<p class=note>The guard was exercised only on the five changed questions, which all routed to retrieval; its routing for
unchanged contracts after an amendment is untested.</p>
<p><b>Contracts flagged stale:</b> {', '.join(st.get('stale_after_amendments', [])) or '-'}. <b>Rebuilt:</b> {rebuilt},
for {st.get('rebuild_tokens', 0):,} tokens (the full build was {st.get('full_build_tokens', 0):,}).</p>
{summary_table(p2, o2)}
{explorer(p2, o2, 'p2', 'notes_stale')}

<h2 id="plugin">Plugging in a learned memory</h2>
<p>Every config implements the same small interface in <code>cmh/memory.py</code>: <code>build(corpus)</code> runs
offline, <code>context(question)</code> returns what goes in the prompt, and <code>stale_docs(corpus)</code> says which
contracts the memory no longer matches. <code>EngramMemory</code> is the empty slot: with access, build() would
train or point at an engram for the bank, context() would send little or nothing, and the same 30
questions would score it next to the baselines on accuracy, tokens and latency.</p>
</div>

<aside class="side" aria-label="About this build">
  <nav aria-label="On this page"><a href="#verdict-h">Result</a><a href="#cost">Answer quality and cost</a>
  <a href="#grading">How it is graded</a><a href="#answers">Every answer</a><a href="#changes">When a contract changes</a>
  <a href="#plugin">Plugging in a learned memory</a></nav>
  <div><h3>Data</h3><p>Contracts: CUAD v1 (The Atticus Project), CC BY 4.0. The amendments are illustrative and not real
  filings.</p><p>Model: {model} (served as claude-sonnet-5-5; Claude Code in print mode, no tools, the harness's own
  system prompt). Every model call is recorded in <code>runs/latest/calls.jsonl</code>.</p></div>
  <div><h3>Run it yourself</h3><p>Re-check the grades with no API key:</p><pre>python -m cmh.run --replay runs/latest</pre>
  <p>Run live:</p><pre>python -m cmh.run --model claude-cli:sonnet
python -m cmh.run --model api:claude-sonnet-5-5</pre><p>The API run needs <code>ANTHROPIC_API_KEY</code>. Tests need no
  model:</p><pre>python -m unittest discover -s tests -t .</pre></div>
  <div><a class="pill" href="{REPO}">Code on GitHub</a></div>
</aside>
</div>
"""
    js = """<script>
function show(p,c){
 document.querySelectorAll('.ex[data-p="'+p+'"]').forEach(x=>x.hidden=x.dataset.c!==c);
 document.querySelectorAll('.seg[data-p="'+p+'"] button').forEach(b=>b.setAttribute('aria-checked',String(b.dataset.c===c)));
}
document.querySelectorAll('.seg[data-p] button').forEach(b=>b.addEventListener('click',()=>show(b.closest('.seg').dataset.p,b.dataset.c)));
document.querySelectorAll('.verdict .cell').forEach(a=>a.addEventListener('click',ev=>{
 ev.preventDefault();show(a.dataset.p,a.dataset.c);
 const d=document.getElementById(a.getAttribute('href').slice(1));if(!d)return;
 document.querySelectorAll('details.lit').forEach(x=>x.classList.remove('lit'));
 d.open=true;d.classList.add('lit');
 d.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});
}));
</script>"""
    desc = (f"A memory built ahead of time answered {nt.get('passed', '-')} of 25 contract questions, the same as long "
            f"context, at {ratio:.0f}x fewer tokens; {stale_n} of 5 answers went stale after amendments until rebuilt.")
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Contract memory harness</title><meta name=description content=\"{e(desc)}\">"
            f"<style>{CSS}</style></head><body><main>{body}</main>{js}</body></html>")


if __name__ == "__main__":
    run = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "runs" / "latest"
    (ROOT / "docs" / "index.html").write_text(build(run))
    import subprocess
    subprocess.run([sys.executable, str(ROOT / "scripts" / "demo_chrome.py"), str(ROOT / "docs" / "index.html"),
                    "--repo", "contract-memory-harness"], check=True)
    (ROOT / "docs" / ".nojekyll").write_text("")
    print("wrote docs/index.html")
