import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from cmh import run as runmod
from cmh.agent import OneShot, ToolAgent
from cmh.corpus import DATA, Corpus
from cmh.grade import grade, norm
from cmh.memory import EngramMemory, GuardedMemory, RetrievalMemory, StudyNotesMemory
from cmh.models import Scripted


def fake_answer(system, prompt):
    """A crude fake model: notes requests get a fixed note; questions get an answer built from the
    first sentence of the context that shares the most words with the question."""
    if "Write notes" in prompt:
        doc = re.search(r"DOCUMENT (\w+)", prompt).group(1)
        return f"Governing law: see {doc}"
    if "action" in prompt and "Turn 1 of" in prompt:
        return json.dumps({"action": "search", "query": prompt.split("QUESTION:")[1][:80]})
    q = prompt.split("QUESTION:")[1].split("\n")[0].lower()
    ctx = prompt.split("QUESTION:")[0]
    best = max(re.split(r"(?<=[.;])\s", ctx), key=lambda s: len(set(s.lower().split()) & set(q.split())))
    doc = (re.findall(r"(?:DOCUMENT|NOTES) (\w+)", ctx) or re.findall(r"--- (\w+)#", ctx) or ["?"])[0]
    return json.dumps({"found": True, "answer": best[:300], "citations": [doc]})


class CorpusTests(unittest.TestCase):
    def test_bank_and_amendments(self):
        base, changed = Corpus.load(), Corpus.load(with_amendments=True)
        self.assertEqual(sorted(base.base_ids()), ["bnl", "corio", "dynamex", "seaspine", "verticalnet"])
        self.assertEqual(len(changed.family("bnl")), 2)
        self.assertEqual(changed.mentioned("What law governs the BNL outsourcing agreement?"), ["bnl"])

    def test_every_required_fact_is_in_its_source(self):
        """Dataset check: each expected fact is literally in the contract (or amendment) it comes from."""
        base, changed = Corpus.load(), Corpus.load(with_amendments=True)
        for q in json.loads((DATA / "questions.json").read_text()):
            text = norm(" ".join(base.docs[d].text for d in q["docs"]))
            for alts in q["must"]:
                self.assertTrue(any(norm(a) in text for a in alts), (q["id"], alts))
        for q in json.loads((DATA / "changed_questions.json").read_text()):
            amend = norm(" ".join(d.text for d in changed.family(q["docs"][0]) if d.amends))
            self.assertTrue(any(norm(a) in amend for a in q["must"][0]), q["id"])

    def test_bm25_finds_the_clause(self):
        m = RetrievalMemory()
        m.build(Corpus.load())
        top = m.search("terminate without cause two years written notice", 3)
        self.assertTrue(any(c.startswith("dynamex#") for c in top), top)


class GradeTests(unittest.TestCase):
    Q = {"id": "q09", "kind": "fact", "docs": ["bnl"], "must": [["oklahoma"]]}

    def test_fact(self):
        self.assertTrue(grade(self.Q, {"found": True, "answer": "Oklahoma law.", "citations": ["bnl"]})["pass"])
        self.assertFalse(grade(self.Q, {"found": True, "answer": "Oklahoma law.", "citations": ["corio"]})["pass"])

    def test_absent_and_fabricated(self):
        q = {"id": "q19", "kind": "absent", "docs": ["bnl"], "must": []}
        self.assertTrue(grade(q, {"found": False, "answer": "No such clause.", "citations": []})["pass"])
        self.assertEqual(grade(q, {"found": True, "answer": "One year.", "citations": ["bnl"]})["label"], "fabricated")

    def test_changed_and_stale(self):
        q = {"id": "c02", "kind": "changed", "docs": ["bnl"], "must": [["texas"]], "old": ["oklahoma"]}
        self.assertTrue(grade(q, {"found": True, "answer": "Texas, per Amendment No. 1.", "citations": ["bnl_amendment_1"]})["pass"])
        self.assertEqual(grade(q, {"found": True, "answer": "Oklahoma.", "citations": ["bnl"]})["label"], "stale")

    def test_numbers_normalize(self):
        q = {"id": "q10", "kind": "fact", "docs": ["verticalnet"], "must": [["4,500,000"]]}
        self.assertTrue(grade(q, {"found": True, "answer": "$4500000", "citations": ["verticalnet#3"]})["pass"])

    def test_cross_wrong_citation(self):
        q = {"id": "q23", "kind": "cross", "docs": ["dynamex"], "must": [["ontario"]], "wrong_docs": ["corio"]}
        self.assertFalse(grade(q, {"found": True, "answer": "Ontario", "citations": ["dynamex", "corio"]})["pass"])


class MemoryTests(unittest.TestCase):
    def test_staleness_and_guard(self):
        client = Scripted(fake_answer)
        notes = StudyNotesMemory()
        notes.build(Corpus.load(), client)
        changed = Corpus.load(with_amendments=True)
        self.assertEqual(sorted(notes.stale_docs(changed)), ["bnl", "corio", "dynamex", "verticalnet"])
        notes.corpus = changed
        guard = GuardedMemory(notes, RetrievalMemory())
        guard.build(changed)
        self.assertIn("fallback", guard.context("What law governs the BNL outsourcing agreement?").note)
        self.assertEqual(guard.context("What law governs the PcoMed and Integra supply agreement?").note, "")
        rebuilt = notes.refresh(changed, client)
        self.assertEqual(len(rebuilt), 4)
        self.assertEqual(notes.stale_docs(changed), [])

    def test_engram_slot_is_explicit(self):
        with self.assertRaises(NotImplementedError):
            EngramMemory().build(Corpus.load())


class AgentTests(unittest.TestCase):
    def test_agent_searches_then_answers(self):
        mem = RetrievalMemory()
        mem.build(Corpus.load())
        script = iter([json.dumps({"action": "search", "query": "governing law Oklahoma"}),
                       json.dumps({"action": "read", "chunk": "bnl#0"}),
                       json.dumps({"action": "answer", "found": True, "answer": "Oklahoma", "citations": ["bnl"]})])
        client = Scripted(lambda s, p: next(script))
        t = ToolAgent(mem, client).answer("q09", "What state's law governs the BNL agreement?")
        self.assertEqual(t.output["answer"], "Oklahoma")
        self.assertEqual(len(t.calls), 3)
        self.assertEqual([list(s)[0] for s in t.steps], ["search", "read"])


class EndToEndTests(unittest.TestCase):
    def test_record_then_replay_gives_same_results(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            orig = runmod.make_client
            runmod.make_client = lambda spec: Scripted(fake_answer)
            runmod.main(["--out", str(tmp), "--workers", "1"])
            first = json.loads((tmp / "results.json").read_text())
            runmod.make_client = orig
            runmod.main(["--replay", str(tmp), "--workers", "1"])
            second = json.loads((tmp / "results.json").read_text())
            for part in ("part1", "part2"):
                for c in first[part]:
                    self.assertEqual(first[part][c]["summary"]["passed"], second[part][c]["summary"]["passed"])
            self.assertEqual(len(first["part1"]["long_context"]["rows"]), 25)
            self.assertEqual(len(first["part2"]["notes_rebuilt"]["rows"]), 5)
        finally:
            runmod.make_client = orig
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
