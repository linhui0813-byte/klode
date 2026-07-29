"""MCP tool schema is external API — tool names, `audience` enum, and param names must not drift
silently (the parity report §2 was exactly this drift)."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lode.lib import mcp_server as mcp
from lode.lib.config import Config, _SLUG_RE

_EXPECTED_TOOLS = {
    "list_lenses", "diagnose", "consult_dimension", "consult_framework",
    "search_sources", "zoom_card", "verify_quote",
    "list_kbs",
}


class McpSchema(unittest.TestCase):
    def test_tool_names_stable(self):
        self.assertEqual({t["name"] for t in mcp.TOOLS}, _EXPECTED_TOOLS)

    def test_dispatch_matches_tools(self):
        self.assertEqual({t["name"] for t in mcp.TOOLS}, set(mcp.DISPATCH))

    def test_consult_dimension_contract(self):
        t = next(t for t in mcp.TOOLS if t["name"] == "consult_dimension")
        props = t["inputSchema"]["properties"]
        self.assertEqual(set(t["inputSchema"]["required"]), {"dimension"})
        self.assertIn("audience", props)
        self.assertIn("section", props)
        self.assertEqual(props["audience"]["enum"], ["writer", "engine", "full"])

    def test_diagnose_contract(self):
        t = next(t for t in mcp.TOOLS if t["name"] == "diagnose")
        self.assertEqual(set(t["inputSchema"]["required"]), {"symptom"})

    def test_schema_descriptions_carry_no_dead_dimension_names(self):
        # §2: descriptions must not hardcode dimension names (they drift) — they point at list_lenses
        import json
        blob = json.dumps(mcp.TOOLS)
        for dead in ("viewpoint,", "presence,", '"emotion"'):
            self.assertNotIn(dead, blob)


class ServerName(unittest.TestCase):
    """WI-6 — serverInfo.name is derived per-KB from the id (`lode-<id>`), not a hardcoded constant,
    so per-KB servers get distinct `mcp__<name>__*` tool namespaces."""
    FIX = Path(__file__).resolve().parent / "fixtures" / "kb-fixture" / "library.toml"

    def _cfg_with_id(self, kb_id: str) -> Config:
        tmp = Path(tempfile.mkdtemp(prefix="lodlib-sn-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "library").mkdir(parents=True)
        (tmp / "library.toml").write_text(
            f'[library]\nid = "{kb_id}"\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            "[bibliography]\nenabled = false\n", encoding="utf-8")
        return Config.load(tmp / "library.toml")

    def test_name_derived_from_kb_id(self):
        self.assertEqual(mcp._server_name(Config.load(self.FIX)), "lode-kb-fixture")

    def test_two_ids_yield_two_names(self):
        a, b = self._cfg_with_id("alpha"), self._cfg_with_id("beta")
        self.assertEqual(mcp._server_name(a), "lode-alpha")
        self.assertNotEqual(mcp._server_name(a), mcp._server_name(b))

    def test_fallback_when_no_id(self):
        self.assertEqual(mcp._server_name(SimpleNamespace(id="")), "lode")

    def test_derived_name_is_prefix_safe_slug(self):
        self.assertTrue(_SLUG_RE.fullmatch(mcp._server_name(Config.load(self.FIX))))

    def test_initialize_reports_the_derived_name(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(Config.load(self.FIX),
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        reply = json.loads(buf.getvalue())
        self.assertEqual(reply["result"]["serverInfo"]["name"], "lode-kb-fixture")


if __name__ == "__main__":
    unittest.main()
