"""Multi-format ingestion — the `lode.lib.formats` package: the model + registry, each stdlib
handler (txt/html/epub/docx), the PDF handler wrapping the tiered logic, the content-sniffing
router, and the optional-tier escalation. All fixtures are real in-memory zip/html — the only
mocks are the pdftotext subprocess and the lazy-import OCR seam (each paired with a skip-guarded
real path). The default suite runs with ZERO optional backends installed."""
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib import formats                                    # noqa: E402
from lode.lib.formats import _base, docx, epub, html, pdf, txt  # noqa: E402
from lode.lib.formats._base import (Extraction, ExtractionError, UnsupportedFormat,  # noqa: E402
                                    ZipBombError, ZipTraversalError)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# --------------------------------------------------------------------------- fixture builders
def make_epub(spine=("c3", "c1", "c2"), *, with_mimetype=True, bodies=None,
              include_nav=True) -> bytes:
    """A real in-memory EPUB. Chapters are STORED in the zip as c1,c2,c3 but the OPF spine
    lists them in `spine` order — so a correct reader yields spine order, not zip/alpha order."""
    marks = {"c1": "MARK_A", "c2": "MARK_B", "c3": "MARK_G"}
    bodies = bodies or {c: f"<p>{m}</p>" for c, m in marks.items()}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if with_mimetype:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml",
                   '<?xml version="1.0"?><container version="1.0" '
                   'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                   '<rootfile full-path="OEBPS/content.opf" '
                   'media-type="application/oebps-package+xml"/></rootfiles></container>')
        items = "".join(f'<item id="{c}" href="{c}.xhtml" media-type="application/xhtml+xml"/>'
                        for c in ("c1", "c2", "c3"))
        if include_nav:
            items += '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"/>'
        refs = "".join(f'<itemref idref="{s}"/>' for s in spine)
        z.writestr("OEBPS/content.opf",
                   '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" '
                   f'version="3.0"><manifest>{items}</manifest><spine>{refs}</spine></package>')
        for c in ("c1", "c2", "c3"):
            z.writestr(f"OEBPS/{c}.xhtml",
                       f'<?xml version="1.0"?><html><body>{bodies.get(c, "")}</body></html>')
        if include_nav:
            z.writestr("OEBPS/nav.xhtml", "<html><body><p>NAVMARK</p></body></html>")
    return buf.getvalue()


