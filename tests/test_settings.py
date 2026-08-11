"""WI-6 — the settings resolver, and the argparse defect that made its precedence unimplementable.

An audit found the blocker concretely: `--tier` defaulted to `"auto"`, so `ingest x` and
`ingest x --tier auto` produced identical namespaces. With a value-default, the argument level of
the chain silently swallows environment, file, and default — there is no way to know a flag was
omitted. Every settings-backed flag therefore defaults to `None`.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import settings                                            # noqa: E402
from klode.lib.cli import build_parser                                    # noqa: E402


class ArgparseCanDistinguishOmissionFromChoice(unittest.TestCase):
    """The defect that made the precedence chain unimplementable."""

    def test_omitted_and_explicit_tier_are_distinguishable(self):
        p = build_parser()
        omitted = p.parse_args(["ingest", "x.pdf", "--shelf", "books"])
        explicit = p.parse_args(["ingest", "x.pdf", "--shelf", "books", "--tier", "auto"])
        self.assertIsNone(omitted.tier)                 # silence
        self.assertEqual(explicit.tier, "auto")         # a deliberate choice
        self.assertNotEqual(omitted.tier, explicit.tier)

    def test_verify_is_a_tri_state_boolean(self):
        p = build_parser()
        base = ["ingest", "x.pdf", "--shelf", "books"]
        self.assertIsNone(p.parse_args(base).verify)                       # omitted
        self.assertTrue(p.parse_args(base + ["--verify"]).verify)          # explicit on
        self.assertFalse(p.parse_args(base + ["--no-verify"]).verify)      # explicit off


class Precedence(unittest.TestCase):
    """argument → environment → file → default, with the winner's origin recorded."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".klode").mkdir()
        self.file = self.tmp / ".klode" / "settings.toml"
        # SAVE and restore: the previous version deleted every KLODE_* var without putting them
        # back, contaminating later tests and the caller's environment
        self._saved = {v: os.environ.get(v) for s_ in settings.SPEC if (v := s_.env)}
        for var in self._saved:
            os.environ.pop(var, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for var, val in self._saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val

    def _write(self, body: str):
        self.file.write_text(body, encoding="utf-8")

    class _Args:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    def test_the_full_matrix(self):
        self._write('[ingest]\ntier = "docling"\n')
        os.environ["KLODE_INGEST_TIER"] = "xberg"
        self.addCleanup(os.environ.pop, "KLODE_INGEST_TIER", None)

        cases = [
            ("argument wins over everything", self._Args(tier="pdftotext"), "pdftotext", settings.ARG),
            ("environment wins over file", self._Args(tier=None), "xberg", settings.ENV),
        ]
        for label, args, value, source in cases:
            with self.subTest(label):
                r = settings.resolve(args, home=self.tmp)
                self.assertEqual(r.value("ingest.tier"), value)
                self.assertEqual(r.source("ingest.tier"), source)

        os.environ.pop("KLODE_INGEST_TIER")
        r = settings.resolve(self._Args(tier=None), home=self.tmp)
        self.assertEqual((r.value("ingest.tier"), r.source("ingest.tier")), ("docling", settings.FILE))

        self.file.unlink()
        r = settings.resolve(self._Args(tier=None), home=self.tmp)
        self.assertEqual((r.value("ingest.tier"), r.source("ingest.tier")), ("auto", settings.DEFAULT))

    def test_a_missing_file_is_not_an_error(self):
        r = settings.resolve(None, home=self.tmp)
        self.assertEqual(r.source("ingest.tier"), settings.DEFAULT)

    def test_an_unset_setting_reports_unset_not_a_fabricated_default(self):
        # judge.model has no default ON PURPOSE (self-enhancement bias)
        r = settings.resolve(None, home=self.tmp)
        self.assertIsNone(r.value("judge.model"))
        self.assertEqual(r.source("judge.model"), settings.UNSET)

    def test_an_empty_environment_variable_is_absence_not_a_value(self):
        os.environ["KLODE_INGEST_TIER"] = ""
        self.addCleanup(os.environ.pop, "KLODE_INGEST_TIER", None)
        r = settings.resolve(None, home=self.tmp)
        self.assertEqual(r.source("ingest.tier"), settings.DEFAULT)


class InvalidInputFailsLoud(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-settings2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".klode").mkdir()
        self.file = self.tmp / ".klode" / "settings.toml"
        # These tests SET KLODE_* vars and then pop them unconditionally, which deletes a value the
        # caller had before the suite ran. Same defect Precedence.setUp already fixed; the class
        # boundary is not a reason for it to survive here.
        self._saved = {v: os.environ.get(v) for s_ in settings.SPEC if (v := s_.env)}
        for var in self._saved:
            os.environ.pop(var, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for var, val in self._saved.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val

    def test_an_unknown_key_is_rejected_rather_than_ignored(self):
        # silently dropping it is how a setting "does nothing" with no explanation
        self.file.write_text('[ingest]\nteir = "auto"\n', encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            settings.load(home=self.tmp)
        self.assertIn("unknown setting", str(e.exception))

    def test_malformed_toml_is_rejected(self):
        self.file.write_text("[ingest\n", encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            settings.load(home=self.tmp)
        self.assertIn("invalid TOML", str(e.exception))

    def test_a_wrongly_typed_file_value_is_rejected(self):
        self.file.write_text('[judge]\npermutations = "two"\n', encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            settings.resolve(None, home=self.tmp)
        self.assertIn("must be an integer", str(e.exception))

    def test_a_non_boolean_environment_value_is_rejected(self):
        os.environ["KLODE_INGEST_VERIFY"] = "maybe"
        self.addCleanup(os.environ.pop, "KLODE_INGEST_VERIFY", None)
        with self.assertRaises(ValueError) as e:
            settings.resolve(None, home=self.tmp)
        self.assertIn("not a boolean", str(e.exception))

    def test_boolean_environment_values_parse_both_ways(self):
        for raw, want in (("true", True), ("1", True), ("on", True),
                          ("false", False), ("0", False), ("off", False)):
            with self.subTest(raw=raw):
                os.environ["KLODE_INGEST_VERIFY"] = raw
                try:
                    self.assertIs(settings.resolve(None, home=self.tmp).value("ingest.verify"), want)
                finally:
                    os.environ.pop("KLODE_INGEST_VERIFY", None)


class SecretsStayOut(unittest.TestCase):
    def test_no_credential_or_endpoint_is_a_settings_key(self):
        # keys in a file are keys in a backup; KLODE_DOCLING_URL names a private host and its
        # env-only placement is deliberate (recorded in formats/pdf.py)
        names = {f"{s.section}.{s.key}" for s in settings.SPEC}
        envs = {s.env for s in settings.SPEC if s.env}
        for forbidden in ("api_key", "key", "token", "secret", "docling", "endpoint", "url"):
            self.assertFalse(any(forbidden in n for n in names), f"{forbidden} must not be settable")
        self.assertNotIn("ANTHROPIC_API_KEY", envs)
        self.assertNotIn("KLODE_DOCLING_URL", envs)


if __name__ == "__main__":
    unittest.main()
