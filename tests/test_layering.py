"""Layering guard — makes the adapter boundary CI-enforced, not aspirational.

Uses `ast` (not a regex) so it sees in-function / lazy imports too — exactly the pattern a text scan
would miss. The package is flat by design; the one rule that carries a mechanism is: the two frontends
(`cli`, `mcp_server`) are top-level *adapters* — nothing depends on them, they don't depend on each
other, and both route consult through the shared `console`.
"""
import ast
import unittest
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "klode" / "lib"
ADAPTERS = {"cli", "mcp_server"}


def _sibling_imports(path: Path) -> set[str]:
    """Sibling `klode` modules this module imports — top-level AND inside functions (lazy)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 1:      # from .X import … / from . import X
            if node.module:
                mods.add(node.module.split(".")[0])
            else:
                mods.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            mods.update(a.name.split(".")[1] for a in node.names if a.name.startswith("klode."))
    return mods


class Layering(unittest.TestCase):
    def test_no_module_imports_an_adapter(self):
        for p in sorted(PKG.glob("*.py")):
            if p.stem == "__main__":                                  # the entry point legitimately runs cli
                continue
            leaked = _sibling_imports(p) & ADAPTERS
            self.assertFalse(leaked, f"{p.stem} imports adapter(s) {leaked} — adapters are top-level")

    def test_adapters_do_not_import_each_other(self):
        self.assertNotIn("mcp_server", _sibling_imports(PKG / "cli.py"))
        self.assertNotIn("cli", _sibling_imports(PKG / "mcp_server.py"))

    def test_both_frontends_route_through_console(self):
        self.assertIn("console", _sibling_imports(PKG / "cli.py"))
        self.assertIn("console", _sibling_imports(PKG / "mcp_server.py"))

    def test_console_does_not_import_adapters(self):
        self.assertFalse(_sibling_imports(PKG / "console.py") & ADAPTERS)

    def test_new_core_layer_is_low_level(self):
        # the canonical core sits BELOW the adapters: core is a stdlib-only leaf, the registry
        # depends only on core, and the services import no adapter.
        self.assertEqual(_sibling_imports(PKG / "core.py"), set())          # true leaf
        self.assertLessEqual(_sibling_imports(PKG / "opspec.py"), {"core"})
        self.assertFalse(_sibling_imports(PKG / "services.py") & ADAPTERS)


if __name__ == "__main__":
    unittest.main()
