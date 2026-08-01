"""The ingest pipeline around the handlers: the generalized quality signal (WI-8), the full
normalize handoff + empty/garbage guard (WI-9), the extended provenance schema (WI-10), and the
CLI rewiring / backward compatibility (WI-11). Drives real `ingest()` and `cli.main()`."""
import contextlib
import dataclasses
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import cli, ingest                              # noqa: E402
from klode.lib.config import Config                            # noqa: E402
from klode.lib.formats import pdf                              # noqa: E402
from klode.lib.formats._base import ExtractionError           # noqa: E402
from tests.test_formats import make_docx, make_epub, _fake_run   # noqa: E402


def _make_lib(tmp: Path) -> Path:
    root = tmp / "kb"
    (root / "library" / "books").mkdir(parents=True)
    (root / "library" / "cards").mkdir(parents=True)
    dic = root / "words.txt"
    dic.write_text("the\nof\nand\nnarrative\ninformation\nmatters\n", encoding="utf-8")
    (root / "library.toml").write_text(
        '[library]\nid = "k"\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        "[bibliography]\nenabled = false\n[copyright]\nguard = false\n"
        f'[normalize]\ndict_path = "{dic}"\nbackup_dir = ""\n', encoding="utf-8")
    return root


def _prov_lines(cfg: Config):
    p = cfg.lib / "PROVENANCE.jsonl"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-ing-"))
        self.root = _make_lib(self.tmp)
        self.cfg = Config.load(self.root / "library.toml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _src(self, name: str, data: bytes) -> Path:
        p = self.tmp / name
        p.write_bytes(data)
        return p


# =========================================================== WI-8 quality signal
class QualitySignal(PipelineTest):
    def test_corruption_matches_legacy_metric(self):
        g = "the~ regulatIOn of narrative mfonnafion t~e " * 40
        self.assertEqual(ingest.quality_signal(g)["corruption"], ingest.corruption_score(g))

    def test_clean_prose_zero_control_exact_words(self):
        t = "the regulation of narrative information " * 40
        q = ingest.quality_signal(t)
        self.assertEqual(q["control_char_ratio"], 0.0)
        self.assertEqual(q["words"], len(t.split()))

    def test_control_bytes_raise_ratio(self):
        t = "prose " + "".join(chr(c) for c in range(1, 9)) * 40
        self.assertGreater(ingest.quality_signal(t)["control_char_ratio"], ingest.GARBAGE_CTRL_RATIO)

    def test_empty_and_whitespace_zero_words(self):
        self.assertEqual(ingest.quality_signal("")["words"], 0)
        self.assertEqual(ingest.quality_signal("   \n\t ")["words"], 0)


# =========================================================== WI-9 normalize handoff + guards
class NormalizeHandoff(PipelineTest):
    def _ingest_txt(self, data: bytes, cfg=None, cid="s"):
        src = self._src("s.txt", data)
        return ingest.ingest(cfg or self.cfg, src, "books", card_id=cid)

    def test_full_process_folds_ligature_and_dewraps(self):
        r = self._ingest_txt("The deﬁnition of behav-\nior matters in this sentence.\n".encode())
        out = (self.root / r.dest).read_text(encoding="utf-8")
        self.assertIn("definition", out)                       # ﬁ ligature folded
        self.assertIn("behavior", out)                         # hyphenated wrap dewrapped
        self.assertNotIn("ﬁ", out)
        self.assertNotIn("behav-", out)

    def test_furniture_stripped_and_reflowed(self):
        r = self._ingest_txt(
            "The narrative can also choose here\n13\n"
            "to regulate the flow of information today.\n".encode())
        out = (self.root / r.dest).read_text(encoding="utf-8")
        self.assertNotIn("\n13\n", "\n" + out + "\n")           # page number gone
        self.assertIn("here to regulate", out)                 # two fragments reflowed onto one line

    def test_dict_absent_degrades_no_crash(self):
        cfg = dataclasses.replace(self.cfg, dict_path="/nonexistent/words")
        r = self._ingest_txt("The deﬁnition of behav-\nior matters here.\n".encode(), cfg=cfg)
        out = (self.root / r.dest).read_text(encoding="utf-8")
        self.assertIn("definition", out)
        self.assertIn("behavior", out)

    def test_empty_extraction_refused_before_write(self):
        before = len(_prov_lines(self.cfg))
        with self.assertRaises(ExtractionError):
            self._ingest_txt("   \n  \n".encode())
        self.assertFalse((self.cfg.lib / "books" / "s.txt").exists())   # nothing written
        self.assertEqual(len(_prov_lines(self.cfg)), before)            # no provenance row

    def test_garbage_extraction_refused_before_write(self):
        data = b"Some prose here. " + bytes(range(1, 9)) * 60
        with self.assertRaises(ExtractionError):
            self._ingest_txt(data)
        self.assertFalse((self.cfg.lib / "books" / "s.txt").exists())

    def test_all_furniture_source_refused_after_normalize(self):
        # a source that normalizes to nothing (a lone page number) must not write an empty .txt
        before = len(_prov_lines(self.cfg))
        with self.assertRaises(ExtractionError):
            self._ingest_txt(b"13\n")
        self.assertFalse((self.cfg.lib / "books" / "s.txt").exists())
        self.assertEqual(len(_prov_lines(self.cfg)), before)

    def test_containment_guard_preserved(self):
        src = self._src("d.txt", b"hello world text here")
        with self.assertRaises(ValueError):
            ingest.ingest(self.cfg, src, "books", card_id="../../../tmp/escape")


# =========================================================== WI-10 provenance schema
class Provenance(PipelineTest):
    def test_txt_row_has_new_and_old_fields(self):
        src = self._src("Alpha.txt", b"the narrative regulates information here today")
        ingest.ingest(self.cfg, src, "books")
        row = json.loads(_prov_lines(self.cfg)[-1])
        self.assertEqual((row["format"], row["handler"]), ("txt", "txt"))
        self.assertEqual(row["source"], "Alpha.txt")
        for k in ("source_sha256", "words", "corruption_before", "corruption_after", "ingested_at"):
            self.assertIn(k, row)

    def test_epub_row_records_spine_handler(self):
        src = self._src("book.epub", make_epub())
        ingest.ingest(self.cfg, src, "books")
        row = json.loads(_prov_lines(self.cfg)[-1])
        self.assertEqual((row["format"], row["handler"]), ("epub", "epub"))

    def test_source_sha256_of_original_bytes(self):
        data = b"the narrative regulates information here"
        src = self._src("x.txt", data)
        ingest.ingest(self.cfg, src, "books")
        row = json.loads(_prov_lines(self.cfg)[-1])
        self.assertEqual(row["source_sha256"], hashlib.sha256(data).hexdigest())

    def test_append_only_first_line_untouched(self):
        ingest.ingest(self.cfg, self._src("one.txt", b"the narrative information here"), "books")
        first = _prov_lines(self.cfg)[0]
        ingest.ingest(self.cfg, self._src("two.txt", b"more narrative information there"), "books")
        lines = _prov_lines(self.cfg)
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], first)
        json.loads(lines[0]); json.loads(lines[1])

    def test_pdf_row_tags_handler(self):
        src = self._src("p.pdf", b"%PDF-1.7\n<dummy>")
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run",
                               return_value=_fake_run(stdout="clean narrative information " * 100)):
            ingest.ingest(self.cfg, src, "books")
        row = json.loads(_prov_lines(self.cfg)[-1])
        self.assertEqual((row["format"], row["handler"]), ("pdf", "pdftotext"))


