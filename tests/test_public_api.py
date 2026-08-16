"""The `klode` public-API facade — the Loop-A contract a consumer (Loop B) builds against."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.test_characterization import _write_fixture   # reuse the fixture builder


class PublicApiContract(unittest.TestCase):
    def test_loop_a_round_trip_via_facade_only(self):
        import klode.lib as klode
        tmp = Path(tempfile.mkdtemp(prefix="klode-api-"))
        try:
            cfg = klode.Config.load(_write_fixture(tmp))
            res = klode.consult(cfg, klode.ConsultRequest("dim1"))     # resolve + load + project
            self.assertEqual(res.outcome, "dimension")
            self.assertTrue(res.selected)                                # the Craft layer projected
            v = klode.verify(cfg, "src1", "y")                          # un-fakeable-citation primitive
            self.assertIsNotNone(v)
            self.assertTrue(v.found)
            self.assertIn("dim1", [d for d, _ in klode.diagnose(cfg, "the scene drags")])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_facade_declares_and_exposes_the_contract(self):
        import klode.lib as klode
        for name in ("Config", "consult", "ConsultRequest", "verify",
                     "dimension", "framework", "diagnose", "resolve", "search",
                     "retrieve_evidence", "EvidenceSearchResult", "EvidenceStatus", "RawPassage"):
            self.assertIn(name, klode.__all__)
            self.assertTrue(hasattr(klode, name), name)
        # the linter stays a module (name would shadow it); reachable, not a facade export
        from klode.lib.check import check as _check_fn
        self.assertTrue(callable(_check_fn))

    def test_importing_klode_lib_does_not_eager_import_frontends_or_optional_deps(self):
        # a FRESH process, so the test runner's own imports don't pollute sys.modules
        probe = ("import klode.lib, sys\n"
                 "leak=[m for m in ('klode.lib.cli','klode.lib.mcp_server','klode.lib.ingest',"
                 "'klode.lib.normalize','klode.lib.entail') if m in sys.modules]\n"
                 "print('LEAK '+','.join(leak) if leak else 'CLEAN')\n"
                 "sys.exit(1 if leak else 0)\n")
        p = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_internal_import_paths_preserved(self):
        # eval/*.py and the tests import these directly — the facade ADDS a surface, never removes one
        import klode.lib.query, klode.lib.common, klode.lib.build   # noqa: F401


class EveryPathParameterAcceptsAString(unittest.TestCase):
    """`Config` is on the public facade and the obvious way to call it is with a string. All three
    path entry points did `.resolve()` on the argument, so a `str` raised
    `AttributeError: 'str' object has no attribute 'resolve'` — the confusing-error-three-modules-
    deep failure the module's own docstring opens by promising not to produce."""

    FIX = Path(__file__).resolve().parent / "fixtures" / "kb-fixture"

    def test_load_and_find_take_a_string(self):
        from klode.lib import Config
        self.assertEqual(Config.load(str(self.FIX / "library.toml")).id, "kb-fixture")
        self.assertEqual(Config.load(start=str(self.FIX)).id, "kb-fixture")
        self.assertEqual(Config.find(str(self.FIX)), self.FIX / "library.toml")

    def test_a_string_and_a_path_agree(self):
        from klode.lib import Config
        self.assertEqual(Config.load(str(self.FIX / "library.toml")).config_path,
                         Config.load(self.FIX / "library.toml").config_path)

    def test_a_symlinked_config_anchors_the_same_way_however_it_is_opened(self):
        """Moving `.resolve()` into the coercion helper stopped it covering the DISCOVERED path,
        so a `library.toml` that is itself a symlink anchored every relative setting at the link's
        directory when found and at the target's when named — one file, two trees."""
        import os
        from klode.lib import Config
        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        real = base / "real"
        (real / "library" / "books").mkdir(parents=True)
        (real / "library" / "cards").mkdir(parents=True)
        (real / "library.toml").write_text(
            '[library]\nid = "t"\ndir = "library"\nshelves = ["books"]\n', encoding="utf-8")
        alt = base / "alt"
        (alt / "library" / "books").mkdir(parents=True)
        (alt / "library" / "cards").mkdir(parents=True)
        os.symlink(real / "library.toml", alt / "library.toml")
        self.assertEqual(Config.load(start=alt).root, Config.load(alt / "library.toml").root)

    def test_an_unavailable_cwd_is_a_ConfigError(self):
        from unittest import mock
        from klode.lib import Config
        from klode.lib.config import ConfigError
        with mock.patch("pathlib.Path.cwd", side_effect=FileNotFoundError("deleted cwd")):
            for fn in (Config.find, Config.load):
                with self.subTest(fn=fn.__name__), self.assertRaises(ConfigError):
                    fn()

    def test_a_toml_derived_path_also_fails_as_a_ConfigError(self):
        from klode.lib import Config
        from klode.lib.config import ConfigError
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "library.toml").write_text(
            '[library]\nid = "t"\ndir = "li\\u0000b"\nshelves = ["books"]\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            Config.load(tmp / "library.toml")

    def test_an_unusable_path_is_a_ConfigError_not_a_stray_builtin(self):
        from klode.lib import Config
        from klode.lib.config import ConfigError
        for label, bad in (("embedded null", "bad\0path"), ("wrong type", 7), ("a list", [])):
            with self.subTest(label=label):
                with self.assertRaises(ConfigError):
                    Config.load(bad)
                with self.assertRaises(ConfigError):
                    Config.load(start=bad)


if __name__ == "__main__":
    unittest.main()
