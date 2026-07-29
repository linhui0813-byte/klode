"""WI-2/3/4 — the multi-KB multiplexer: a registry-bound pool, grounding tools that always name
their KB, and discovery tools that fan out across KBs. Driven through the real `handle()` JSON-RPC
path against two genuinely different committed fixtures (pacing vs cadence)."""
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

from lode.lib import mcp_server as mcp                # noqa: E402
from lode.lib import registry                         # noqa: E402
from lode.lib.config import Config                    # noqa: E402
from lode.lib.pool import KBPool                       # noqa: E402

FIX1 = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"    # id kb-fixture, dim "pacing"
FIX2 = REPO / "tests" / "fixtures" / "kb-fixture-2" / "library.toml"  # id kb-fixture-2, dim "cadence"


def _call(pool, name, arguments, mid=1):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mcp.handle(pool, {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})
    reply = json.loads(buf.getvalue())          # single parseable line == stream intact
    return reply["result"]["content"][0]["text"], reply["result"]["isError"]


class Multiplex(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lode-mux-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _registry(self, *pairs) -> Path:
        body = "".join(f'[[kb]]\nid = "{kid}"\npath = "{path}"\n' for kid, path in pairs)
        p = self.tmp / "reg.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def _pool(self, *pairs) -> KBPool:
        return KBPool(registry.load(self._registry(*pairs)))

    def _broken(self) -> Path:
        d = self.tmp / "broke"
        d.mkdir(parents=True, exist_ok=True)
        (d / "library.toml").write_text('[library]\nid = "broke"\nshelves = []\n', encoding="utf-8")
        return d / "library.toml"

    # ---- WI-2: registry binding, single-KB equivalence, startup errors ----
    def test_registry_pool_ids(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        self.assertEqual(pool.ids(), ("cadence", "pace"))

    def test_single_kb_grounding_is_tag_plus_direct(self):
        cfg = Config.load(FIX1)
        text, err = _call(KBPool.single(cfg), "consult_dimension", {"dimension": "pacing"})
        self.assertFalse(err)
        self.assertEqual(text, f"[{cfg.id}]\n" + mcp._tool_consult_dimension(cfg, {"dimension": "pacing"}))

    def test_single_kb_discovery_is_byte_identical(self):
        cfg = Config.load(FIX1)
        text, err = _call(KBPool.single(cfg), "list_lenses", {})
        self.assertFalse(err)
        self.assertEqual(text, mcp._tool_list_lenses(cfg, {}))          # untagged

    def test_main_config_and_registry_mutually_exclusive(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mcp.main(["-c", str(FIX1), "-r", str(self._registry(("pace", FIX1)))])
        self.assertEqual(code, 2)
        self.assertIn("not both", err.getvalue())
        self.assertEqual(out.getvalue(), "")                          # protocol stdout untouched

    def test_main_malformed_registry_exits_2_writing_nothing_to_stdout(self):
        bad = self.tmp / "bad.toml"
        bad.write_text("[[kb]]\nid = \n", encoding="utf-8")           # invalid TOML
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mcp.main(["-r", str(bad)])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(), "")                          # never enters the stdin loop

    # ---- WI-3: grounding tools always name their KB; no auto-routing ----
    def test_grounding_explicit_kb_isolation_and_provenance(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "consult_dimension", {"kb": "pace", "dimension": "pacing"})
        self.assertFalse(err)
        self.assertTrue(text.startswith("[pace]\n"))                   # provenance tag
        self.assertIn("Cut what the reader can infer", text)           # pacing content
        self.assertNotIn("Break on the breath", text)                  # none of cadence's content

    def test_grounding_verify_quote_is_tagged(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "verify_quote",
                          {"kb": "cadence", "id": "rhythm",
                           "phrase": "Break a long clause where the breath would break"})
        self.assertFalse(err)
        self.assertTrue(text.startswith("[cadence]\n"))
        self.assertIn("VERIFIED", text)

    def test_grounding_no_kb_in_multiplex_is_error_listing_ids(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "consult_dimension", {"dimension": "pacing"})   # no kb
        self.assertTrue(err)
        self.assertIn("pace", text)
        self.assertIn("cadence", text)
        self.assertNotIn("Cut what the reader can infer", text)        # no grounded body leaked

    def test_grounding_unknown_kb_is_error(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "consult_dimension", {"kb": "nope", "dimension": "pacing"})
        self.assertTrue(err)
        self.assertIn("nope", text)
        self.assertIn("pace", text)

    def test_single_kb_grounding_no_kb_is_still_tagged(self):
        cfg = Config.load(FIX1)
        text, err = _call(KBPool.single(cfg), "consult_dimension", {"dimension": "pacing"})
        self.assertFalse(err)
        self.assertTrue(text.startswith(f"[{cfg.id}]\n"))              # intentionally NOT byte-identical

    def test_grounding_no_kb_never_guesses_across_pools(self):
        a = self._pool(("pace", FIX1), ("cadence", FIX2))
        b = self._pool(("x", FIX1), ("y", FIX2))
        _, ea = _call(a, "consult_dimension", {"dimension": "pacing"})
        _, eb = _call(b, "consult_dimension", {"dimension": "pacing"})
        self.assertTrue(ea and eb)                                     # both refuse to pick

    # ---- WI-4: discovery fan-out, tagging, caps, error isolation ----
    def test_discovery_fanout_tags_all(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "list_lenses", {})                     # no kb -> fan out
        self.assertFalse(err)
        self.assertIn("[pace]", text)
        self.assertIn("[cadence]", text)
        self.assertIn("pacing", text)
        self.assertIn("cadence", text)

    def test_discovery_star_equals_omitted(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        star, _ = _call(pool, "list_lenses", {"kb": "*"})
        self.assertIn("[pace]", star)
        self.assertIn("[cadence]", star)

    def test_discovery_explicit_kb_only_that(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        text, err = _call(pool, "list_lenses", {"kb": "pace"})
        self.assertFalse(err)
        self.assertIn("[pace]", text)
        self.assertNotIn("[cadence]", text)
        self.assertIn("pacing", text)

    def test_discovery_fanout_caps_and_announces_truncation(self):
        pairs = [(f"kb{n:02d}", FIX1) for n in range(mcp._FANOUT_CAP + 2)]
        pool = self._pool(*pairs)
        text, err = _call(pool, "list_lenses", {})
        self.assertFalse(err)
        self.assertEqual(text.count("[kb"), mcp._FANOUT_CAP)           # capped
        self.assertIn("more KB(s) not shown", text)                    # truncation announced

    def test_discovery_fanout_isolates_a_broken_kb(self):
        pool = self._pool(("pace", FIX1), ("broke", self._broken()))
        text, err = _call(pool, "list_lenses", {})
        self.assertFalse(err)                                          # fan-out never errors as a whole
        self.assertIn("[pace]", text)
        self.assertIn("[broke]", text)
        self.assertIn("unavailable", text)

    def test_single_kb_discovery_no_kb_is_byte_identical(self):
        cfg = Config.load(FIX1)
        text, err = _call(KBPool.single(cfg), "diagnose", {"symptom": "the scene drags"})
        self.assertFalse(err)
        self.assertEqual(text, mcp._tool_diagnose(cfg, {"symptom": "the scene drags"}))

    # ---- WI-6: cross-cutting invariants ----
    def test_single_kb_equivalence_for_every_tool(self):
        cfg = Config.load(FIX1)
        pool = KBPool.single(cfg)
        cases = {
            "list_lenses": {}, "search_sources": {"query": "reader"},
            "diagnose": {"symptom": "the scene drags"},                 # discovery -> untagged
            "consult_dimension": {"dimension": "pacing"}, "consult_framework": {"name": "vega"},
            "zoom_card": {"id": "brevity"},
            "verify_quote": {"id": "brevity", "phrase": "Trim every clause the reader can infer"},
        }
        for name, args in cases.items():
            direct = mcp.DISPATCH[name](cfg, args)
            text, err = _call(pool, name, args)
            self.assertFalse(err, name)
            expected = f"[{cfg.id}]\n{direct}" if name in mcp._GROUNDING else direct
            self.assertEqual(text, expected, name)

    def test_error_reply_is_exactly_one_json_line(self):
        pool = self._pool(("pace", FIX1), ("cadence", FIX2))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            mcp.handle(pool, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                              "params": {"name": "consult_dimension", "arguments": {"dimension": "pacing"}}})
        out = buf.getvalue()
        self.assertEqual(out.count("\n"), 1)                            # exactly one line — stream intact
        self.assertTrue(json.loads(out)["result"]["isError"])

    def test_empty_pool_grounding_is_error_not_crash(self):
        # WI-6 error path: a grounding call against an empty pool has no KB to ground against
        text, err = _call(KBPool(()), "consult_dimension", {"dimension": "pacing"})
        self.assertTrue(err)

    def test_grounding_broken_kb_via_handle_is_error(self):
        # WI-6 error path: addressing a registered-but-broken KB through handle() must not crash
        pool = self._pool(("pace", FIX1), ("broke", self._broken()))
        text, err = _call(pool, "consult_dimension", {"kb": "broke", "dimension": "pacing"})
        self.assertTrue(err)
        self.assertIn("broke", text)


if __name__ == "__main__":
    unittest.main()
