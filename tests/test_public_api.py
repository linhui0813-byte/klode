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
                     "dimension", "framework", "diagnose", "resolve", "search"):
            self.assertIn(name, klode.__all__)
            self.assertTrue(hasattr(klode, name), name)
        # the linter stays a module (name would shadow it); reachable, not a facade export
        from klode.lib.check import check as _check_fn
        self.assertTrue(callable(_check_fn))

    def test_importing_lodlib_does_not_eager_import_frontends_or_optional_deps(self):
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


if __name__ == "__main__":
    unittest.main()
