"""Card-first raw-passage retrieval and the fail-closed complete-source fallback."""
import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import klode.lib as lib
from klode.lib import core, services
from klode.lib.mcp_server import _dispatch_mcp
from klode.lib.pool import KBPool


SOURCE = """Beyond Feelings — Test Edition

A run of short sentences quickens the pace of a scene.
In chapter nine, the scarlet umbrella breaks during the smallest example.
The final paragraph warns that retrieval is not proof of interpretation.
"""


class EvidenceRetrieval(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-evidence-"))
        library = self.tmp / "library"
        (library / "books").mkdir(parents=True)
        (library / "cards").mkdir()
        (library / "books" / "beyond-feelings.txt").write_text(SOURCE, encoding="utf-8")
        (library / "cards" / "beyond-feelings.md").write_text(
            """---
id: beyond-feelings
shelf: books
file: library/books/beyond-feelings.txt
aliases: [critical thinking]
zoom: full
---
# Vincent Ruggiero — Beyond Feelings

## Thin
Sentence length changes pace (grep: `short sentences quickens the pace`).

## Full
The book distinguishes retrieval from interpretation
(grep: `retrieval is not proof of interpretation`).
""",
            encoding="utf-8",
        )
        self.config_path = self.tmp / "library.toml"
        self.config_path.write_text(
            """[library]
dir = "library"
cards = "cards"
shelves = ["books"]
[bibliography]
enabled = false
[frameworks]
enabled = false
[copyright]
guard = false
""",
            encoding="utf-8",
        )
        self.cfg = lib.Config.load(self.config_path)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_card_anchor_returns_verbatim_raw_passage_with_citation(self):
        result = lib.retrieve_evidence(self.cfg, "beyond-feelings", "What quickens the pace?")
        self.assertIs(result.status, core.EvidenceStatus.FOUND)
        self.assertFalse(result.full_text_searched)
        self.assertEqual(result.passages[0].route, "card-anchor")
        self.assertIn("short sentences quickens the pace", result.passages[0].text)
        self.assertEqual(result.passages[0].rel, "library/books/beyond-feelings.txt")
        self.assertGreaterEqual(result.passages[0].line_start, 1)
        self.assertGreaterEqual(result.passages[0].line_end, result.passages[0].line_start)
        self.assertEqual(result.passages[0].source_sha,
                         hashlib.sha256(SOURCE.encode("utf-8")).hexdigest())

    def test_missing_card_evidence_automatically_searches_complete_raw_source(self):
        result = lib.retrieve_evidence(
            self.cfg, "beyond-feelings", "What happened to the scarlet umbrella?")
        self.assertTrue(result.found)
        self.assertTrue(result.full_text_searched)
        self.assertEqual(result.passages[0].route, "full-text")
        self.assertIn("scarlet umbrella breaks", result.passages[0].text)

    def test_full_text_can_be_forced_after_caller_judges_card_evidence_insufficient(self):
        result = lib.retrieve_evidence(
            self.cfg, "beyond-feelings", "What quickens the pace?", full_text=True)
        self.assertTrue(result.found)
        self.assertTrue(result.full_text_searched)
        self.assertTrue(all(p.route == "full-text" for p in result.passages))

    def test_complete_source_miss_is_explicitly_insufficient(self):
        result = lib.retrieve_evidence(
            self.cfg, "beyond-feelings", "Where is the quantum zucchini protocol?")
        self.assertIs(result.status, core.EvidenceStatus.INSUFFICIENT)
        self.assertTrue(result.full_text_searched)
        self.assertFalse(result.passages)
        self.assertIn("no relevant passage was found", result.note.lower())
        self.assertIn("do not answer from recall", result.note.lower())

    def test_missing_raw_source_is_insufficient_not_a_false_empty_success(self):
        (self.tmp / "library" / "books" / "beyond-feelings.txt").unlink()
        result = lib.retrieve_evidence(self.cfg, "beyond-feelings", "scarlet umbrella")
        self.assertFalse(result.found)
        self.assertFalse(result.full_text_searched)
        self.assertIn("source-not-installed", result.unavailable_sources[0])

    def test_stale_source_is_not_reported_as_completely_searched(self):
        card = self.tmp / "library" / "cards" / "beyond-feelings.md"
        text = card.read_text(encoding="utf-8").replace(
            "zoom: full", "zoom: full\nsource_sha256: " + "0" * 64)
        card.write_text(text, encoding="utf-8")
        result = lib.retrieve_evidence(self.cfg, "beyond-feelings", "scarlet umbrella")
        self.assertFalse(result.found)
        self.assertFalse(result.full_text_searched)
        self.assertIn("source-stale", result.unavailable_sources[0])

    def test_service_and_mcp_return_the_same_result_and_frame_raw_text_as_untrusted(self):
        pool = KBPool.single(self.cfg)
        direct = services.execute(pool, "evidence", params={
            "card": "beyond-feelings", "query": "scarlet umbrella",
        })
        self.assertTrue(direct.value.found)
        self.assertEqual(direct.provenance.source_sha, direct.value.passages[0].source_sha)
        rendered, is_error = _dispatch_mcp(pool, "retrieve_evidence", {
            "id": "beyond-feelings", "query": "scarlet umbrella",
        })
        self.assertFalse(is_error)
        self.assertIn("EVIDENCE_FOUND", rendered)
        self.assertIn("library/books/beyond-feelings.txt:3-5", rendered)
        self.assertIn("<<<UNTRUSTED SOURCE", rendered)


if __name__ == "__main__":
    unittest.main()
