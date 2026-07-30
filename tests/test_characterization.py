"""Characterization suite — pins consult/diagnose behavior at the CLI (subprocess) and MCP (handler)
boundary BEFORE the console refactor, so the refactor is provably behavior-preserving. Subprocess for
the CLI exercises the real `python -m lodlib` entry point (catches import/packaging regressions);
handler-level for MCP pins the audience/section projection the shared console must reproduce.

Assertions are behavioral (exit code + the distinguishing projected content), not full byte snapshots
— strong enough to catch a dropped projection, a flipped exit code, or a changed redirect, without
being brittle to incidental wording.
"""
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib.config import Config
from lode.lib import mcp_server as mcp
from lode.lib.pool import KBPool


def _write_fixture(root: Path) -> Path:
    lib = root / "library"
    (lib / "books").mkdir(parents=True)
    (lib / "cards").mkdir()
    syn = lib / "frameworks" / "_syntheses"
    syn.mkdir(parents=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        "[frameworks]\nenabled = true\n[bibliography]\nenabled = false\n[copyright]\nguard = false\n",
        encoding="utf-8")
    (syn / "dim1.md").write_text(
        "---\ntitle: Synthesis — Dim One\nstatus: canonical\ndimension: dim1\ncards: [fw1]\n---\n"
        "# dim1\n\n**Core question:** how does dim one work?\n\n"
        "## Craft\nthe writer move — do the thing.\n\n"
        "## Operational spec for the engine\nscorer knob X = 1.\n\n## Owed\ngate stuff.\n", encoding="utf-8")
    (lib / "frameworks" / "fw1.md").write_text(
        "# Framework — A Thinker, *The Book* (2020)\n\n"
        "**Dimension:** Dim1 · **Source:** `library/books/fw1-book.txt`\n**Aliases:** a thinker, probing\n\n"
        "### 1. Engine\nthe core drive.\n### 4. Practices (what to DO)\ndo it.\n### 7. Disagreement\nargues.\n",
        encoding="utf-8")
    (lib / "cards" / "fw1-book.md").write_text(
        "---\nid: fw1-book\nfile: library/books/fw1-book.txt\naliases: [a thinker]\n---\n"
        "# A Thinker — The Book\n\n## Thin\nbook gist.\n", encoding="utf-8")
    (lib / "cards" / "src1.md").write_text(
        "---\nid: src1\nfile: library/books/src1.txt\naliases: [solo author, only book]\n---\n"
        "# Solo Author — Only Book\n\n## Thin\nsolo gist.\n", encoding="utf-8")
    (lib / "books" / "fw1-book.txt").write_text("x\n", encoding="utf-8")
    (lib / "books" / "src1.txt").write_text("y\n", encoding="utf-8")
    (syn / "_diagnostics.md").write_text(
        "# D\n\n| symptom cues | dimensions |\n|---|---|\n| drags, boring | dim1 |\n", encoding="utf-8")
    return root / "library.toml"


class CliCharacterization(unittest.TestCase):
    """`python -m lodlib` at the process boundary — the real entry point."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="lodlib-char-cli-"))
        cls.cfg = _write_fixture(cls.tmp)
        subprocess.run([sys.executable, "-m", "lode.lib", "-c", str(cls.cfg), "build"],
                       cwd=REPO, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_cli(self, *args):
        p = subprocess.run([sys.executable, "-m", "lode.lib", "-c", str(self.cfg), *args],
                           cwd=REPO, capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr

    def test_entrypoint_alive(self):
        code, _, _ = self.run_cli("check")            # the exact external contract a sibling hook uses
        self.assertEqual(code, 0)

    def test_dimension_writer_default_hides_engine(self):
        code, out, _ = self.run_cli("consult", "dim1")
        self.assertEqual(code, 0)
        self.assertIn("## Craft", out)
        self.assertIn("the writer move", out)
        self.assertNotIn("scorer knob", out)          # engine hidden by default

    def test_dimension_full_shows_engine(self):
        code, out, _ = self.run_cli("consult", "dim1", "--full")
        self.assertEqual(code, 0)
        self.assertIn("scorer knob X = 1", out)

    def test_dimension_section_selects_engine_only(self):
        code, out, _ = self.run_cli("consult", "dim1", "--section", "spec")
        self.assertEqual(code, 0)
        self.assertIn("scorer knob X = 1", out)
        self.assertNotIn("the writer move", out)

    def test_dimension_missing_section_exits_1(self):
        code, _, _ = self.run_cli("consult", "dim1", "--section", "nope")
        self.assertEqual(code, 1)

    def test_framework_dispatches_directly(self):
        code, out, _ = self.run_cli("consult", "a thinker")
        self.assertEqual(code, 0)
        self.assertIn("the core drive", out)

    def test_source_card_dispatches_directly(self):
        code, out, _ = self.run_cli("consult", "only book")
        self.assertEqual(code, 0)
        self.assertIn("solo gist", out)

    def test_ambiguous_exits_1_lists_candidates(self):
        code, _, err = self.run_cli("consult", "book")   # 'book' in both books
        self.assertEqual(code, 1)
        self.assertIn("matches several lenses", err)

    def test_none_exits_1(self):
        code, _, err = self.run_cli("consult", "zzznope")
        self.assertEqual(code, 1)
        self.assertIn("no lens", err)

    def test_diagnose_routes(self):
        code, out, _ = self.run_cli("diagnose", "the scene drags")
        self.assertEqual(code, 0)
        self.assertIn("dim1", out)

    def test_diagnose_no_match_exits_1(self):
        code, _, _ = self.run_cli("diagnose", "zzz utterly unrelated")
        self.assertEqual(code, 1)


class McpCharacterization(unittest.TestCase):
    """MCP tool handlers — the audience/section projection the shared console must reproduce."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-char-mcp-"))
        self.cfg = Config.load(_write_fixture(self.tmp))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mcp(self, tool, args):
        """Drive the real handle() path (single-KB pool) — the projection now flows through
        services.execute + the registry renderers, not a direct _tool_ call."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(KBPool.single(self.cfg), {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                 "params": {"name": tool, "arguments": args}})
        return json.loads(buf.getvalue())["result"]["content"][0]["text"]

    def test_dim_writer_is_craft_only(self):
        out = self._mcp("consult_dimension",{"dimension": "dim1"})
        self.assertIn("## Craft", out)
        self.assertIn("the writer move", out)
        self.assertNotIn("scorer knob", out)

    def test_dim_engine_adds_scorer(self):
        out = self._mcp("consult_dimension",{"dimension": "dim1", "audience": "engine"})
        self.assertIn("scorer knob X = 1", out)

    def test_dim_full_adds_adjudicates(self):
        out = self._mcp("consult_dimension",{"dimension": "dim1", "audience": "full"})
        self.assertIn("scorer knob X = 1", out)
        self.assertIn("Adjudicates across", out)

    def test_dim_section_override(self):
        out = self._mcp("consult_dimension",{"dimension": "dim1", "section": "spec"})
        self.assertIn("scorer knob X = 1", out)
        self.assertNotIn("the writer move", out)

    def test_dim_redirects_a_framework(self):
        out = self._mcp("consult_dimension",{"dimension": "a thinker"})
        self.assertIn("consult_framework", out)

    def test_framework_handler(self):
        out = self._mcp("consult_framework",{"name": "a thinker"})
        self.assertIn("the core drive", out)

    def test_diagnose_handler(self):
        out = self._mcp("diagnose",{"symptom": "the scene drags"})
        self.assertIn("dim1", out)
        self.assertIn("consult_dimension", out)


if __name__ == "__main__":
    unittest.main()
