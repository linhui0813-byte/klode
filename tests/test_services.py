"""WI-4 — catalog + content services via the shared executor: they wrap query/console/pool into core
OpResults with provenance, honor scope (registry / kb / fan-out), and don't re-implement engine logic."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import core, registry, services         # noqa: E402
from klode.lib.config import Config, ConfigError        # noqa: E402
from klode.lib.pool import KBPool                        # noqa: E402

FIX1 = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"    # id kb-fixture, dim pacing
FIX2 = REPO / "tests" / "fixtures" / "kb-fixture-2" / "library.toml"  # id kb-fixture-2, dim cadence


class Services(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-svc-"))
        self.single = KBPool.single(Config.load(FIX1))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _multi(self) -> KBPool:
        body = f'[[kb]]\nid = "pace"\npath = "{FIX1}"\n[[kb]]\nid = "cadence"\npath = "{FIX2}"\n'
        m = self.tmp / "reg.toml"
        m.write_text(body, encoding="utf-8")
        return KBPool(registry.load(m))

    # --- catalog ---
    def test_kbs_list_is_registry_scoped(self):
        r = services.execute(self._multi(), "kbs.list")
        self.assertIsNone(r.provenance.kb)                       # registry scope, no single KB
        self.assertEqual({i.id for i in r.value}, {"pace", "cadence"})

    def test_search_returns_hits_with_provenance(self):
        r = services.execute(self.single, "search", params={"terms": ["reader"]})
        self.assertEqual(r.provenance.kb, "kb-fixture")
        self.assertEqual(r.op_id, "search")
        self.assertIn("hits", r.value)

    def test_cards_list(self):
        r = services.execute(self.single, "cards.list")
        self.assertEqual({c["id"] for c in r.value}, {"brevity", "structure"})

    def test_diagnose_routes(self):
        r = services.execute(self.single, "diagnose", params={"symptom": "the scene drags"})
        self.assertIn("pacing", [d for d, _ in r.value])

    # --- content ---
    def test_consult_dimension_yields_dimension_result(self):
        r = services.execute(self.single, "consult", params={"name": "pacing"})
        self.assertIsInstance(r.value, core.DimensionResult)
        self.assertEqual(r.value.name, "pacing")

    def test_consult_source_yields_source_card_result(self):
        r = services.execute(self.single, "consult", params={"name": "vega"})   # alias of the brevity card
        self.assertIsInstance(r.value, core.SourceCardResult)

    def test_consult_none_yields_note(self):
        r = services.execute(self.single, "consult", params={"name": "zzznope"})
        self.assertIsInstance(r.value, core.Note)
        self.assertEqual(r.value.outcome, "none")

    def test_zoom_levels(self):
        thin = services.execute(self.single, "zoom", params={"id": "brevity", "level": "thin"})
        self.assertIsInstance(thin.value, core.CardContent)
        self.assertTrue(thin.value.body)
        content = services.execute(self.single, "zoom", params={"id": "brevity", "level": "content"})
        self.assertTrue(content.value.source.installed)          # fixture source .txt is present

    # --- scope policy ---
    def test_zoom_missing_card_is_none(self):
        r = services.execute(self.single, "zoom", params={"id": "no-such-card", "level": "thin"})
        self.assertIsNone(r.value)                              # adapters render "No card with id …"

    def test_fanout_discovery_across_kbs(self):
        r = services.execute(self._multi(), "diagnose", params={"symptom": "monotone"})
        self.assertIsInstance(r.value, core.FanOut)
        kbs = {item.provenance.kb for item in r.value.items}
        self.assertEqual(kbs, {"pace", "cadence"})

    def test_grounding_op_requires_kb_when_multiple(self):
        with self.assertRaises(ConfigError):                     # ScopeError is a ConfigError
            services.execute(self._multi(), "consult", params={"name": "pacing"})   # consult not fanout

    def test_explicit_kb_selects_it(self):
        r = services.execute(self._multi(), "consult", kb_arg="cadence", params={"name": "cadence"})
        self.assertEqual(r.provenance.kb, "cadence")
        self.assertIsInstance(r.value, core.DimensionResult)

    def test_empty_pool_kb_op_errors(self):
        with self.assertRaises(ConfigError):
            services.execute(KBPool(()), "search", params={"terms": ["x"]})

    def test_bounded_int_clamps_untrusted_params(self):
        b = services._bounded_int
        self.assertEqual(b({"limit": -5}, "limit", 20, 1, 1000), 1)       # negative -> lower bound
        self.assertEqual(b({"limit": 999999}, "limit", 20, 1, 1000), 1000)  # huge -> upper bound
        self.assertEqual(b({"limit": "nope"}, "limit", 20, 1, 1000), 20)  # bad type -> default
        self.assertEqual(b({}, "limit", 20, 1, 1000), 20)                 # missing -> default
        self.assertEqual(b({"limit": 7}, "limit", 20, 1, 1000), 7)        # in range -> as given

    def test_search_limit_is_bounded(self):
        r = services.execute(self.single, "search", params={"terms": ["reader"], "limit": -1})
        self.assertLessEqual(len(r.value["hits"]), 1)                     # negative limit clamped to 1

    def test_fanout_lets_programming_bugs_surface(self):
        # a real defect inside a service must NOT be swallowed as a per-KB "unavailable"
        pool = self._multi()
        orig = services.SERVICES["diagnose"]
        services.SERVICES["diagnose"] = lambda cfg, params: (_ for _ in ()).throw(TypeError("bug"))
        try:
            with self.assertRaises(TypeError):
                services.execute(pool, "diagnose", params={"symptom": "x"})
        finally:
            services.SERVICES["diagnose"] = orig


if __name__ == "__main__":
    unittest.main()