def make_docx(paragraphs) -> bytes:
    """A real in-memory DOCX. Each paragraph is a list of run strings, or the token "EMPTY",
    or a "BR"/"TAB" token inside a run list."""
    def para_xml(runs):
        if runs == "EMPTY":
            return "<w:p/>"
        parts = []
        for r in runs:
            if r == "BR":
                parts.append("<w:r><w:br/></w:r>")
            elif r == "TAB":
                parts.append("<w:r><w:tab/></w:r>")
            else:
                parts.append(f"<w:r><w:t>{r}</w:t></w:r>")
        return f"<w:p>{''.join(parts)}</w:p>"
    body = "".join(para_xml(p) for p in paragraphs)
    document = (f'<?xml version="1.0"?><w:document xmlns:w="{W_NS}">'
                f'<w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<?xml version='1.0'?><Types/>")
        z.writestr("word/document.xml", document)
    return buf.getvalue()


def _write(tmp: Path, name: str, data: bytes) -> Path:
    p = tmp / name
    p.write_bytes(data)
    return p


def _fake_run(stdout="", returncode=0, stderr=""):
    m = mock.Mock()
    m.stdout, m.returncode, m.stderr = stdout, returncode, stderr
    return m


class _FakeResp:                                            # a urlopen() context-manager stand-in
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FormatsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lode-fmt-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


# =========================================================== WI-1 model + registry
class ModelRegistry(FormatsTest):
    def test_extraction_is_frozen_with_four_fields(self):
        e = Extraction(text="x", handler="txt", format="txt", note="")
        self.assertEqual((e.text, e.handler, e.format, e.note), ("x", "txt", "txt", ""))
        with self.assertRaises(Exception):
            e.text = "y"                                       # frozen

    def test_extraction_rejects_empty_format(self):
        with self.assertRaises(ValueError):
            Extraction(text="x", handler="txt", format="", note="")

    def test_handlers_priority_sorted_and_formats_unique(self):
        prios = [h.priority for h in formats.HANDLERS]
        self.assertEqual(prios, sorted(prios))                 # ordered
        fmts = [h.format for h in formats.HANDLERS]
        self.assertEqual(len(fmts), len(set(fmts)))            # unique
        self.assertEqual(formats.by_format("txt").priority, max(prios))   # txt is the fallback

    def test_by_format_hit_and_miss(self):
        self.assertEqual(formats.by_format("epub").format, "epub")
        self.assertIsNone(formats.by_format("nope"))

    def test_every_handler_satisfies_protocol_surface(self):
        for h in formats.HANDLERS:
            self.assertTrue(hasattr(h, "sniff") and hasattr(h, "extract"))
            self.assertTrue(isinstance(h.format, str) and h.format)
            self.assertIsInstance(h.priority, int)

    def test_import_pulls_in_no_backend(self):
        code = "import lode.lib.formats, sys; print([m for m in " \
               "('kreuzberg','docling','trafilatura','docx') if m in sys.modules])"
        out = __import__("subprocess").run([sys.executable, "-c", code], cwd=REPO,
                                           capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "[]", out.stdout + out.stderr)


# =========================================================== WI-2 TXT
class Txt(FormatsTest):
    def _ext(self, data):
        return txt.TxtHandler().extract(_write(self.tmp, "s", data))

    def test_utf8_roundtrips_nonascii(self):
        e = self._ext("café 世界".encode("utf-8"))
        self.assertEqual(e.text, "café 世界")
        self.assertEqual((e.format, e.handler), ("txt", "txt"))

    def test_latin1_fallback_sets_note(self):
        e = self._ext(b"caf\xe9")
        self.assertEqual(e.text, "café")
        self.assertIn("latin-1", e.note)

    def test_bom_stripped(self):
        self.assertEqual(self._ext(b"\xef\xbb\xbfhello").text, "hello")

    def test_sniff_rejects_binary_and_markup(self):
        h = txt.TxtHandler()
        for head in (b"%PDF-1.7 x", b"PK\x03\x04zz", b"   <html>", b"\xef\xbb\xbf<!DOCTYPE html>", b"",
                     bytes(range(1, 20)) * 20):            # control-byte-heavy binary
            self.assertFalse(h.sniff(self.tmp, head), head)
        self.assertTrue(h.sniff(self.tmp, b"Just prose.\n"))


# =========================================================== WI-3 HTML/XHTML
class Html(FormatsTest):
    def _text(self, s):
        return html.HtmlHandler().extract(_write(self.tmp, "s.html", s.encode())).text

    def test_drops_script_style_and_breaks_blocks(self):
        t = self._text("<script>alert(1)</script><style>.x{}</style><p>Hello</p><p>World</p>")
        self.assertNotIn("alert", t)
        self.assertNotIn(".x{", t)
        self.assertIn("Hello", t)
        self.assertIn("World", t)
        self.assertLess(t.index("Hello"), t.index("World"))
        self.assertIn("\n", t)                                 # blocks separated

    def test_entities_unescaped(self):
        t = self._text("A &amp; B &#233; C &mdash; D")
        self.assertIn("A & B é C — D", t)

    def test_inline_tags_stay_on_one_line(self):
        self.assertIn("a bold word", self._text("a <b>bold</b> word"))

    def test_unclosed_tag_is_lenient(self):
        self.assertIn("oops", self._text("<p>oops"))

    def test_sniff_tolerant(self):
        h = html.HtmlHandler()
        for head in (b"  <!DOCTYPE html>", b'<?xml version="1.0"?><html>', b"\xef\xbb\xbf<html>"):
            self.assertTrue(h.sniff(self.tmp, head), head)
        self.assertFalse(h.sniff(self.tmp, b"The quick brown fox."))


# =========================================================== WI-4 EPUB
class Epub(FormatsTest):
    def _text(self, data):
        return epub.EpubHandler().extract(_write(self.tmp, "b.epub", data)).text

    def test_spine_reading_order(self):
        t = self._text(make_epub(spine=("c3", "c1", "c2")))     # G, A, B
        self.assertLess(t.index("MARK_G"), t.index("MARK_A"))
        self.assertLess(t.index("MARK_A"), t.index("MARK_B"))

    def test_manifest_only_item_excluded(self):
        self.assertNotIn("NAVMARK", self._text(make_epub(spine=("c1", "c2", "c3"))))

    def test_chapter_entities_via_shared_html_path(self):
        t = self._text(make_epub(spine=("c1",), bodies={"c1": "<p>A &amp; B</p>"}))
        self.assertIn("A & B", t)

    def test_sniff_requires_epub_mimetype(self):
        h = epub.EpubHandler()
        self.assertTrue(h.sniff(self.tmp, b"PK\x03\x04") is False or
                        h.sniff(_write(self.tmp, "ok.epub", make_epub()), b"PK\x03\x04"))
        bare = io.BytesIO()
        with zipfile.ZipFile(bare, "w") as z:
            z.writestr("a.txt", "x")
        p = _write(self.tmp, "bare.epub", bare.getvalue())
        self.assertFalse(h.sniff(p, b"PK\x03\x04"))
        self.assertTrue(h.sniff(_write(self.tmp, "real.epub", make_epub()), b"PK\x03\x04"))

    def test_zip_bomb_declared_size_refused(self):
        data = make_epub(spine=("c1",), bodies={"c1": "A" * 5000})
        p = _write(self.tmp, "bomb.epub", data)
        with mock.patch.object(_base, "MAX_UNCOMPRESSED", 2000):
            with self.assertRaises(ZipBombError):
                epub.EpubHandler().extract(p)

    def test_zip_bomb_cumulative_refused(self):
        bodies = {c: "A" * 700 for c in ("c1", "c2", "c3")}
        p = _write(self.tmp, "cum.epub", make_epub(spine=("c1", "c2", "c3"), bodies=bodies))
        with mock.patch.object(_base, "MAX_UNCOMPRESSED", 1500):   # each <1500, sum >1500
            with self.assertRaises(ZipBombError):
                epub.EpubHandler().extract(p)

    def test_zip_traversal_refused_and_nothing_written(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
            z.writestr("../evil.xhtml", "<html><body>x</body></html>")
        sentinel = Path(tempfile.mkdtemp(dir=self.tmp))
        before = os.listdir(sentinel)
        with self.assertRaises(ZipTraversalError):
            epub.EpubHandler().extract(_write(self.tmp, "trav.epub", bad.getvalue()))
        self.assertEqual(os.listdir(sentinel), before)          # nothing extracted to disk

    def test_entry_name_guard_refuses_absolute_and_dotdot(self):
        for bad in ("../evil.xhtml", "a/../../etc/passwd", "/etc/lode_evil", "\\abs", "C:evil"):
            with self.assertRaises(ZipTraversalError, msg=bad):
                _base._safe_entry_name(bad)
        for ok in ("OEBPS/c1.xhtml", "word/document.xml", "a/b/c.txt"):
            _base._safe_entry_name(ok)                         # no raise

    def test_missing_opf_raises(self):
        broken = io.BytesIO()
        with zipfile.ZipFile(broken, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
            z.writestr("META-INF/container.xml",
                       '<?xml version="1.0"?><container '
                       'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                       '<rootfiles><rootfile full-path="OEBPS/missing.opf"/></rootfiles></container>')
        with self.assertRaises(ExtractionError):
            epub.EpubHandler().extract(_write(self.tmp, "no-opf.epub", broken.getvalue()))


# =========================================================== WI-5 DOCX
class Docx(FormatsTest):
    def _text(self, data):
        return docx.DocxHandler().extract(_write(self.tmp, "d.docx", data)).text

    def test_runs_merge_paragraphs_split(self):
        self.assertEqual(self._text(make_docx([["Run1", "Run2"], ["Second"]])),
                         "Run1Run2\nSecond")

    def test_namespace_resolved(self):
        self.assertIn("Run1Run2", self._text(make_docx([["Run1", "Run2"]])))

    def test_br_and_empty_paragraph(self):
        t = self._text(make_docx([["A", "BR", "B"], "EMPTY", ["C"]]))
        self.assertIn("A", t)
        self.assertIn("B", t)
        self.assertIn("C", t)
        self.assertIn("", t.split("\n"))                       # empty paragraph -> a blank line

    def test_sniff_requires_document_xml(self):
        h = docx.DocxHandler()
        bare = io.BytesIO()
        with zipfile.ZipFile(bare, "w") as z:
            z.writestr("a.txt", "x")
        self.assertFalse(h.sniff(_write(self.tmp, "bare.docx", bare.getvalue()), b"PK\x03\x04"))
        self.assertTrue(h.sniff(_write(self.tmp, "ok.docx", make_docx([["x"]])), b"PK\x03\x04"))

    def test_zip_bomb_declared_size_refused(self):
        p = _write(self.tmp, "bomb.docx", make_docx([["A" * 5000]]))
        with mock.patch.object(_base, "MAX_UNCOMPRESSED", 2000):
            with self.assertRaises(ZipBombError):
                docx.DocxHandler().extract(p)

    def test_zip_traversal_refused(self):
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("word/document.xml", "<x/>")
            z.writestr("../evil", "x")
        with self.assertRaises(ZipTraversalError):
            docx.DocxHandler().extract(_write(self.tmp, "trav.docx", bad.getvalue()))


# =========================================================== WI-6 / WI-12 PDF handler + tiers
class Pdf(FormatsTest):
    CLEAN = "ZZCLEAN word " * 200                              # >MIN_WORDS, corruption 0
    GARBLED = "the~ regulatIOn mfonnafion t~e " * 80           # corruption >> threshold

    def _pdf(self):
        return _write(self.tmp, "d.pdf", b"%PDF-1.7\n<dummy>")

    def test_sniff(self):
        h = pdf.PdfHandler()
        self.assertTrue(h.sniff(self.tmp, b"%PDF-1.7"))
        self.assertTrue(h.sniff(self.tmp, b"%PDF-1.4"))
        self.assertFalse(h.sniff(self.tmp, b"PK\x03\x04"))
        self.assertFalse(h.sniff(self.tmp, b"plain text"))

    def test_auto_clean_text_layer(self):
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.CLEAN)):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual((e.handler, e.format), ("pdftotext", "pdf"))
        self.assertIn("ZZCLEAN", e.text)
        self.assertEqual(e.note, "text layer clean")

    def test_auto_degrades_when_ocr_absent(self):
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", side_effect=ImportError("no kreuzberg")):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual(e.handler, "pdftotext")
        self.assertIn("WANTED OCR but", e.note)

    def test_auto_escalates_to_xberg(self):
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", return_value="clean words here " * 40):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual(e.handler, "xberg")
        self.assertTrue(e.note.startswith("escalated:"))

    def test_forced_tier_bypasses_escalation(self):
        def _boom(*a, **k):
            raise AssertionError("xberg must not be called for a forced tier")
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", side_effect=_boom):
            e = pdf.PdfHandler().extract(self._pdf(), tier="pdftotext")
        self.assertEqual(e.handler, "pdftotext")

    def test_pdftotext_failure_surfaces(self):
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(returncode=1, stderr="boom")):
            with self.assertRaises(RuntimeError):
                pdf.PdfHandler().extract(self._pdf(), tier="pdftotext")

    def test_auto_escalates_to_docling(self):                  # WI-12: Tier 3 now reachable
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", return_value="t~e regulatIOn " * 10), \
             mock.patch.object(pdf, "_docling", return_value="the regulation of information " * 30):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual(e.handler, "docling")

    def test_auto_degrades_when_docling_absent(self):          # WI-12: no crash at Tier 3 gap
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", return_value="t~e regulatIOn " * 10), \
             mock.patch.object(pdf, "_docling", side_effect=ImportError("no docling")):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertIn(e.handler, ("pdftotext", "xberg"))       # best available, no crash

    def test_empty_ocr_never_replaces_text(self):          # audit: empty result must not win on score 0
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(stdout=self.GARBLED)), \
             mock.patch.object(pdf, "_xberg", return_value=""), \
             mock.patch.object(pdf, "_docling", side_effect=ImportError("no docling")):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual(e.handler, "pdftotext")
        self.assertTrue(e.text.split())                    # kept usable text, not the empty OCR

    def test_auto_tolerates_pdftotext_failure(self):       # audit: Tier-1 failure escalates, not aborts
        with mock.patch.object(pdf.shutil, "which", return_value="/x/pdftotext"), \
             mock.patch.object(pdf.subprocess, "run", return_value=_fake_run(returncode=1, stderr="boom")), \
             mock.patch.object(pdf, "_xberg", return_value="clean recovered text " * 40):
            e = pdf.PdfHandler().extract(self._pdf(), tier="auto")
        self.assertEqual(e.handler, "xberg")

    def test_unknown_forced_tier_raises_valueerror(self):  # audit: friendly error, not KeyError
        with self.assertRaises(ValueError):
            pdf.choose_and_extract(self._pdf(), tier="bogus")

    # ---- docling remote adapter (remote GPU host) — mocked urllib, backend-free ----
    def test_docling_remote_posts_and_returns_markdown(self):
        payload = {"status": "success",
                   "document": {"md_content": "| a | b |\n|---|---|\n| 1 | 2 |"}}
        with mock.patch.dict(os.environ, {pdf.DOCLING_ENV: "http://docling.test:15001"}), \
             mock.patch.object(pdf.urllib.request, "urlopen", return_value=_FakeResp(payload)) as uo:
            md = pdf._docling(self._pdf())
        self.assertIn("| a | b |", md)
        req = uo.call_args.args[0]
        self.assertTrue(req.full_url.endswith("/v1/convert/file"))
        self.assertIn(b'filename="d.pdf"', req.data)        # the PDF was posted as multipart

    def test_docling_remote_network_error_raises_oserror(self):
        with mock.patch.dict(os.environ, {pdf.DOCLING_ENV: "http://docling.test:15001"}), \
             mock.patch.object(pdf.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("down")):
            with self.assertRaises(OSError):                # URLError is an OSError -> escalation degrades
                pdf._docling(self._pdf())

    def test_docling_remote_empty_markdown_raises(self):
        with mock.patch.dict(os.environ, {pdf.DOCLING_ENV: "http://docling.test:15001"}), \
             mock.patch.object(pdf.urllib.request, "urlopen",
                               return_value=_FakeResp({"document": {"md_content": ""}})):
            with self.assertRaises(RuntimeError):
                pdf._docling(self._pdf())

    def test_docling_no_endpoint_no_package_raises_importerror(self):
        self.assertFalse(importlib.util.find_spec("docling"))   # absent by default
        env = {k: v for k, v in os.environ.items() if k != pdf.DOCLING_ENV}
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ImportError):
                pdf._docling(self._pdf())

    @unittest.skipUnless(shutil.which("pdftotext"), "poppler not installed")
    def test_real_pdftotext_integration(self):
        # a real (if trivial) PDF path — only runs where poppler is present
        p = _write(self.tmp, "r.pdf", b"%PDF-1.7\n%%EOF\n")
        try:
            e = pdf.PdfHandler().extract(p, tier="pdftotext")
            self.assertEqual(e.format, "pdf")
        except RuntimeError:
            self.skipTest("pdftotext rejected the trivial fixture")


