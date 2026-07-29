"""WI-1 — self-describing KB identity on Config: id/name/description/tags parsing, defaults,
and fail-loud validation. Stdlib-only, backward-compatible (a config with no identity keys loads)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from lode.lib.config import Config, ConfigError, slugify   # noqa: E402

_BASE = 'dir = "library"\ncards = "cards"\nshelves = ["books"]\n'
_TAIL = "[bibliography]\nenabled = false\n"


def _write(root: Path, library_extra: str = "") -> Path:
    (root / "library").mkdir(parents=True, exist_ok=True)
    (root / "library.toml").write_text(
        "[library]\n" + _BASE + library_extra + _TAIL, encoding="utf-8")
    return root / "library.toml"


class Identity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="lodlib-id-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_full_identity_parsed(self):
        cfg = Config.load(_write(
            self.tmp,
            'id = "kb-demo"\nname = "Demo KB"\ndescription = "a demo library"\n'
            'tags = ["fiction", "craft"]\n'))
        self.assertEqual(cfg.id, "kb-demo")
        self.assertEqual(cfg.name, "Demo KB")
        self.assertEqual(cfg.description, "a demo library")
        self.assertEqual(cfg.tags, ("fiction", "craft"))

    def test_id_defaults_to_slug_of_dir_name(self):
        d = self.tmp / "KB 42 Storycraft"
        d.mkdir()
        cfg = Config.load(_write(d))
        self.assertEqual(cfg.id, "kb-42-storycraft")   # derived + slugified, itself a valid slug

    def test_absent_optional_fields_default_empty(self):
        cfg = Config.load(_write(self.tmp))
        self.assertEqual(cfg.name, "")
        self.assertEqual(cfg.description, "")
        self.assertEqual(cfg.tags, ())

    def test_malformed_id_is_rejected(self):
        for bad in ("Bad Id", "-leading", "trailing-", "dou--ble", "UPPER", ""):
            with self.assertRaises(ConfigError):
                Config.load(_write(self.tmp, f'id = "{bad}"\n'))

    def test_id_must_be_a_string(self):
        with self.assertRaises(ConfigError):
            Config.load(_write(self.tmp, "id = 42\n"))

    def test_tags_must_be_a_list_of_strings(self):
        with self.assertRaises(ConfigError):
            Config.load(_write(self.tmp, 'tags = "fiction"\n'))     # a bare string, not a list
        with self.assertRaises(ConfigError):
            Config.load(_write(self.tmp, "tags = [1, 2]\n"))        # non-string elements

    def test_name_and_description_must_be_strings(self):
        with self.assertRaises(ConfigError):
            Config.load(_write(self.tmp, "name = 5\n"))
        with self.assertRaises(ConfigError):
            Config.load(_write(self.tmp, "description = true\n"))

    def test_real_kb01_config_loads_with_its_identity(self):
        real = REPO / "corpus" / "kb-01-storycraft" / "library.toml"
        if not real.exists():
            self.skipTest("kb-01 config not present")
        cfg = Config.load(real)
        self.assertEqual(cfg.id, "storycraft")         # explicit [library].id
        self.assertTrue(cfg.description)               # self-describing for the catalog

    def test_slugify_helper(self):
        self.assertEqual(slugify("KB 01 Storycraft!"), "kb-01-storycraft")
        self.assertEqual(slugify("__weird__"), "weird")

    def test_non_table_section_is_rejected(self):
        # a malformed `[library]` as a bare string must fail loud (ConfigError), not AttributeError
        (self.tmp / "library.toml").write_text('library = "oops"\n', encoding="utf-8")
        with self.assertRaises(ConfigError):
            Config.load(self.tmp / "library.toml")

    def test_non_utf8_config_is_rejected(self):
        (self.tmp / "library.toml").write_bytes(b'[library]\ndir = "\xff"\n')
        with self.assertRaises(ConfigError):
            Config.load(self.tmp / "library.toml")

    def test_undrivable_id_fails_loud(self):
        d = self.tmp / "小说"                 # no ASCII-alnum content and no explicit id
        d.mkdir()
        with self.assertRaises(ConfigError):
            Config.load(_write(d))

    def test_one_line_fields_are_sanitized(self):
        # newlines collapse, control bytes (ESC) are stripped — a catalog line can't be spoofed
        cfg = Config.load(_write(self.tmp, 'name = "Line1\\nLine2"\ndescription = "a\\tb\\u001bX"\n'))
        self.assertEqual(cfg.name, "Line1 Line2")
        self.assertEqual(cfg.description, "a b X")
        self.assertNotIn("\x1b", cfg.description)


if __name__ == "__main__":
    unittest.main()
