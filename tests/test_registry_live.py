"""A long-lived pool must serve what the manifest says NOW, not what it said at startup.

The MCP server builds its pool once and then runs for the life of a session. Before this, moving
a KB and re-pointing the registry left that server serving a path that no longer existed until
someone restarted it — while the manifest's own contract says "the catalog matches what is
served". These pin the reload, and the deliberate limits on it.
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from klode.lib.pool import KBPool                                        # noqa: E402
from klode.lib.registry import RegistryError                             # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"
FIX2 = REPO / "tests" / "fixtures" / "kb-fixture-2" / "library.toml"


class LiveRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-reg-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.manifest = self.tmp / "registry.toml"

    def _write(self, body: str):
        self.manifest.write_text(body, encoding="utf-8")
        # stat granularity: make every rewrite observably different without sleeping
        import os
        st = self.manifest.stat()
        os.utime(self.manifest, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    def test_a_repointed_path_is_picked_up_without_a_restart(self):
        # exactly the incident this fixes: the KB moved, the registry was updated, and a running
        # server kept resolving the old path
        moved = self.tmp / "moved"
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n')
        pool = KBPool.from_registry(self.manifest)
        self.assertEqual(pool.config("one").config_path, FIX.resolve())

        shutil.copytree(FIX.parent, moved)
        self._write(f'[[kb]]\nid = "one"\npath = "{moved / "library.toml"}"\n')
        self.assertEqual(pool.config("one").config_path, (moved / "library.toml").resolve())

    def test_a_newly_registered_kb_appears_in_the_catalog(self):
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n')
        pool = KBPool.from_registry(self.manifest)
        self.assertEqual(pool.ids(), ("one",))
        self.assertEqual(pool.default, "one")                 # sole KB

        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n[[kb]]\nid = "two"\npath = "{FIX2}"\n')
        self.assertEqual(pool.ids(), ("one", "two"))
        self.assertIsNone(pool.default)                       # no longer a sole KB
        self.assertEqual({i.id for i in pool.catalog()}, {"one", "two"})

    def test_a_removed_kb_stops_being_addressable(self):
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n[[kb]]\nid = "two"\npath = "{FIX2}"\n')
        pool = KBPool.from_registry(self.manifest)
        self.assertEqual(pool.ids(), ("one", "two"))
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n')
        with self.assertRaises(RegistryError) as e:
            pool.config("two")
        self.assertIn("unknown KB 'two'", str(e.exception))

    def test_a_broken_manifest_keeps_the_previous_catalog(self):
        # blanking a working catalog because someone is mid-edit is worse than briefly serving
        # the last good one
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n')
        pool = KBPool.from_registry(self.manifest)
        self.assertEqual(pool.ids(), ("one",))
        self._write("[[kb]  <- not TOML")
        self.assertEqual(pool.ids(), ("one",))                # survived
        self.assertEqual(pool.config("one").config_path, FIX.resolve())
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n[[kb]]\nid = "two"\npath = "{FIX2}"\n')
        self.assertEqual(pool.ids(), ("one", "two"))          # and recovers on the next good write

    def test_an_untouched_manifest_is_re_read_at_most_once(self):
        # the reload is stat-gated: an unchanged manifest must cost one stat, not a re-parse per
        # call, and the memoized Config must survive
        from klode.lib import registry as reg
        self._write(f'[[kb]]\nid = "one"\npath = "{FIX}"\n')
        pool = KBPool.from_registry(self.manifest)
        cfg = pool.config("one")
        calls = []
        real = reg.load
        reg.load = lambda *a, **k: (calls.append(1), real(*a, **k))[1]
        try:
            for _ in range(5):
                pool.ids(); pool.catalog(); pool.config("one")
        finally:
            reg.load = real
        self.assertEqual(calls, [])                           # nothing changed -> nothing re-parsed
        self.assertIs(pool.config("one"), cfg)                # and the Config is still memoized

    def test_a_single_config_pool_has_no_manifest_to_watch(self):
        from klode import lib
        pool = KBPool.single(lib.Config.load(FIX))
        self.assertEqual(pool.ids(), (lib.Config.load(FIX).id,))
        self.assertIsNone(pool._manifest)                     # nothing to stat, nothing to reload


if __name__ == "__main__":
    unittest.main()
