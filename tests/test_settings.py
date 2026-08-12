"""WI-6 — the settings resolver, and the argparse defect that made its precedence unimplementable.

An audit found the blocker concretely: `--tier` defaulted to `"auto"`, so `ingest x` and
`ingest x --tier auto` produced identical namespaces. With a value-default, the argument level of
the chain silently swallows environment, file, and default — there is no way to know a flag was
omitted. Every settings-backed flag therefore defaults to `None`.
"""
import os
import pathlib
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


class _EnvIsolated(unittest.TestCase):
    """Save, clear, and restore every `KLODE_*` variable around each test.

    This block was copy-pasted into three classes and could drift between them, which makes test
    isolation unreliable in exactly the way that is hardest to notice: a leaked variable changes a
    LATER test, not this one.
    """

    def setUp(self):
        super().setUp()
        self._saved_env = {v: os.environ.get(v) for s_ in settings.SPEC if (v := s_.env)}
        for var in self._saved_env:
            os.environ.pop(var, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self):
        for var, val in self._saved_env.items():
            if val is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = val


class Precedence(_EnvIsolated):
    """argument → environment → file → default, with the winner's origin recorded."""

    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-settings-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".klode").mkdir()
        self.file = self.tmp / ".klode" / "settings.toml"

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

    def test_an_explicitly_empty_environment_variable_is_an_error_not_absence(self):
        """Reversed deliberately. Treating `FOO=` as "unset" meant `KLODE_INGEST_TIER=$TYPO klode …`
        — the classic deployment bug, where the referenced variable does not exist — silently fell
        through to the file or the default while the operator believed the override was in force.
        Present-and-empty is broken configuration; `env -u` is how you actually unset."""
        os.environ["KLODE_INGEST_TIER"] = ""
        self.addCleanup(os.environ.pop, "KLODE_INGEST_TIER", None)
        with self.assertRaises(ValueError) as e:
            settings.resolve(None, home=self.tmp)
        self.assertIn("set but empty", str(e.exception))
        self.assertIn("env -u", str(e.exception))       # the message says how to fix it

    def test_a_genuinely_unset_variable_is_still_absence(self):
        os.environ.pop("KLODE_INGEST_TIER", None)
        self.assertEqual(settings.resolve(None, home=self.tmp).source("ingest.tier"),
                         settings.DEFAULT)


