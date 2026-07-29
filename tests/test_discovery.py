"""WI-4 / WI-5 — KB discovery is a passive catalog (list + describe), never a recommender.

WI-4 covers the `lode kbs` CLI; WI-5 covers the `list_kbs` MCP tool. Both render the same registry
catalog (id + description) and must contain no ranking/recommendation language."""
import contextlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib import cli                            # noqa: E402
from lode.lib import mcp_server as mcp              # noqa: E402
from lode.lib.pool import KBPool                     # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"
# words that would betray ranking/recommendation — a passive catalog must use none of them
_RANK_WORDS = ("recommend", "best", "use this", "should use", "top pick", "preferred", "ranked")


def _run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class KbsCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-disc-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manifest(self, body: str) -> Path:
        p = self.tmp / "r.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_lists_id_and_description(self):
        p = self._manifest(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n')
        code, out, _ = _run_cli(["kbs", "--registry", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("fixture", out)
        self.assertIn("synthetic", out.lower())        # from the fixture's description

    def test_output_is_passive_no_recommendation(self):
        p = self._manifest(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n')
        _, out, _ = _run_cli(["kbs", "--registry", str(p)])
        low = out.lower()
        for w in _RANK_WORDS:
            self.assertNotIn(w, low, f"catalog output must not rank/recommend (found {w!r})")

    def test_empty_registry_friendly_message_exit_0(self):
        p = self._manifest("# a manifest with no [[kb]] entries\n")
        code, out, _ = _run_cli(["kbs", "--registry", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("no KBs registered", out)

    def test_missing_registry_exits_2_via_configerror(self):
        code, _, err = _run_cli(["kbs", "--registry", str(self.tmp / "nope.toml")])
        self.assertEqual(code, 2)                       # RegistryError -> ConfigError handler
        self.assertIn("config error", err.lower())

    def test_broken_entry_is_listed_not_fatal(self):
        p = self._manifest(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n'
                            '[[kb]]\nid = "broken"\npath = "/no/such/library.toml"\n')
        code, out, _ = _run_cli(["kbs", "--registry", str(p)])
        self.assertEqual(code, 0)
        self.assertIn("fixture", out)
        self.assertIn("broken", out)
        self.assertIn("unavailable", out.lower())


class ListKbsMcp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-disc-mcp-"))
        self.cfg = mcp._load_config(str(FIX))          # the served KB; list_kbs spans the registry

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _manifest(self, body: str) -> Path:
        p = self.tmp / "r.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def test_list_kbs_registered_in_tools_and_dispatch(self):
        names = {t["name"] for t in mcp.TOOLS}
        self.assertIn("list_kbs", names)
        self.assertEqual(names, set(mcp.DISPATCH))

    def test_list_kbs_returns_catalog(self):
        p = self._manifest(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n')
        out = mcp._tool_list_kbs(self.cfg, {"registry": str(p)})
        self.assertIn("fixture", out)
        self.assertIn("synthetic", out.lower())

    def test_list_kbs_is_passive(self):
        p = self._manifest(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n')
        low = mcp._tool_list_kbs(self.cfg, {"registry": str(p)}).lower()
        for w in _RANK_WORDS:
            self.assertNotIn(w, low)

    def test_list_kbs_empty_registry_graceful(self):
        p = self._manifest("# no entries\n")
        out = mcp._tool_list_kbs(self.cfg, {"registry": str(p)})
        self.assertIn("No KBs", out)

    def test_list_kbs_tool_description_is_passive(self):
        t = next(t for t in mcp.TOOLS if t["name"] == "list_kbs")
        low = t["description"].lower()
        for w in _RANK_WORDS:
            self.assertNotIn(w, low)

    def test_malformed_registry_is_in_protocol_error_not_a_crash(self):
        # a malformed explicit manifest: handle() must return an in-protocol isError result and
        # keep stdout to a single well-formed JSON line — never raise and corrupt the stream.
        bad = self._manifest("[[kb]]\nid = \n")     # invalid TOML
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(KBPool.single(self.cfg), {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                                  "params": {"name": "list_kbs",
                                             "arguments": {"registry": str(bad)}}})
        reply = json.loads(buf.getvalue())          # single parseable line == stream intact
        self.assertEqual(reply["id"], 7)
        self.assertTrue(reply["result"]["isError"])


if __name__ == "__main__":
    unittest.main()
