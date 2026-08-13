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

class ProseAndJsonAgreeOnOutCOMES(unittest.TestCase):
    """Name parity is not behaviour parity.

    The registry check compares subcommand names against MCP tool names, so it still passed with
    routing through `services.execute` deleted, or with prose and JSON disagreeing completely about
    what a given request means. Both divergences that an audit found — `--json search ""`
    succeeding where prose failed, and `--limit 0` differing between surfaces — were invisible to
    it. These drive both renderers over the same inputs and compare the EXIT CODES, which is the
    part a caller actually branches on.
    """

    CASES = [
        (["search", ""], "an empty query"),
        (["search", "definitely-no-such-term-xyz"], "a query with no hits"),
        (["zoom", "nosuchcard", "--level", "meta"], "a missing card"),
        (["zoom", "brevity", "--level", "meta"], "an ordinary hit"),
        (["zoom", "brevity", "--level", "content", "--grep", "no-such-phrase-xyz"], "a failed grep"),
    ]

    def setUp(self):
        import shutil, tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-parity-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        kb = self.tmp / "kb"
        shutil.copytree(REPO / "tests" / "fixtures" / "kb-fixture", kb)
        self.cfg = str(kb / "library.toml")

    def _rc(self, argv):
        import contextlib, io
        from klode.lib.cli import main
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                return main(["-c", self.cfg] + argv)
            except SystemExit as e:
                return e.code if isinstance(e.code, int) else 2

    def test_both_surfaces_agree_on_the_exit_code(self):
        for argv, label in self.CASES:
            with self.subTest(label):
                self.assertEqual(self._rc(argv), self._rc(["--json"] + argv),
                                 f"prose and JSON disagree on {label}")

    def test_json_output_is_parseable_wherever_it_is_offered(self):
        import contextlib, io, json as _json
        from klode.lib.cli import main, JSON_COMMANDS
        for argv, label in self.CASES:
            if argv[0] not in JSON_COMMANDS:
                continue
            with self.subTest(label):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
                    try:
                        main(["-c", self.cfg, "--json"] + argv)
                    except SystemExit:
                        continue
                out = buf.getvalue().strip()
                if out:
                    _json.loads(out)

if __name__ == "__main__":
    unittest.main()