class InvalidInputFailsLoud(_EnvIsolated):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-settings2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / ".klode").mkdir()
        self.file = self.tmp / ".klode" / "settings.toml"

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

    def test_every_invalid_domain_is_rejected_from_every_source(self):
        """The previous tests covered selected TYPE errors and no domain errors, so a regression in
        `_validate` — an unknown tier, an out-of-range hurdle, an empty model — passed."""
        class _Args:
            def __init__(self, **kw): self.__dict__.update(kw)

        cases = [("ingest.tier", "bogus", "KLODE_INGEST_TIER", dict(tier="bogus")),
                 ("ingest.marker_mode", "turbo", "KLODE_MARKER_MODE", dict(marker_mode="turbo")),
                 ("judge.hurdle", 999, "KLODE_JUDGE_HURDLE", dict(hurdle=999)),
                 ("judge.hurdle", -1, "KLODE_JUDGE_HURDLE", dict(hurdle=-1)),
                 ("judge.permutations", 0, "KLODE_JUDGE_PERMUTATIONS", dict(permutations=0)),
                 ("judge.permutations", 99, "KLODE_JUDGE_PERMUTATIONS", dict(permutations=99)),
                 ("judge.model", "   ", "KLODE_JUDGE_MODEL", dict(model="   "))]
        for name, bad, env, argkw in cases:
            with self.subTest(f"{name}={bad!r} from file"):
                with self.assertRaises(ValueError):
                    settings.resolve(None, file_values={name: bad})
            with self.subTest(f"{name}={bad!r} from environment"):
                os.environ[env] = str(bad)
                try:
                    with self.assertRaises(ValueError):
                        settings.resolve(None, home=self.tmp)
                finally:
                    os.environ.pop(env, None)
            with self.subTest(f"{name}={bad!r} from argument"):
                with self.assertRaises(ValueError):
                    settings.resolve(_Args(**argkw), home=self.tmp)

    def test_every_builtin_default_is_itself_valid(self):
        # resolution inserts defaults without running them through _coerce/_validate, so an
        # out-of-domain default would ship silently
        for spec in settings.SPEC:
            if spec.default is None:
                continue
            with self.subTest(key=f"{spec.section}.{spec.key}"):
                settings._coerce(spec, spec.default, "built-in default")

    def test_a_malformed_file_value_is_rejected_even_when_shadowed(self):
        # validated only when it WON precedence, so a broken file passed today and failed the
        # moment the environment override was removed
        self.file.write_text('[ingest]\nverify = "not-a-boolean"\n', encoding="utf-8")
        os.environ["KLODE_INGEST_VERIFY"] = "true"
        self.addCleanup(os.environ.pop, "KLODE_INGEST_VERIFY", None)
        with self.assertRaises(ValueError):
            settings.resolve(None, home=self.tmp)

    def test_an_empty_typoed_section_is_rejected(self):
        # no keys to iterate, so the unknown-key check never fired
        self.file.write_text("[ingset]\n", encoding="utf-8")
        with self.assertRaises(ValueError) as e:
            settings.load(home=self.tmp)
        self.assertIn("unknown section", str(e.exception))

    def test_an_explicit_settings_path_that_is_missing_or_a_directory_is_an_error(self):
        # asked for BY NAME: returning {} made a typo'd path look like a working run at defaults
        for bad in (self.tmp / "no-such.toml", self.tmp):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    settings.load(bad)

    def test_an_unknown_programmatic_key_raises_rather_than_reading_as_unset(self):
        r = settings.resolve(None, home=self.tmp)
        with self.assertRaises(KeyError):
            r.value("ingest.verfy")
        with self.assertRaises(KeyError):
            r.source("ingest.verfy")

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
        """Isolated from the caller's real home and environment. These tests previously resolved
        whatever was in `~/.klode/settings.toml` and the ambient `KLODE_*` variables, so an
        unrelated local setting could change or break an output-format assertion."""
        import io
        from contextlib import redirect_stdout
        from unittest import mock
        from klode.lib.cli import build_parser, cmd_settings
        empty = pathlib.Path(tempfile.mkdtemp()) / "absent.toml"
        env = {v: "" for s_ in settings.SPEC if (v := s_.env)}
        buf = io.StringIO()
        with mock.patch.object(settings, "settings_path", lambda *a, **k: empty), \
             mock.patch.dict(os.environ, env, clear=False):
            for v in env:
                os.environ.pop(v, None)
            with redirect_stdout(buf):
                cmd_settings(build_parser().parse_args(["settings", "--explain"]))
        return buf.getvalue()

    def test_every_spec_appears_with_its_help_and_env(self):
        """Asserted per SETTING and against the WHOLE help text.

        The previous version matched roughly the first 40 characters against the whole output, so
        help could be truncated, rewritten past that point, or attached to the wrong key and still
        pass — and an empty `help` produced `assertIn("", out)`, which is always true, so the test
        named "with its help" did not require help to exist."""
        out = self._explain()
        blocks = {}
        current = None
        for line in out.splitlines():
            if line and not line.startswith(" "):
                current = line.strip()
                blocks[current] = []
            elif current:
                blocks[current].append(line.strip())
        for spec in settings.SPEC:
            name = f"{spec.section}.{spec.key}"
            with self.subTest(key=name):
                self.assertIn(name, blocks, "setting missing from --explain entirely")
                body = " ".join(blocks[name])
                self.assertTrue(spec.help.strip(), f"{name} has no help text at all")
                # the whole help, whitespace-normalised (it is wrapped in the output)
                self.assertIn(" ".join(spec.help.split()), " ".join(body.split()),
                              f"{name}'s help is truncated or attached to another key")
                self.assertIn(spec.env or "(none)", body)
                if spec.choices:
                    self.assertIn(repr(list(spec.choices))[1:-1].split(",")[0].strip(), body)

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


class SafetySettingsReachTheirConsumer(_EnvIsolated):
    """Resolution is not consumption. These tests would all have passed with `ingest.run()` no
    longer forwarding tier/verify, or the marker request no longer sending `mode` — the resolver
    would still resolve them correctly and change nothing about what runs."""

    def test_ingest_tier_and_verify_arrive_at_ingest(self):
        from unittest import mock
        from klode.lib import ingest as ing
        seen = {}

        def fake_ingest(cfg, source, shelf, **kw):
            seen.update(kw)
            raise RuntimeError("stop — the arguments are what is under test")
        os.environ["KLODE_INGEST_TIER"] = "docling"
        os.environ["KLODE_INGEST_VERIFY"] = "false"
        args = type("A", (), {"source": "x.pdf", "shelf": "books", "id": None, "lang": "eng",
                              "force": False, "format": None, "tier": None, "verify": None,
                              "accept_unverified": False})()
        with mock.patch.object(ing, "ingest", fake_ingest):
            ing.run(None, args)
        self.assertEqual(seen.get("tier"), "docling")
        self.assertIs(seen.get("verify"), False)

    def test_marker_mode_arrives_in_the_request_body(self):
        from unittest import mock
        from klode.lib.formats import pdf
        os.environ["KLODE_MARKER_URL"] = "http://marker.test:15002"
        os.environ["KLODE_MARKER_MODE"] = "balanced"
        payload = {"success": True, "output": f"\n\n{{0}}{'-' * 48}\n\nalpha",
                   "metadata": {"page_stats": [{"page_id": 0}]}}
        with mock.patch.object(pdf.urllib.request, "urlopen",
                               return_value=_FakeRespForSettings(payload)) as uo:
            pdf._marker_structured(pathlib.Path("tests/fixtures/pdfs/single-page.pdf"),
                                   pdf.marker_endpoint())
        body = uo.call_args[0][0].data
        self.assertIn(b'name="mode"', body)
        self.assertIn(b"balanced", body)


