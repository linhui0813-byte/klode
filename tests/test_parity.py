"""WI-9 — the structural anti-drift gate: both surfaces are projected from the registry, so every
op's MCP/CLI projection exists and no surface has an orphan tool/verb absent from the registry."""
import argparse
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import cli, opspec                   # noqa: E402
from klode.lib import mcp_server as mcp            # noqa: E402
from klode.lib.core import CapabilityStatus         # noqa: E402


def _cli_subcommands() -> set[str]:
    p = cli.build_parser()
    sp = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    return set(sp.choices)


def _mcp_tool_names() -> set[str]:
    return {t["name"] for t in mcp.TOOLS}


class Parity(unittest.TestCase):
    def test_mcp_tools_equal_registry_mcp_projections(self):
        self.assertEqual(_mcp_tool_names(), set(opspec.mcp_names()))     # no orphan tool, none missing

    def test_every_mcp_tool_has_a_renderer_and_resolves_to_an_op(self):
        self.assertEqual(set(mcp.RENDERERS), _mcp_tool_names())
        for name in _mcp_tool_names():
            self.assertIsNotNone(opspec.by_mcp_name(name), name)

    def test_cli_subcommands_equal_registry_cli_projections(self):
        reg_cli = {s.cli for s in opspec.ops() if s.cli}
        self.assertEqual(_cli_subcommands(), reg_cli)                   # no orphan verb, none missing

    def test_capability_parity_review_experimental_and_not_on_mcp(self):
        self.assertIs(opspec.by_op_id("review").capability, CapabilityStatus.EXPERIMENTAL)
        self.assertNotIn("review", opspec.mcp_names())                 # a stub verb is not an MCP tool
        self.assertIsNotNone(opspec.by_cli_name("review"))             # it IS a (gated) CLI verb

    def test_drift_tripwire_bites(self):
        # a tool/verb present on a surface but absent from the registry must break parity
        rogue_mcp = _mcp_tool_names() | {"rogue_tool"}
        self.assertNotEqual(rogue_mcp, set(opspec.mcp_names()))
        rogue_cli = _cli_subcommands() | {"rogue_verb"}
        self.assertNotEqual(rogue_cli, {s.cli for s in opspec.ops() if s.cli})


if __name__ == "__main__":
    unittest.main()
