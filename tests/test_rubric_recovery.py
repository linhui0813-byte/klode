"""An edited rubric must be recoverable by the command its own error names.

`check` on an edited-after-approval rubric says: re-approve with `klode.gate approve`. That command
called `_load_doc`, which ran the structural `parse` — including the approval-digest check — on the
UNMODIFIED document, and the digest was exactly what had gone stale. The demotion to `candidate`
that would have cleared it ran two lines later. So `approve` refused the one state it is named as
the remedy for, `repin` refused it through the same shared loader, and the only escape was
hand-deleting `approved_digest` from the JSON, which no message mentions.

Ordinary authoring produces this state: refine a level descriptor and the rubric is bricked.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from klode.gate import __main__ as gate_main                              # noqa: E402
from klode.gate import spec as _spec                                      # noqa: E402
from klode.lib.config import Config                                       # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "kb-fixture"


class _EditedRubric(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        shutil.copytree(FIXTURE, self.tmp / "kb")
        self.lib = self.tmp / "kb" / "library.toml"
        self.cfg = Config.load(self.lib)
        self.path = Path(self.cfg.criteria) / "pacing.json"

    def _edit_a_descriptor(self):
        """The most ordinary authoring change there is: reword one level descriptor."""
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        d = doc["criteria"][0]["levels"][0]["descriptor"]
        d["value"] = d["value"].rstrip(".") + " throughout."
        self.path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    def _args(self, **kw):
        ns = type("N", (), {"config": str(self.lib), "dimension": "pacing", "no_stamp": True})()
        for k, v in kw.items():
            setattr(ns, k, v)
        return ns


class AnEditIsDetected(_EditedRubric):
    def test_the_gate_refuses_an_edited_rubric(self):
        self._edit_a_descriptor()
        with self.assertRaises(_spec.SpecError) as cm:
            _spec.load(self.cfg, "pacing", corpus=False)
        self.assertIn("EDITED since it was approved", str(cm.exception))

    def test_the_message_names_the_command_and_the_dimension(self):
        """The remedy has to be runnable as printed — that is the whole defect in one line."""
        self._edit_a_descriptor()
        with self.assertRaises(_spec.SpecError) as cm:
            _spec.load(self.cfg, "pacing", corpus=False)
        msg = str(cm.exception)
        self.assertIn("klode.gate", msg)
        self.assertIn("approve", msg)
        self.assertIn("pacing", msg)          # not a bare `approve` with no target
        self.assertIn("-c", msg)              # and not one missing the required config flag


class TheNamedRemedyWorks(_EditedRubric):
    def test_approve_recovers_an_edited_rubric(self):
        self._edit_a_descriptor()
        self.assertEqual(gate_main.cmd_approve(self._args()), 0)
        spec = _spec.load(self.cfg, "pacing", corpus=False)
        self.assertTrue(spec.approved, "approve ran but the rubric is still not approved")

    def test_the_new_digest_covers_the_edited_body(self):
        self._edit_a_descriptor()
        gate_main.cmd_approve(self._args())
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(doc["approved_digest"], _spec.content_digest(doc),
                         "re-approval did not re-bind the digest to the new body")

    def test_a_second_edit_is_still_detected_after_recovery(self):
        """Recovery must not cost the guarantee: approve-then-edit stays detectable."""
        self._edit_a_descriptor()
        gate_main.cmd_approve(self._args())
        self._edit_a_descriptor()
        with self.assertRaises(_spec.SpecError):
            _spec.load(self.cfg, "pacing", corpus=False)

    def test_repin_also_recovers(self):
        """repin resets admission to candidate unconditionally, so the same guard blocked it from
        doing the very thing it was about to do — a moved corpus plus an edited body was terminal."""
        self._edit_a_descriptor()
        self.assertEqual(gate_main.cmd_repin(self._args()), 0)
        doc = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(doc["admission"], "candidate")
        self.assertNotIn("approved_digest", doc)


class ReadersStillRefuse(_EditedRubric):
    def test_the_door_is_only_open_to_the_writers(self):
        """`allow_stale_approval` must not leak into the read path — an edited rubric is still
        refused everywhere it would be SCORED against."""
        self._edit_a_descriptor()
        with self.assertRaises(_spec.SpecError):
            _spec.load(self.cfg, "pacing", corpus=False)
        with self.assertRaises(SystemExit):
            gate_main._load_doc(self.path)                     # default: allow_stale_approval=False

    def test_check_reports_invalid_without_repairing_anything(self):
        self._edit_a_descriptor()
        before = self.path.read_text(encoding="utf-8")
        self.assertEqual(gate_main.cmd_check(self._args()), 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before,
                         "check mutated the rubric it was only asked to inspect")


class ABadConfigIsAMessageNotATraceback(_EditedRubric):
    """`ConfigError` exists so a missing `library.toml` arrives as one readable line, and
    `klode.lib`'s CLI prints exactly that. This entry point let it escape as a 25-line stack, so
    the same mistake read completely differently depending on which command you ran."""

    def test_every_subcommand_reports_a_bad_config_cleanly(self):
        for verb, fn in (("check", gate_main.cmd_check),
                         ("approve", gate_main.cmd_approve),
                         ("repin", gate_main.cmd_repin)):
            with self.subTest(verb=verb):
                args = self._args()
                args.config = "/nonexistent/library.toml"
                with self.assertRaises(SystemExit) as cm:
                    fn(args)
                self.assertIn("config error", str(cm.exception))

    def test_the_message_survives_rather_than_being_replaced(self):
        args = self._args()
        args.config = "/nonexistent/library.toml"
        with self.assertRaises(SystemExit) as cm:
            gate_main.cmd_check(args)
        self.assertIn("/nonexistent/library.toml", str(cm.exception),
                      "the path the user typed was dropped from the error")


class TheWriterIsNoWeakerThanTheReader(_EditedRubric):
    """`spec.load` and `_load_doc` read the same hand-edited file. The one that WRITES had the
    thinner exception boundary — it caught `json.JSONDecodeError` but not the other `ValueError`
    subclasses, nor `RecursionError`, nor an oversized file."""

    def test_an_oversized_integer_is_diagnosed_not_leaked(self):
        self.path.write_text('{"n": ' + "9" * 5000 + "}", encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            gate_main._load_doc(self.path)
        self.assertIn("JSON", str(cm.exception))

    def test_deep_nesting_is_diagnosed_not_leaked(self):
        self.path.write_text("[" * 200_000 + "]" * 200_000, encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            gate_main._load_doc(self.path)
        self.assertTrue(str(cm.exception))     # a message, not a RecursionError traceback

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        self.path.write_text(" " * (_spec.MAX_SPEC_BYTES + 1), encoding="utf-8")
        with self.assertRaises(SystemExit) as cm:
            gate_main._load_doc(self.path)
        self.assertIn("rubric limit", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
