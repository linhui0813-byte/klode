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

    def test_a_docling_url_without_a_scheme_is_rejected_at_the_boundary(self):
        # a typo'd scheme otherwise surfaces as a backend failure mid-ingest, blamed on the backend
        self.file.write_text('[ingest]\ndocling_url = "example.invalid:15001"\n', encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            settings.resolve(None, home=self.tmp)
        self.assertIn("must start with", str(e.exception))

    def test_the_same_check_applies_to_the_environment_override(self):
        os.environ["KLODE_DOCLING_URL"] = "ftp://elsewhere"
        self.addCleanup(os.environ.pop, "KLODE_DOCLING_URL", None)
        with self.assertRaises(ValueError):
            settings.resolve(None, home=self.tmp)

    def test_a_valid_docling_url_resolves_from_the_file(self):
        self.file.write_text('[ingest]\ndocling_url = "http://example.invalid:15001"\n',
                             encoding="utf-8")
        r = settings.resolve(None, home=self.tmp)
        self.assertEqual(r.value("ingest.docling_url"), "http://example.invalid:15001")
        self.assertEqual(r.source("ingest.docling_url"), settings.FILE)

    def test_boolean_environment_values_parse_both_ways(self):
        for raw, want in (("true", True), ("1", True), ("on", True),
                          ("false", False), ("0", False), ("off", False)):
            with self.subTest(raw=raw):
                os.environ["KLODE_INGEST_VERIFY"] = raw
                try:
                    self.assertIs(settings.resolve(None, home=self.tmp).value("ingest.verify"), want)
                finally:
                    os.environ.pop("KLODE_INGEST_VERIFY", None)


class OneTierListNotThree(unittest.TestCase):
    """The tier list was declared in three places — argparse choices, the settings SPEC, and the
    extractor table — with nothing binding them. They drifted exactly as that invites: `marker` was
    a valid tier everywhere except argparse, so `--tier marker` was rejected while the identical
    value in settings.toml worked. One capability, reachable from one surface and not the other.

    argparse now derives its choices from the SPEC. These tests bind the SPEC to the extractor
    table, which is the pair a cycle-free import cannot bind at runtime."""

    def _spec(self):
        return next(s for s in settings.SPEC if (s.section, s.key) == ("ingest", "tier"))

    def test_every_settable_tier_has_an_extractor(self):
        from klode.lib.formats import pdf
        settable = set(self._spec().choices) - {"auto"}
        self.assertEqual(settable, set(pdf._EXTRACTORS),
                         "a tier nobody can extract with, or an extractor nobody can select")

    def test_the_cli_offers_exactly_the_settable_tiers(self):
        action = next(a for a in build_parser()._subparsers._group_actions[0]
                      .choices["ingest"]._actions if a.dest == "tier")
        self.assertEqual(tuple(action.choices), self._spec().choices)

    def test_marker_is_selectable_from_the_command_line(self):
        # the concrete regression: this raised SystemExit
        self.assertEqual(build_parser().parse_args(
            ["ingest", "x.pdf", "--shelf", "s", "--tier", "marker"]).tier, "marker")


class SecretsStayOut(unittest.TestCase):
    """The line is CREDENTIALS, not 'anything that looks infrastructural'.

    The earlier version banned `url`, `endpoint` and `docling` alongside `api_key` and `token`.
    That conflated topology with a secret: a docling-serve URL grants nothing on its own — the
    control is where the service binds — while an API key grants everything. Banning both by the
    same rule made the rule easy to relax for the wrong one. So the credential ban is now absolute
    and independently checked, and topology is allowed."""

    CREDENTIALS = ("api_key", "apikey", "token", "secret", "password", "passwd",
                   "credential", "private_key")

    def test_no_credential_is_a_settings_key(self):
        # a key in a file is a key in a backup — this one must never soften
        names = {f"{s.section}.{s.key}" for s in settings.SPEC}
        for forbidden in self.CREDENTIALS:
            self.assertFalse(any(forbidden in n for n in names),
                             f"{forbidden} must never be settable from a file")

    def test_no_credential_is_read_from_the_environment_by_this_module_either(self):
        envs = {s.env for s in settings.SPEC if s.env}
        self.assertNotIn("ANTHROPIC_API_KEY", envs)
        for env in envs:
            self.assertFalse(any(c in env.lower() for c in self.CREDENTIALS), env)

    def test_the_docling_endpoint_is_configurable_and_scheme_checked(self):
        # topology, deliberately allowed — and validated at the boundary rather than at first use
        self.assertIn("ingest.docling_url", {f"{s.section}.{s.key}" for s in settings.SPEC})
        spec = next(s for s in settings.SPEC if s.key == "docling_url")
        self.assertEqual(spec.env, "KLODE_DOCLING_URL")     # the env override still wins
        self.assertIsNone(spec.default)                     # absent, never a guessed localhost


if __name__ == "__main__":
    unittest.main()
