"""MCP tool schema is external API — tool names, `audience` enum, and param names must not drift
silently (the parity report §2 was exactly this drift)."""
import contextlib
import io
import json
import unittest
from pathlib import Path

from lode.lib import mcp_server as mcp
from lode.lib.config import Config, _SLUG_RE
from lode.lib.pool import KBPool

_EXPECTED_TOOLS = {
    "list_lenses", "diagnose", "consult_dimension", "consult_framework",
    "search_sources", "zoom_card", "verify_quote",
    "list_kbs",
}


class McpSchema(unittest.TestCase):
    def test_tool_names_stable(self):
        self.assertEqual({t["name"] for t in mcp.TOOLS}, _EXPECTED_TOOLS)

    def test_dispatch_covers_tools_except_router_handled_list_kbs(self):
        tool_names = {t["name"] for t in mcp.TOOLS}
        self.assertEqual(tool_names, _EXPECTED_TOOLS)                        # 8 tools in the schema
        # list_kbs is pool-scoped and handled by the router, so it is deliberately NOT in DISPATCH
        self.assertEqual(set(mcp.DISPATCH), _EXPECTED_TOOLS - {"list_kbs"})

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


class KbSelector(unittest.TestCase):
    """WI-5 — the `kb` selector is on every KB-scoped tool (not list_kbs) and never in `required`."""

    def _by_name(self):
        return {t["name"]: t for t in mcp.TOOLS}

    def test_kb_on_grounding_and_discovery_absent_on_list_kbs(self):
        tools = self._by_name()
        for name in ("consult_dimension", "consult_framework", "zoom_card", "verify_quote",
                     "search_sources", "list_lenses", "diagnose"):
            self.assertIn("kb", tools[name]["inputSchema"]["properties"], name)
        self.assertNotIn("kb", tools["list_kbs"]["inputSchema"].get("properties", {}))

    def test_kb_is_never_required(self):
        for t in mcp.TOOLS:
            self.assertNotIn("kb", t["inputSchema"].get("required", []))
        self.assertEqual(set(self._by_name()["consult_dimension"]["inputSchema"]["required"]),
                         {"dimension"})

    def test_grounding_and_discovery_kb_descriptions_differ(self):
        tools = self._by_name()
        grounding = tools["verify_quote"]["inputSchema"]["properties"]["kb"]["description"].lower()
        discovery = tools["search_sources"]["inputSchema"]["properties"]["kb"]["description"].lower()
        self.assertIn("required when", grounding)
        self.assertIn("fan out", discovery)
        self.assertNotEqual(grounding, discovery)


class ServerName(unittest.TestCase):
    """serverInfo.name is the single stable brand 'lode'. Clients namespace tools by the client-config
    KEY (e.g. `.mcp.json`), not by serverInfo.name, so this stays constant across KBs."""
    FIX = Path(__file__).resolve().parent / "fixtures" / "kb-fixture" / "library.toml"

    def test_server_name_is_lode(self):
        self.assertEqual(mcp.SERVER_NAME, "lode")

    def test_server_name_is_prefix_safe_slug(self):
        self.assertTrue(_SLUG_RE.fullmatch(mcp.SERVER_NAME))

    def test_initialize_reports_lode(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(KBPool.single(Config.load(self.FIX)),
                       {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        reply = json.loads(buf.getvalue())
        self.assertEqual(reply["result"]["serverInfo"]["name"], "lode")


if __name__ == "__main__":
    unittest.main()
