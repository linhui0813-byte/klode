"""MCP tool schema is external API — tool names, `audience` enum, and param names must not drift
silently (the parity report §2 was exactly this drift)."""
import unittest

from lode.lib import mcp_server as mcp

_EXPECTED_TOOLS = {
    "list_lenses", "diagnose", "consult_dimension", "consult_framework",
    "search_sources", "zoom_card", "verify_quote",
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


if __name__ == "__main__":
    unittest.main()
