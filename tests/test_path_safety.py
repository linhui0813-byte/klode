"""A card id is a bare filename stem. `zoom_card`/`verify_quote` take an agent-supplied `id` over
MCP, and every read path (meta/body/source_of/verify) funnels through `card_path`, so a
separator-bearing id must never traverse out of the cards dir to read an arbitrary file."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib import query                       # noqa: E402
from lode.lib.config import Config               # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


class CardPathGuard(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(FIX)
        self.tmp = Path(tempfile.mkdtemp(prefix="lode-sec-"))
        (self.tmp / "secret.md").write_text("# outside the KB\n", encoding="utf-8")  # exists via traversal only

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_valid_id_still_resolves(self):
        self.assertIsNotNone(query.card_path(self.cfg, "brevity"))

    def test_separator_ids_are_rejected(self):
        for bad in ("../brevity", "a/b", "/etc/passwd", "sub\\card"):
            self.assertIsNone(query.card_path(self.cfg, bad), bad)

    def test_traversal_cannot_reach_a_file_outside_cards(self):
        rel = os.path.relpath(self.tmp / "secret", self.cfg.cards)   # ../../…/secret (file exists)
        self.assertIsNone(query.card_path(self.cfg, rel))            # guarded despite the target existing

    def test_meta_source_and_verify_are_all_guarded(self):
        traversal = os.path.relpath(self.tmp / "secret", self.cfg.cards)
        self.assertIsNone(query.meta(self.cfg, traversal))
        self.assertIsNone(query.source_of(self.cfg, traversal))
        self.assertIsNone(query.verify(self.cfg, traversal, "anything"))


if __name__ == "__main__":
    unittest.main()
