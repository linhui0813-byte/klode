"""WI-5 — the grounding service: `verify` returns an EvidenceHit whose resolution is one of the six
taxonomy states. It proves textual OCCURRENCE, never entailment (a subprocess probe confirms the
default path never even imports the entail backend)."""
import os
import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib import core, services                   # noqa: E402
from lode.lib.config import Config                     # noqa: E402
from lode.lib.pool import KBPool                        # noqa: E402


class Grounding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lode-grnd-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _kb(self, source: str | None, *, sha: str | None = None) -> KBPool:
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "library" / "books").mkdir(parents=True)
        (root / "library" / "cards").mkdir(parents=True)
        (root / "library.toml").write_text(
            '[library]\nid = "k"\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[bibliography]\nenabled = false\n[copyright]\nguard = false\n", encoding="utf-8")
        if source is not None:
            (root / "library" / "books" / "s.txt").write_text(source, encoding="utf-8")
        fm = ("---\nid: s\nfile: library/books/s.txt\n"
              + (f"source_sha256: {sha}\n" if sha else "") + "---\n# S\n\n## Thin\ng\n")
        (root / "library" / "cards" / "s.md").write_text(fm, encoding="utf-8")
        return KBPool.single(Config.load(root / "library.toml"))

    def _verify(self, pool, phrase):
        return services.execute(pool, "verify", params={"card": "s", "phrase": phrase}).value

    def test_found(self):
        hit = self._verify(self._kb("A run of short sentences quickens the pace.\n"), "quickens the pace")
        self.assertIs(hit.resolution, core.Resolution.FOUND)
        self.assertTrue(hit.lines and hit.occurrence_only)      # occurrence, never "claim verified"

    def test_not_found(self):
        hit = self._verify(self._kb("nothing relevant here\n"), "no such zzqx phrase")
        self.assertIs(hit.resolution, core.Resolution.NOT_FOUND)

    def test_ambiguous(self):
        hit = self._verify(self._kb("dup token here. and dup token again.\n"), "dup token")
        self.assertIs(hit.resolution, core.Resolution.AMBIGUOUS)

    def test_folded_only(self):
        hit = self._verify(self._kb("the informa-\ntion is key\n"), "information is key")
        self.assertIs(hit.resolution, core.Resolution.FOLDED_ONLY)

    def test_source_stale(self):
        pool = self._kb("A run quickens the pace.\n", sha="deadbeefwrong")   # stored != current
        r = services.execute(pool, "verify", params={"card": "s", "phrase": "quickens the pace"})
        self.assertIs(r.value.resolution, core.Resolution.SOURCE_STALE)
        self.assertTrue(r.provenance.source_sha)                # provenance shows the hash it ran against

    def test_source_not_installed(self):
        hit = self._verify(self._kb(None), "anything")          # card exists, source .txt absent
        self.assertIs(hit.resolution, core.Resolution.SOURCE_NOT_INSTALLED)

    def test_card_file_path_traversal_is_contained(self):
        # a card's `file:` that escapes the library tree must be treated as not-installed, never read
        root = Path(tempfile.mkdtemp(dir=self.tmp))
        (root / "library" / "cards").mkdir(parents=True)
        (root / "library.toml").write_text(
            '[library]\nid = "k"\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[bibliography]\nenabled = false\n[copyright]\nguard = false\n", encoding="utf-8")
        secret = self.tmp / "secret.txt"                        # a real, readable file OUTSIDE the tree
        secret.write_text("TOP SECRET phrase\n", encoding="utf-8")
        rel = os.path.relpath(secret, root)                     # ../secret.txt
        (root / "library" / "cards" / "s.md").write_text(
            f"---\nid: s\nfile: {rel}\n---\n# S\n\n## Thin\ng\n", encoding="utf-8")
        pool = KBPool.single(Config.load(root / "library.toml"))
        hit = services.execute(pool, "verify",
                               params={"card": "s", "phrase": "TOP SECRET phrase"}).value
        # containment holds: the phrase IS in the target file, but it is out of tree -> not read
        self.assertIs(hit.resolution, core.Resolution.SOURCE_NOT_INSTALLED)

    def test_verify_never_imports_entail(self):
        probe = (
            "import sys\n"
            "from pathlib import Path\n"
            "from lode.lib.config import Config\n"
            "from lode.lib import services\n"
            "from lode.lib.pool import KBPool\n"
            "cfg = Config.load(Path('tests/fixtures/kb-fixture/library.toml'))\n"
            "services.execute(KBPool.single(cfg), 'verify',"
            " params={'card':'brevity','phrase':'Trim every clause the reader can infer'})\n"
            "print('LEAK' if 'lode.lib.entail' in sys.modules else 'CLEAN')\n"
        )
        p = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True)
        self.assertIn("CLEAN", p.stdout, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