# =========================================================== WI-7 router
class Router(FormatsTest):
    def test_content_beats_extension(self):
        p = _write(self.tmp, "foo.txt", b"%PDF-1.7\n<dummy>")
        self.assertEqual(formats.route(p).format, "pdf")

    def test_shared_zip_signature_disambiguated_by_content(self):
        self.assertEqual(formats.route(_write(self.tmp, "book.docx", make_epub())).format, "epub")
        self.assertEqual(formats.route(_write(self.tmp, "doc.epub", make_docx([["x"]]))).format, "docx")

    def test_bare_zip_is_unsupported(self):
        bare = io.BytesIO()
        with zipfile.ZipFile(bare, "w") as z:
            z.writestr("a.txt", "x")
        with self.assertRaises(UnsupportedFormat):
            formats.route(_write(self.tmp, "x.epub", bare.getvalue()))

    def test_format_override_wins(self):
        p = _write(self.tmp, "foo.txt", b"%PDF-1.7\n<dummy>")
        self.assertEqual(formats.route(p, fmt="txt").format, "txt")

    def test_unrecognized_binary_raises(self):
        with self.assertRaises(UnsupportedFormat):
            formats.route(_write(self.tmp, "x.bin", b"\x00\x01\x02"))
        with self.assertRaises(UnsupportedFormat):
            formats.route(_write(self.tmp, "empty", b""))

    def test_malicious_archive_fails_loud(self):           # audit: route re-raises, not "unsupported"
        bad = io.BytesIO()
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                       compress_type=zipfile.ZIP_STORED)
            z.writestr("../evil.xhtml", "x")
        with self.assertRaises(ZipTraversalError):
            formats.route(_write(self.tmp, "trav.epub", bad.getvalue()))


# =========================================================== WI-12 optional-tier absence
class OptionalTiers(FormatsTest):
    def test_docx_stdlib_when_enhancer_absent(self):
        # python-docx is absent in the default env; auto tier uses the stdlib handler
        self.assertFalse(importlib.util.find_spec("docx"))     # confirm absence
        t = docx.DocxHandler().extract(_write(self.tmp, "d.docx", make_docx([["hi"]]))).text
        self.assertEqual(t, "hi")

    def test_html_stdlib_when_enhancer_absent(self):
        self.assertFalse(importlib.util.find_spec("trafilatura"))
        t = html.HtmlHandler().extract(_write(self.tmp, "p.html", b"<p>hello</p>")).text
        self.assertIn("hello", t)


if __name__ == "__main__":
    unittest.main()
