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


class EverySettingIsDiscoverable(unittest.TestCase):
    """A setting nobody can find is a setting that does not exist for them.

    Every `Spec` carried `help`, `choices`, `env` and a default, and none of it was printed
    anywhere — `marker_mode`'s whole reason for existing was reachable only by reading source."""

    def _explain(self):
        import io
        from contextlib import redirect_stdout
        from klode.lib.cli import build_parser, cmd_settings
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_settings(build_parser().parse_args(["settings", "--explain"]))
        return buf.getvalue()

    def test_every_spec_appears_with_its_help_and_env(self):
        out = self._explain()
        for spec in settings.SPEC:
            with self.subTest(key=f"{spec.section}.{spec.key}"):
                self.assertIn(f"{spec.section}.{spec.key}", out)
                self.assertIn(spec.help.split(".")[0].split("—")[0].strip()[:40], out)
                if spec.env:
                    self.assertIn(spec.env, out)

    def test_closed_domains_are_shown_so_a_valid_value_is_guessable(self):
        out = self._explain()
        self.assertIn("'fast', 'balanced'", out)
        self.assertIn("'marker'", out)               # the tier list, from the SPEC

    def test_a_deliberately_unset_default_says_so_rather_than_printing_None(self):
        self.assertIn("unset on purpose", self._explain())

    def test_an_unconsumed_setting_says_so_where_a_user_will_read_it(self):
        # `judge.model` had its "changes nothing today" caveat in a module docstring only, so
        # `--explain` described it as if it worked. A caveat only the source states is not a caveat.
        out = self._explain()
        for key in ("judge.model", "judge.permutations"):
            with self.subTest(key=key):
                spec = next(s for s in settings.SPEC if f"{s.section}.{s.key}" == key)
                self.assertIn("NOT YET CONSUMED", spec.help,
                              "wiring this up? remove the marker in the same change")
        self.assertIn("NOT YET CONSUMED", out)

    def test_a_consumed_setting_does_not_carry_the_warning(self):
        for key in ("judge.hurdle", "ingest.tier", "ingest.verify",
                    "ingest.docling_url", "ingest.marker_url", "ingest.marker_mode"):
            with self.subTest(key=key):
                spec = next(s for s in settings.SPEC if f"{s.section}.{s.key}" == key)
                self.assertNotIn("NOT YET CONSUMED", spec.help)

    def test_the_terse_listing_points_at_the_explanation(self):
        import io
        from contextlib import redirect_stdout
        from klode.lib.cli import build_parser, cmd_settings
        buf = io.StringIO()
        with redirect_stdout(buf):
            cmd_settings(build_parser().parse_args(["settings"]))
        self.assertIn("--explain", buf.getvalue())


class JudgeHurdleActuallyReachesTheVerdict(unittest.TestCase):
    """It was declared, printed by `klode settings`, and read by nothing — `_svc_review` hardcoded
    60. Asserting the value RESOLVES would have proved nothing; this asserts it ARRIVES."""

    def setUp(self):
        self._saved = os.environ.get("KLODE_JUDGE_HURDLE")
        self.addCleanup(lambda: (os.environ.__setitem__("KLODE_JUDGE_HURDLE", self._saved)
                                 if self._saved is not None
                                 else os.environ.pop("KLODE_JUDGE_HURDLE", None)))

    def test_the_configured_hurdle_arrives_at_the_review_service(self):
        from klode.lib import cli
        seen = {}

        def fake_run(args, op, params):
            seen.update(params)
            raise ValueError("stop here — the parameter is what is under test")
        real = cli._run
        self.addCleanup(setattr, cli, "_run", real)
        cli._run = fake_run

        os.environ["KLODE_JUDGE_HURDLE"] = "85"
        cli.cmd_review(cli.build_parser().parse_args(["review", "draft text", "pacing"]))
        self.assertEqual(seen["hurdle"], 85, "the service would have used its hardcoded 60")

    def test_an_out_of_range_hurdle_is_refused_before_any_review_runs(self):
        from klode.lib import cli
        called = []
        real = cli._run
        self.addCleanup(setattr, cli, "_run", real)
        cli._run = lambda *a, **k: called.append(1)
        os.environ["KLODE_JUDGE_HURDLE"] = "999"
        rc = cli.cmd_review(cli.build_parser().parse_args(["review", "d", "pacing"]))
        self.assertEqual(rc, 1)
        self.assertEqual(called, [], "a bad setting must stop the run, not be clamped silently")


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
