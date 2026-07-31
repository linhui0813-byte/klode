"""Packaging integrity — guard against the wheel silently dropping a subpackage (the
`lode.lib.formats` bug) and against the version drifting out of its single source."""
import sys
import tomllib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


class Packaging(unittest.TestCase):
    def _pyproject(self) -> dict:
        return tomllib.load(open(REPO / "pyproject.toml", "rb"))

    def test_wheel_ships_every_package(self):
        # every dir under lode/ with an __init__.py must be covered by the build's package config,
        # so a new subpackage can never be silently excluded from the wheel.
        discovered = sorted(str(p.parent.relative_to(REPO)).replace("/", ".")
                            for p in (REPO / "lode").rglob("__init__.py"))
        self.assertIn("lode.lib.formats", discovered)          # the package this test exists to protect
        st = self._pyproject()["tool"]["setuptools"]
        pkgs = st.get("packages")
        if isinstance(pkgs, list):                             # explicit list must cover ALL discovered
            self.assertEqual(sorted(pkgs), discovered)
        else:                                                  # find-based discovery: a lode* include
            include = (pkgs or {}).get("find", {}).get("include", [])
            self.assertTrue(any(i in ("lode", "lode*", "lode.*") for i in include), include)

    def test_all_discovered_packages_import(self):
        import importlib
        for p in (REPO / "lode").rglob("__init__.py"):
            name = str(p.parent.relative_to(REPO)).replace("/", ".")
            importlib.import_module(name)                      # no broken/uninstallable package

    def test_version_is_single_sourced(self):
        proj = self._pyproject()["project"]
        self.assertIn("version", proj.get("dynamic", []))      # pyproject derives the version
        self.assertNotIn("version", proj)                      # not hardcoded here
        from lode.lib import __version__
        from lode.lib import mcp_server
        self.assertEqual(mcp_server.SERVER_VERSION, __version__)   # MCP derives from the one source

    def test_license_present_and_declared(self):
        self.assertTrue((REPO / "LICENSE").is_file())
        self.assertEqual(self._pyproject()["project"]["license"], "MIT")


if __name__ == "__main__":
    unittest.main()
