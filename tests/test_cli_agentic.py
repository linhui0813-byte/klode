"""WI-8 — the CLI as an agentic surface: --json (serializing the same OpResult the MCP renders),
--kb (registry addressing), and the new verify/review/lenses/cards verbs — all through execute()."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import cli, registry                # noqa: E402
from klode.lib import mcp_server as mcp            # noqa: E402
from klode.lib.pool import KBPool                   # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"
REAL = "Trim every clause the reader can infer"    # occurs in the fixture's brevity source


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class CliAgentic(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-cli-"))
        self.reg = self.tmp / "reg.toml"
        self.reg.write_text(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n', encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _kb(self, *rest):
        return ["--kb", "fixture", "--registry", str(self.reg), *rest]

    def test_search_json_carries_provenance(self):
        code, out, _ = _run(self._kb("--json", "search", "reader"))
        self.assertEqual(code, 0)
        d = json.loads(out)
        self.assertEqual(d["op_id"], "search")
        self.assertEqual(d["provenance"]["kb"], "fixture")
        self.assertIn("hits", d["value"])

    def test_verify_verb_prose_and_json(self):
        code, out, _ = _run(self._kb("verify", "brevity", REAL))
        self.assertEqual(code, 0)                         # FOUND -> exit 0
        self.assertIn("FOUND", out)
        _, jout, _ = _run(self._kb("--json", "verify", "brevity", REAL))
        self.assertEqual(json.loads(jout)["value"]["resolution"], "found")

    def test_verify_not_found_exits_1(self):
        code, out, _ = _run(self._kb("verify", "brevity", "no such zzqx phrase"))
        self.assertEqual(code, 1)
        self.assertIn("NOT-FOUND", out.upper())

    def test_cross_surface_provenance_parity(self):
        _, cout, _ = _run(self._kb("--json", "consult", "pacing"))
        cli_kb = json.loads(cout)["provenance"]["kb"]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(KBPool(registry.load(self.reg)),
                       {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "consult_dimension",
                                   "arguments": {"kb": "fixture", "dimension": "pacing"}}})
        mcp_text = json.loads(buf.getvalue())["result"]["content"][0]["text"]
        self.assertTrue(mcp_text.startswith(f"[{cli_kb}]"))    # one structured provenance, two skins

    def test_unknown_kb_exits_2(self):
        code, _, err = _run(["--kb", "nope", "--registry", str(self.reg), "--json", "search", "x"])
        self.assertEqual(code, 2)                         # ConfigError -> main() exit 2

    def test_review_is_never_authoritative(self):
        _, out, _ = _run(self._kb("review", "a draft", "pacing"))
        self.assertIn("NOT AUTHORITATIVE", out)
        _, jout, _ = _run(self._kb("--json", "review", "a draft", "pacing"))
        d = json.loads(jout)
        self.assertTrue(d["value"]["non_production"])
        self.assertEqual(d["capability"], "experimental")

    def test_json_verify_not_found_exits_1(self):
        code, out, _ = _run(self._kb("--json", "verify", "brevity", "no such zzqx phrase"))
        self.assertEqual(code, 1)                         # --json preserves the not-found exit code
        self.assertEqual(json.loads(out)["value"]["resolution"], "not-found")

    def test_global_registry_before_subcommand_is_not_clobbered(self):
        # `klode --registry X kbs` must use X, not the subparser default
        code, out, _ = _run(["--registry", str(self.reg), "--json", "kbs"])
        self.assertEqual(code, 0)
        self.assertIn("fixture", out)

    def test_prose_honors_kb_like_json_does(self):
        # the KB must not depend on output format: prose `--kb X` resolves via the registry too.
        cwd = os.getcwd()
        os.chdir(self.tmp)                                 # no library.toml here: only --kb can resolve one
        try:
            code, out, _ = _run(self._kb("search", "reader"))     # prose, no --json
        finally:
            os.chdir(cwd)
        self.assertEqual(code, 0)                          # resolved the fixture KB (bug: ignored --kb -> exit 2)
        self.assertNotIn("{", out)                         # prose, not JSON

    def test_config_and_kb_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit) as cm:          # argparse rejects the pair at parse time
            _run(["-c", str(FIX), "--kb", "fixture", "--registry", str(self.reg), "search", "x"])
        self.assertEqual(cm.exception.code, 2)

    def test_star_kb_prose_is_rejected_not_crashed(self):
        # fan-out (`*`) has no single-KB prose rendering; it must error cleanly, never TypeError
        for verb in (["search", "reader"], ["lenses"], ["cards"]):
            code, _, err = _run(["--kb", "*", "--registry", str(self.reg), *verb])
            self.assertEqual(code, 2, verb)
            self.assertIn("needs --json", err, verb)

    def test_init_rejects_unsafe_shelf_names(self):
        for bad in ("../escape", "a/b", 'a"b', ".."):      # traversal / separators / TOML-breaking quote
            code, _, err = _run(["init", str(self.tmp / "proj"), "--shelf", bad])
            self.assertEqual(code, 2, bad)
            self.assertIn("invalid shelf", err, bad)
        self.assertFalse((self.tmp / "proj").exists())     # rejected before any filesystem mutation

    def test_cards_and_lenses_verbs(self):
        _, cout, _ = _run(self._kb("cards"))
        self.assertIn("brevity", cout)
        _, lout, _ = _run(self._kb("lenses"))
        self.assertIn("pacing", lout)

    def test_existing_prose_path_unchanged(self):
        code, out, _ = _run(["-c", str(FIX), "search", "reader"])   # no --json, single-KB via -c
        self.assertEqual(code, 0)
        self.assertNotIn("{", out)                        # prose, not json


if __name__ == "__main__":
    unittest.main()
