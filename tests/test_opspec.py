"""WI-3 — the operation registry (klode/lib/opspec.py) is the executable form of the spec. It must
agree with dev-docs/SPEC-operations.md op-for-op, resolve every legacy MCP/CLI name, carry the right
capability flags, and reject a malformed table."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import opspec                                    # noqa: E402
from klode.lib.core import CapabilityStatus                     # noqa: E402
from tests.test_spec_operations import SPEC, parse_ops, parse_aliases   # noqa: E402


class OpRegistry(unittest.TestCase):
    def test_registry_op_ids_equal_spec_op_ids(self):
        spec_ids = {op["op-id"] for op in parse_ops(SPEC.read_text(encoding="utf-8"))}
        reg_ids = {s.op_id for s in opspec.ops()}
        self.assertEqual(reg_ids, spec_ids)                    # activates WI-1's consistency check

    def test_legacy_mcp_aliases_resolve_to_the_documented_op(self):
        for tool, op_id in parse_aliases(SPEC.read_text(encoding="utf-8")).items():
            s = opspec.by_mcp_name(tool)
            self.assertIsNotNone(s, tool)
            self.assertEqual(s.op_id, op_id, tool)

    def test_every_legacy_cli_subcommand_resolves(self):
        for cmd in ("init", "build", "check", "ingest", "normalize",
                    "search", "zoom", "consult", "diagnose", "kbs"):
            self.assertIsNotNone(opspec.by_cli_name(cmd), cmd)

    def test_capability_flags(self):
        self.assertEqual(opspec.by_op_id("review").capability, CapabilityStatus.EXPERIMENTAL)
        for stable in ("search", "consult", "zoom", "verify", "kbs.list"):
            self.assertEqual(opspec.by_op_id(stable).capability, CapabilityStatus.STABLE)

    def test_mcp_names_are_exactly_the_frozen_eight(self):
        self.assertEqual(
            set(opspec.mcp_names()),
            {"list_kbs", "list_lenses", "search_sources", "diagnose",
             "consult_dimension", "consult_framework", "zoom_card", "verify_quote"})

    def test_no_duplicate_op_ids_or_mcp_names(self):
        ids = [s.op_id for s in opspec.ops()]
        self.assertEqual(len(ids), len(set(ids)))
        mcp = list(opspec.mcp_names())
        self.assertEqual(len(mcp), len(set(mcp)))

    def test_validate_rejects_a_malformed_table(self):
        dup_op = (opspec.OpSpec("x", "kb", CapabilityStatus.STABLE, "x", ("t1",)),
                  opspec.OpSpec("x", "kb", CapabilityStatus.STABLE, "y", ("t2",)))
        dup_mcp = (opspec.OpSpec("a", "kb", CapabilityStatus.STABLE, "a", ("t",)),
                   opspec.OpSpec("b", "kb", CapabilityStatus.STABLE, "b", ("t",)))
        dup_cli = (opspec.OpSpec("a", "kb", CapabilityStatus.STABLE, "same", ()),
                   opspec.OpSpec("b", "kb", CapabilityStatus.STABLE, "same", ()))
        bad_scope = (opspec.OpSpec("c", "weird", CapabilityStatus.STABLE, "c", ()),)
        for bad in (dup_op, dup_mcp, dup_cli, bad_scope):
            with self.assertRaises(ValueError):
                opspec._validate(bad)

    def test_validate_allows_multiple_cli_none(self):
        # cli=None (not CLI-projected) must not collide with another cli=None op
        ok = (opspec.OpSpec("a", "kb", CapabilityStatus.STABLE, None, ("t1",)),
              opspec.OpSpec("b", "kb", CapabilityStatus.STABLE, None, ("t2",)))
        opspec._validate(ok)                                   # no raise


if __name__ == "__main__":
    unittest.main()
