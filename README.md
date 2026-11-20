# contract-memory-harness

Demo page: https://pg2342-hub.github.io/contract-memory-harness/

A small agent harness and eval for one question: when an agent works over a firm's contract bank, does a memory built
ahead of time answer as well as rereading the contracts, at a fraction of the tokens, and what happens when a contract
is amended?

- **Contract bank:** five public contracts from CUAD (about 69,000 tokens): a software license and hosting
  agreement, a supply agreement, a transportation services agreement, an outsourcing agreement and a maintenance and
  support agreement.
- **25 questions** (`data/questions.json`): 18 single facts, 4 terms the contract does not contain (the right answer is
  "not there"), 3 that need several contracts.
- **5 changed questions** (`data/changed_questions.json`) after four amendments (`data/amendments/`, written for this
  test and labeled as not real filings) change a term, a governing law, a notice period and two minimum fees.
- **Configs:** long context (every contract in the prompt), retrieval (BM25, top 6 passages), a tool-using agent
  (search and read over several turns), and a precomputed memory (notes written once per contract, offline). After the
  amendments, the memory runs three ways: not rebuilt, guarded (questions on a changed contract fall back to retrieval)
  and rebuilt for the changed contracts only.
- **Grading** is a fixed rubric in `cmh/grade.py` (exact figure present, right contract cited, no made-up terms), not a
  model judging a model.

## Run it

```
python -m unittest discover -s tests -t .              # tests, no model needed
python -m cmh.run --replay runs/latest                  # re-grade the recorded run, no key needed
python -m cmh.run --model claude-cli:sonnet             # live, through Claude Code in print mode
python -m cmh.run --model api:claude-sonnet-5-5         # live, with ANTHROPIC_API_KEY
python docs/build_page.py                               # rebuild the demo page from runs/latest
```

Python 3.10+, standard library only. Every live call is written to `runs/<name>/calls.jsonl` with the token counts and
latency the provider reported.

## Plugging in a learned memory

Every config implements the same interface in `cmh/memory.py`:

- `build(corpus, client)` runs offline, before any question
- `context(question)` returns what goes in the prompt
- `stale_docs(corpus)` lists the contracts the memory no longer matches (by document hash)

`EngramMemory` is the empty slot. With access, `build()` would train or point at an engram for the bank, `context()`
would send little or nothing because the knowledge lives in the model, and the same 30 questions would score it next to
the baselines on accuracy, tokens and latency. `GuardedMemory` works with any memory that reports `stale_docs`.

## Layout

```
cmh/corpus.py     contract bank, amendments, chunking
cmh/bm25.py       retrieval index
cmh/memory.py     memory interface and backends (long context, retrieval, precomputed notes, guard, Engram slot)
cmh/agent.py      one-shot answerer and the tool-using agent
cmh/models.py     Claude Code CLI, Messages API, recorder, replay
cmh/grade.py      rubric
cmh/run.py        runs both parts and writes results.json
docs/             demo page (GitHub Pages)
```

Contracts: [CUAD v1](https://github.com/TheAtticusProject/cuad) by The Atticus Project, CC BY 4.0.