# =========================================================== WI-11 CLI rewiring
def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliRewiring(PipelineTest):
    def _cfg_arg(self):
        return ["-c", str(self.root / "library.toml")]

    def test_pdf_backcompat_same_destination(self):
        src = self._src("sample.pdf", b"%PDF-1.7\n<dummy>")
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run",
                               return_value=_fake_run(stdout="clean narrative information " * 100)):
            code, out, err = _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "books"])
        self.assertEqual(code, 0, err)
        self.assertTrue((self.cfg.lib / "books" / "sample.txt").exists())   # same path as before

    def test_epub_end_to_end_spine_order(self):
        src = self._src("book.epub", make_epub(spine=("c3", "c1", "c2")))
        code, out, err = _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "books"])
        self.assertEqual(code, 0, err)
        text = (self.cfg.lib / "books" / "book.txt").read_text(encoding="utf-8")
        self.assertLess(text.index("MARK_G"), text.index("MARK_A"))

    def test_format_override_end_to_end(self):
        src = self._src("weird.txt", b"%PDF-1.7 not really a pdf just text here")
        code, out, err = _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "books", "--format", "txt"])
        self.assertEqual(code, 0, err)
        self.assertIn("%PDF", (self.cfg.lib / "books" / "weird.txt").read_text(encoding="utf-8"))

    def test_invalid_format_rejected(self):
        src = self._src("a.txt", b"hello there text")
        with self.assertRaises(SystemExit):
            _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "books", "--format", "zzz"])

    def test_next_steps_names_normalize(self):
        src = self._src("n.txt", b"the narrative regulates information here today")
        code, out, err = _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "books"])
        self.assertEqual(code, 0, err)
        self.assertIn("klode normalize", out)

    def test_unknown_shelf_and_missing_file_error(self):
        src = self._src("a.txt", b"hello there text here")
        code, _, _ = _run(self._cfg_arg() + ["ingest", str(src), "--shelf", "nope"])
        self.assertEqual(code, 1)
        code, _, _ = _run(self._cfg_arg() + ["ingest", str(self.tmp / "ghost.txt"), "--shelf", "books"])
        self.assertEqual(code, 1)


class NormalizeTables(unittest.TestCase):
    def test_process_preserves_markdown_tables(self):
        from klode.lib import normalize
        text = ("Here is a short intro line that\nwould normally reflow together.\n\n"
                "| Quarter | Revenue |\n|---------|---------|\n| Q1 | 1200 |\n| Q2 | 1500 |\n\n"
                "Closing prose here for the reader.\n")
        out, _ = normalize.process(text, set())
        self.assertIn("| Quarter | Revenue |", out)          # header verbatim
        self.assertIn("|---------|---------|", out)          # separator survives furniture-strip
        self.assertIn("| Q1 | 1200 |", out)                  # each row on its own line, not reflowed
        self.assertIn("| Q2 | 1500 |", out)
        self.assertIn("intro line that would normally reflow", out)   # prose still de-wrapped


if __name__ == "__main__":
    unittest.main()
