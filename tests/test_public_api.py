"""The `lodlib` public-API facade — the Loop-A contract a consumer (Loop B) builds against."""
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
        import lode.lib as lodlib
        tmp = Path(tempfile.mkdtemp(prefix="lodlib-api-"))
        try:
            cfg = lodlib.Config.load(_write_fixture(tmp))
            res = lodlib.consult(cfg, lodlib.ConsultRequest("dim1"))     # resolve + load + project
            self.assertEqual(res.outcome, "dimension")
            self.assertTrue(res.selected)                                # the Craft layer projected
            v = lodlib.verify(cfg, "src1", "y")                          # un-fakeable-citation primitive
            self.assertIsNotNone(v)
            self.assertTrue(v.found)
            self.assertIn("dim1", [d for d, _ in lodlib.diagnose(cfg, "the scene drags")])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_facade_declares_and_exposes_the_contract(self):
        import lode.lib as lodlib
        for name in ("Config", "consult", "ConsultRequest", "verify",
                     "dimension", "framework", "diagnose", "resolve", "search"):
            self.assertIn(name, lodlib.__all__)
            self.assertTrue(hasattr(lodlib, name), name)
        # the linter stays a module (name would shadow it); reachable, not a facade export
        from lode.lib.check import check as _check_fn
        self.assertTrue(callable(_check_fn))

    def test_importing_lodlib_does_not_eager_import_frontends_or_optional_deps(self):
        # a FRESH process, so the test runner's own imports don't pollute sys.modules
        probe = ("import lode.lib, sys\n"
                 "leak=[m for m in ('lode.lib.cli','lode.lib.mcp_server','lode.lib.ingest',"
                 "'lode.lib.normalize','lode.lib.entail') if m in sys.modules]\n"
                 "print('LEAK '+','.join(leak) if leak else 'CLEAN')\n"
                 "sys.exit(1 if leak else 0)\n")
        p = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)

    def test_internal_import_paths_preserved(self):
        # eval/*.py and the tests import these directly — the facade ADDS a surface, never removes one
        import lode.lib.query, lode.lib.common, lode.lib.build   # noqa: F401


if __name__ == "__main__":
    unittest.main()
