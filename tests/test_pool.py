"""WI-1 — KBPool: registry-backed, lazily-cached id->Config addressing, with a default only when a
single KB is registered, and fail-loud unknown/broken-KB resolution."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib import registry                          # noqa: E402
from klode.lib.config import Config, ConfigError        # noqa: E402
from klode.lib.pool import KBPool                        # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


class Pool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-pool-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _kb(self, kid: str, *, broken: bool = False) -> Path:
        """A minimal valid (or deliberately broken) KB library.toml, return its path."""
        d = self.tmp / kid
        d.mkdir(parents=True, exist_ok=True)
        body = (f'[library]\nid = "{kid}"\nshelves = []\n' if broken
                else f'[library]\nid = "{kid}"\nshelves = ["books"]\n[bibliography]\nenabled = false\n')
        (d / "library.toml").write_text(body, encoding="utf-8")
        return d / "library.toml"

    def _manifest(self, *entries: tuple[str, Path]) -> Path:
        body = "".join(f'[[kb]]\nid = "{kid}"\npath = "{path}"\n' for kid, path in entries)
        p = self.tmp / "registry.toml"
        p.write_text(body, encoding="utf-8")
        return p

    def _pool(self, *entries: tuple[str, Path]) -> KBPool:
        return KBPool(registry.load(self._manifest(*entries)))

    def test_ids_are_sorted_and_complete(self):
        pool = self._pool(("beta", self._kb("beta")), ("alpha", self._kb("alpha")))
        self.assertEqual(pool.ids(), ("alpha", "beta"))

    def test_config_returns_the_addressed_kb(self):
        pool = self._pool(("alpha", self._kb("alpha")), ("beta", self._kb("beta")))
        self.assertEqual(pool.config("alpha").id, "alpha")
        self.assertEqual(pool.config("beta").id, "beta")

    def test_config_is_cached_same_object(self):
        pool = self._pool(("alpha", self._kb("alpha")))
        self.assertIs(pool.config("alpha"), pool.config("alpha"))

    def test_unknown_id_raises_listing_valid_ids(self):
        pool = self._pool(("alpha", self._kb("alpha")), ("beta", self._kb("beta")))
        with self.assertRaises(registry.RegistryError) as ctx:
            pool.config("zeta")
        msg = str(ctx.exception)
        self.assertIn("zeta", msg)
        self.assertIn("alpha", msg)
        self.assertIn("beta", msg)

    def test_broken_entry_constructs_and_isolates(self):
        pool = self._pool(("alpha", self._kb("alpha")),
                          ("broke", self._kb("broke", broken=True)))
        self.assertEqual(pool.config("alpha").id, "alpha")     # good KB still works
        with self.assertRaises(ConfigError) as ctx:            # RegistryError is a ConfigError
            pool.config("broke")
        self.assertIn("broke", str(ctx.exception))

    def test_single_entry_has_default_multi_has_none(self):
        self.assertEqual(self._pool(("alpha", self._kb("alpha"))).default, "alpha")
        self.assertIsNone(self._pool(("alpha", self._kb("alpha")),
                                     ("beta", self._kb("beta"))).default)

    def test_empty_pool(self):
        pool = KBPool(())
        self.assertEqual(pool.ids(), ())
        self.assertIsNone(pool.default)

    def test_single_wraps_a_loaded_config(self):
        cfg = Config.load(FIX)
        pool = KBPool.single(cfg)
        self.assertEqual(pool.ids(), (cfg.id,))
        self.assertEqual(pool.default, cfg.id)
        self.assertIs(pool.config(cfg.id), cfg)                # pre-cached, not reloaded


if __name__ == "__main__":
    unittest.main()
