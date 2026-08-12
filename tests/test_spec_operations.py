"""WI-1 — the canonical operations spec (dev-docs/SPEC-operations.md) is the machine-parseable
contract the registry (WI-3) and the parity test (WI-9) are checked against. The parse helpers here
are imported by those later tests, so the spec and the code can never silently disagree."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

SPEC = REPO / "dev-docs" / "SPEC-operations.md"
_OP_COLUMNS = ("op-id", "scope", "result", "capability", "cli", "mcp")
_PUBLIC_MCP_TOOLS = {
    "list_kbs", "list_lenses", "diagnose", "consult_dimension",
    "consult_framework", "search_sources", "zoom_card", "verify_quote",
    "retrieve_evidence",
}


def _first_table(text: str, heading_prefix: str) -> list[list[str]]:
    """The rows (each a list of stripped cells) of the first markdown table after a `## heading`."""
    lines = text.splitlines()
    i = next((n for n, ln in enumerate(lines) if ln.strip().startswith(heading_prefix)), None)
    if i is None:
        return []
    rows, started = [], False
    for ln in lines[i + 1:]:
        s = ln.strip()
        if s.startswith("|"):
            started = True
            rows.append([c.strip() for c in s.strip("|").split("|")])
        elif started:
            break
    # drop the header row and the `|---|` separator row
    return [r for r in rows[1:] if not all(set(c) <= set("-: ") for c in r)]


def parse_ops(text: str) -> list[dict]:
    """The `## Operations` table as a list of column-keyed dicts."""
    return [dict(zip(_OP_COLUMNS, r)) for r in _first_table(text, "## Operations")]


def parse_aliases(text: str) -> dict[str, str]:
    """The `## Compatibility aliases` table as {mcp public tool name -> canonical op-id}."""
    return {r[0]: r[1] for r in _first_table(text, "## Compatibility aliases")}


class SpecOperations(unittest.TestCase):
    def setUp(self):
        self.text = SPEC.read_text(encoding="utf-8")

    def test_spec_file_exists(self):
        self.assertTrue(SPEC.is_file())

    def test_operation_table_parses_with_all_columns(self):
        ops = parse_ops(self.text)
        self.assertTrue(ops)
        for op in ops:
            for col in _OP_COLUMNS:
                self.assertTrue(op.get(col), f"{op.get('op-id')} missing {col}")

    def test_core_consumption_ops_present(self):
        ids = {op["op-id"] for op in parse_ops(self.text)}
        for expected in ("kbs.list", "lenses.list", "cards.list", "search", "diagnose",
                         "consult", "zoom", "verify", "evidence", "review"):
            self.assertIn(expected, ids)

    def test_review_is_experimental(self):
        review = next(op for op in parse_ops(self.text) if op["op-id"] == "review")
        self.assertEqual(review["capability"], "experimental")

    def test_every_public_mcp_tool_is_aliased_once(self):
        aliases = parse_aliases(self.text)
        self.assertEqual(set(aliases), _PUBLIC_MCP_TOOLS)
        op_ids = {op["op-id"] for op in parse_ops(self.text)}
        for tool, op_id in aliases.items():
            self.assertIn(op_id, op_ids, f"{tool} aliases unknown op {op_id}")

    def test_grounding_is_occurrence_not_entailment(self):
        low = self.text.lower()
        self.assertIn("occurrence", low)
        self.assertIn("not claim truth", low.replace("-", " "))


if __name__ == "__main__":
    unittest.main()
