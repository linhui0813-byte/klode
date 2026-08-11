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

from klode.lib import query                       # noqa: E402
from klode.lib.config import Config               # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


class CardPathGuard(unittest.TestCase):
    def setUp(self):
        self.cfg = Config.load(FIX)
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-sec-"))
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


class CardAuthoredFilePathsAreUntrusted(unittest.TestCase):
    """The previous tests varied only the card ID, so the OTHER attacker-controlled path — the
    card's own `file:` front-matter — went unchecked.

    Cards travel: the registry exists so klode can be pointed at a knowledge base someone else
    wrote. `file:` was confined to `cfg.root`, which admits `.env`, `library.toml`, `.git/config`,
    and `books/../.env` — and `zoom --level content --grep` prints matching lines from whatever it
    resolves. Demonstrated before the fix: a card reading `library/.env` printed an AWS secret.
    """

    def setUp(self):
        import shutil, tempfile
        from pathlib import Path
        from klode.lib.config import Config
        REPO = Path(__file__).resolve().parent.parent
        self.tmp = Path(tempfile.mkdtemp())
        self.kb = self.tmp / "kb"
        shutil.copytree(REPO / "tests/fixtures/kb-fixture", self.kb)
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.kb / "library" / ".env").write_text("SECRET=leaked\n", encoding="utf-8")
        (self.kb / "library" / "books" / "note.md").write_text("not a source\n", encoding="utf-8")
        self.cfg = Config.load(self.kb / "library.toml")
        self.card = self.kb / "library" / "cards" / "brevity.md"
        self.original = self.card.read_text(encoding="utf-8")

    def _point_at(self, rel):
        import re
        self.card.write_text(re.sub(r"^file: .*$", f"file: {rel}", self.original,
                                    count=1, flags=re.M), encoding="utf-8")
        return query.source_of(self.cfg, "brevity")

    def test_a_file_outside_every_shelf_is_never_opened(self):
        for rel in ("library/.env", "library.toml", "library/cards/brevity.md",
                    "library/books/../.env", "../../etc/passwd", "/etc/passwd"):
            with self.subTest(rel=rel):
                src = self._point_at(rel)
                self.assertIsNotNone(src)
                self.assertIsNone(src.path, f"{rel} resolved to a readable path")

    def test_a_non_txt_file_inside_a_shelf_is_refused(self):
        # a shelf source is a .txt by definition; anything else in there is not corpus
        self.assertIsNone(self._point_at("library/books/note.md").path)

    def test_the_legitimate_source_still_resolves(self):
        # the guard must not break the thing it protects
        src = self._point_at("library/books/brevity.txt")
        self.assertIsNotNone(src.path)
        self.assertTrue(src.path.is_file())

    def test_verify_cannot_be_used_to_read_an_out_of_shelf_file(self):
        self._point_at("library/.env")
        hit = query.verify(self.cfg, "brevity", "SECRET")
        self.assertFalse(getattr(hit, "found", False) if hit else False,
                         "verify confirmed a phrase from a file outside the corpus")


if __name__ == "__main__":
    unittest.main()
