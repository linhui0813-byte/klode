"""WI-3 — the KB registry: manifest parsing, lookup precedence, fail-loud validation, and the
lazy catalog view. RegistryError subclasses ConfigError so the CLI's handler catches it."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib import registry                       # noqa: E402
from lode.lib.config import ConfigError             # noqa: E402

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"


class RegistryLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-reg-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, body: str, name: str = "r.toml") -> Path:
        p = self.tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def _dot_lode(self, where: Path, body: str) -> Path:
        d = where / ".lode"
        d.mkdir(parents=True, exist_ok=True)
        (d / "registry.toml").write_text(body, encoding="utf-8")
        return d / "registry.toml"

    def test_wellformed_manifest_loads_entries_sorted(self):
        p = self._write(f'[[kb]]\nid = "beta"\npath = "{FIX}"\n\n[[kb]]\nid = "alpha"\npath = "{FIX}"\n')
        kbs = registry.load(p)
        self.assertEqual([k.id for k in kbs], ["alpha", "beta"])   # sorted by id
        self.assertTrue(all(k.path.is_absolute() for k in kbs))

    def test_relative_path_resolves_against_manifest_dir(self):
        p = self._write('[[kb]]\nid = "x"\npath = "../kb/library.toml"\n', name="sub/r.toml")
        (kb,) = registry.load(p)
        self.assertEqual(kb.path, (self.tmp / "kb" / "library.toml").resolve())

    def test_tilde_path_expands_to_home(self):
        (kb,) = registry.load(self._write('[[kb]]\nid = "h"\npath = "~/kbs/x/library.toml"\n'))
        self.assertEqual(kb.path, (Path.home() / "kbs" / "x" / "library.toml").resolve())

    def test_absolute_path_used_as_is(self):
        (kb,) = registry.load(self._write('[[kb]]\nid = "a"\npath = "/opt/kb/library.toml"\n'))
        self.assertEqual(kb.path, Path("/opt/kb/library.toml").resolve())

    def test_precedence_explicit_over_project_over_user(self):
        proj, user = self.tmp / "proj", self.tmp / "home"
        self._dot_lode(proj, '[[kb]]\nid = "proj"\npath = "/p/library.toml"\n')
        self._dot_lode(user, '[[kb]]\nid = "user"\npath = "/u/library.toml"\n')
        explicit = self._write('[[kb]]\nid = "explicit"\npath = "/e/library.toml"\n', name="e.toml")
        self.assertEqual([k.id for k in registry.load(explicit, start=proj, home=user)], ["explicit"])
        self.assertEqual([k.id for k in registry.load(start=proj, home=user)], ["proj"])
        self.assertEqual([k.id for k in registry.load(start=self.tmp / "empty", home=user)], ["user"])

    def test_no_manifest_anywhere_is_empty_not_error(self):
        self.assertEqual(registry.load(start=self.tmp / "none", home=self.tmp / "none2"), ())

    def test_explicit_but_missing_manifest_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self.tmp / "nope.toml")

    def test_invalid_toml_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self._write("[[kb]]\nid = \n"))

    def test_missing_required_path_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self._write('[[kb]]\nid = "x"\n'))

    def test_missing_required_id_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self._write(f'[[kb]]\npath = "{FIX}"\n'))

    def test_non_slug_id_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self._write(f'[[kb]]\nid = "Bad Id"\npath = "{FIX}"\n'))

    def test_duplicate_id_is_error(self):
        with self.assertRaises(ConfigError):
            registry.load(self._write(f'[[kb]]\nid = "x"\npath = "{FIX}"\n[[kb]]\nid = "x"\npath = "{FIX}"\n'))

    def test_non_utf8_manifest_is_error(self):
        p = self.tmp / "r.toml"
        p.write_bytes(b'[[kb]]\nid = "x"\npath = "\xff"\n')
        with self.assertRaises(ConfigError):
            registry.load(p)


class RegistryDescribe(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-reg2-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _one(self, body: str) -> registry.KB:
        p = self.tmp / "r.toml"
        p.write_text(body, encoding="utf-8")
        (kb,) = registry.load(p)
        return kb

    def test_describe_valid_kb_carries_description(self):
        info = registry.describe(self._one(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n'))
        self.assertTrue(info.ok)
        self.assertEqual(info.id, "fixture")
        self.assertIn("synthetic", info.description.lower())

    def test_describe_broken_kb_is_not_ok_and_does_not_raise(self):
        info = registry.describe(self._one('[[kb]]\nid = "broken"\npath = "/no/such/library.toml"\n'))
        self.assertFalse(info.ok)
        self.assertIsNotNone(info.error)

    def test_resolve_broken_kb_fails_loud(self):
        kb = self._one('[[kb]]\nid = "broken"\npath = "/no/such/library.toml"\n')
        with self.assertRaises(ConfigError):
            registry.resolve(kb)

    def test_describe_malformed_target_config_is_not_ok_not_crash(self):
        # target library.toml exists but is malformed ([library] not a table) — describe must not raise
        bad = self.tmp / "badkb"
        bad.mkdir()
        (bad / "library.toml").write_text('library = "not a table"\n', encoding="utf-8")
        info = registry.describe(self._one(f'[[kb]]\nid = "bad"\npath = "{bad / "library.toml"}"\n'))
        self.assertFalse(info.ok)
        self.assertIsNotNone(info.error)

    def test_describe_non_utf8_target_is_not_ok_not_crash(self):
        bad = self.tmp / "badkb"
        bad.mkdir()
        (bad / "library.toml").write_bytes(b'[library]\ndir = "\xff"\n')
        info = registry.describe(self._one(f'[[kb]]\nid = "bad"\npath = "{bad / "library.toml"}"\n'))
        self.assertFalse(info.ok)

    def test_catalog_describes_every_entry(self):
        p = self.tmp / "r.toml"
        p.write_text(f'[[kb]]\nid = "fixture"\npath = "{FIX}"\n'
                     '[[kb]]\nid = "broken"\npath = "/no/such/library.toml"\n', encoding="utf-8")
        infos = registry.catalog(registry.load(p))
        self.assertEqual({i.id for i in infos}, {"fixture", "broken"})
        self.assertEqual({i.id: i.ok for i in infos}, {"fixture": True, "broken": False})


if __name__ == "__main__":
    unittest.main()
