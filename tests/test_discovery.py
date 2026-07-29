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
from lode.lib import registry                        # noqa: E402
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
    """list_kbs reflects the POOL the server serves — every listed id is addressable via `kb`, so
    it can never diverge from what is actually served. It is router-handled (not in DISPATCH), stays
    passive, and isolates a broken KB. Driven through the real handle() path."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-disc-mcp-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pool(self, *pairs) -> KBPool:
        body = "".join(f'[[kb]]\nid = "{i}"\npath = "{p}"\n' for i, p in pairs)
        m = self.tmp / "r.toml"
        m.write_text(body, encoding="utf-8")
        return KBPool(registry.load(m))

    def _broken(self) -> Path:
        d = self.tmp / "broke"
        d.mkdir(parents=True, exist_ok=True)
        (d / "library.toml").write_text('[library]\nid = "broke"\nshelves = []\n', encoding="utf-8")
        return d / "library.toml"

    def _list_kbs(self, pool):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(pool, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "list_kbs", "arguments": {}}})
        r = json.loads(buf.getvalue())              # single parseable line == stream intact
        return r["result"]["content"][0]["text"], r["result"]["isError"]

    def test_list_kbs_in_tools_but_router_handled(self):
        self.assertIn("list_kbs", {t["name"] for t in mcp.TOOLS})
        self.assertNotIn("list_kbs", mcp.DISPATCH)             # pool-scoped: the router handles it

    def test_list_kbs_reflects_the_served_pool(self):
        text, err = self._list_kbs(self._pool(("fixture", FIX)))
        self.assertFalse(err)
        self.assertIn("fixture", text)                        # the registry/pool id
        self.assertIn("synthetic", text.lower())              # the KB's own description

    def test_list_kbs_is_passive(self):
        text, _ = self._list_kbs(self._pool(("fixture", FIX)))
        low = text.lower()
        for w in _RANK_WORDS:
            self.assertNotIn(w, low)

    def test_list_kbs_empty_pool_graceful(self):
        text, err = self._list_kbs(KBPool(()))
        self.assertFalse(err)
        self.assertIn("No KBs", text)

    def test_list_kbs_broken_kb_shown_unavailable_not_crash(self):
        text, err = self._list_kbs(self._pool(("fixture", FIX), ("broke", self._broken())))
        self.assertFalse(err)                                 # one bad entry never blanks the catalog
        self.assertIn("fixture", text)
        self.assertIn("broke", text)
        self.assertIn("unavailable", text.lower())

    def test_list_kbs_tool_description_is_passive(self):
        t = next(t for t in mcp.TOOLS if t["name"] == "list_kbs")
        low = t["description"].lower()
        for w in _RANK_WORDS:
            self.assertNotIn(w, low)


if __name__ == "__main__":
    unittest.main()