class _FakeRespForSettings:
    def __init__(self, payload):
        import json as _j
        self._b = _j.dumps(payload).encode()

    def read(self, n=-1):
        return self._b[:n] if (n is not None and n >= 0) else self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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

    def test_the_module_reads_no_credential_from_the_environment_at_all(self):
        """Over the module's CODE, not just its SPEC.

        The declarative check inspects `SPEC.env`, so a direct `os.environ["ANTHROPIC_API_KEY"]`
        anywhere else in settings.py would have satisfied it while doing exactly the thing the
        class name forbids. This walks every string constant the module can pass to an environment
        lookup."""
        import ast
        tree = ast.parse(pathlib.Path(settings.__file__).read_text(encoding="utf-8"))
        looked_up = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in ("get", "getenv") and node.args \
                    and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                looked_up.add(node.args[0].value)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
                    and isinstance(node.slice.value, str):
                looked_up.add(node.slice.value)
        for name in looked_up:
            self.assertFalse(any(c in name.lower() for c in self.CREDENTIALS),
                             f"settings.py reads {name!r} from the environment")

    def test_a_url_carrying_credentials_is_refused_from_every_source(self):
        """A prefix check called this validation and it was not.

        `http://user:password@host` starts with `http://`, so it passed — putting a credential in
        `settings.toml` and therefore in every backup, which is the ONE thing this module promises
        cannot happen. The class name asserted a property the code did not have.
        """
        hostile = ["http://user:password@host:15001",     # userinfo
                   "https://tok@host",                    # username alone is still a credential
                   "https://host/?api_key=sekret",        # a query is another place a token hides
                   "https://host/#token=sekret"]
        for url in hostile:
            for key in ("ingest.docling_url", "ingest.marker_url"):
                with self.subTest(url=url, key=key):
                    with self.assertRaises(ValueError):
                        settings.resolve(None, file_values={key: url})
        # ...and from the environment, which is the same validator or it is not one
        for url in hostile:
            with self.subTest(env=url):
                os.environ["KLODE_DOCLING_URL"] = url
                self.addCleanup(os.environ.pop, "KLODE_DOCLING_URL", None)
                with self.assertRaises(ValueError):
                    settings.resolve(None, home=pathlib.Path(tempfile.mkdtemp()))

    def test_a_structurally_broken_endpoint_is_refused_before_it_is_used(self):
        # each of these only fails at the first HTTP call, where it gets blamed on the backend
        for url in ("http://", "https://h#f", "http://host:99999", "http://ho st", "http://host\n"):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    settings.resolve(None, file_values={"ingest.docling_url": url})

    def test_a_legitimate_endpoint_still_resolves(self):
        # the guard must not refuse the documented deployment — plain http on a private
        # interface is a deliberate allowance, recorded in _validate_url
        for url in ("http://10.0.0.5:15001", "https://docling.example.com",
                    "http://localhost:15002/", "http://[::1]:15001"):
            with self.subTest(url=url):
                r = settings.resolve(None, file_values={"ingest.docling_url": url})
                self.assertEqual(r.value("ingest.docling_url"), url)

    def test_the_docling_endpoint_is_configurable_and_scheme_checked(self):
        # topology, deliberately allowed — and validated at the boundary rather than at first use
        self.assertIn("ingest.docling_url", {f"{s.section}.{s.key}" for s in settings.SPEC})
        spec = next(s for s in settings.SPEC if s.key == "docling_url")
        self.assertEqual(spec.env, "KLODE_DOCLING_URL")     # the env override still wins
        self.assertIsNone(spec.default)                     # absent, never a guessed localhost


if __name__ == "__main__":
    unittest.main()
