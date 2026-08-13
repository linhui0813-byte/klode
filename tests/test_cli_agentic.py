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

    def test_evidence_outputs_cited_raw_passage_and_json_status(self):
        code, out, _ = _run(self._kb("evidence", "brevity", "What quickens the pace?"))
        self.assertEqual(code, 0)
        self.assertIn("EVIDENCE_FOUND", out)
        self.assertIn("library/books/brevity.txt:", out)
        self.assertIn("quickens the pace", out)
        jcode, jout, _ = _run(self._kb(
            "--json", "evidence", "brevity", "quantum zucchini protocol"))
        self.assertEqual(jcode, 1)
        self.assertEqual(json.loads(jout)["value"]["status"], "insufficient-evidence")

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
        for verb in (["search", "reader"], ["lenses"], ["cards"],
                     ["evidence", "brevity", "reader"]):
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
        # the return codes were DISCARDED (`_, cout, _`), so this passed if either command printed
        # partial output and then reported failure
        rc_cards, cout, cerr = _run(self._kb("cards"))
        self.assertEqual(rc_cards, 0, cerr)
        self.assertIn("brevity", cout)
        rc_lenses, lout, lerr = _run(self._kb("lenses"))
        self.assertEqual(rc_lenses, 0, lerr)
        self.assertIn("pacing", lout)


    def test_existing_prose_path_unchanged(self):
        code, out, _ = _run(["-c", str(FIX), "search", "reader"])   # no --json, single-KB via -c
        self.assertEqual(code, 0)
        self.assertNotIn("{", out)                        # prose, not json

class InitManagesItsGitignoreBlock(unittest.TestCase):
    """A new shelf's copyrighted sources must become ignored on re-init.

    The marker was open-ended, so once ANY klode block existed the rules were never refreshed:
    `init --force` with a new shelf left that shelf's .txt/.pdf untracked-but-unignored, one
    `git add -A` away from committing the corpus this project exists to keep local. The [E]
    leak guard checks what is tracked; it cannot help with what was never ignored.
    """

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-init-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gi = self.tmp / ".gitignore"

    def _init(self, *shelves):
        from klode.lib.cli import build_parser, cmd_init
        argv = ["init", str(self.tmp), "--force"]
        for s in shelves:
            argv += ["--shelf", s]
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = cmd_init(build_parser().parse_args(argv))
        self.assertEqual(rc, 0, buf.getvalue())
        return self.gi.read_text(encoding="utf-8")

    def test_a_new_shelf_becomes_ignored_on_reinit(self):
        self._init("books")
        text = self._init("books", "papers")
        for pat in ("library/books/*.txt", "library/books/*.pdf",
                    "library/papers/*.txt", "library/papers/*.pdf"):
            self.assertIn(pat, text, f"{pat} would not be ignored")

    def test_the_users_own_rules_are_preserved_verbatim(self):
        self._init("books")
        self.gi.write_text(self.gi.read_text() + "\n# mine\n*.log\nbuild/\n", encoding="utf-8")
        text = self._init("books", "papers")
        self.assertIn("# mine", text)
        self.assertIn("*.log", text)
        self.assertIn("build/", text)

    def test_reinit_does_not_accumulate_duplicate_blocks(self):
        self._init("books")
        for _ in range(3):
            text = self._init("books", "papers")
        self.assertEqual(text.count("klode managed:"), 1)
        self.assertEqual(text.count("library/papers/*.txt"), 1)

    def test_a_removed_shelf_stops_being_ignored(self):
        # the block is REPLACED, so it tracks the current config rather than growing forever
        self._init("books", "papers")
        text = self._init("books")
        self.assertNotIn("library/papers/", text)

    def test_migration_does_not_delete_user_rules_that_start_with_library(self):
        """A REGRESSION my own migration introduced, caught by an independent verification.

        Filtering every `library/...` line in the whole file removed user-authored rules that
        merely began the same way — including ones written BEFORE the old marker. Only the
        contiguous run following klode's own header is klode's to remove.
        """
        from klode.lib.cli import _gitignore_with_managed_block
        legacy = ("library/my-own-vendored-thing/\n"
                  "# klode: the corpus is copyrighted — sources stay local, cards are tracked\n"
                  "library/books/*.txt\nlibrary/books/*.pdf\n\n# mine\n*.log\nlibrary/scratch/\n")
        out = _gitignore_with_managed_block(legacy, "library/books/*.txt\nlibrary/papers/*.txt")
        self.assertIn("library/my-own-vendored-thing/", out, "a rule before the marker was deleted")
        self.assertIn("library/scratch/", out, "a rule after the block was deleted")
        self.assertIn("*.log", out)
        self.assertNotIn("# klode: the corpus", out)
        self.assertEqual(out.count("library/books/*.txt"), 1)

    def test_a_pre_existing_unterminated_block_is_migrated_not_duplicated(self):
        from klode.lib.cli import _gitignore_with_managed_block
        legacy = ("# klode: the corpus is copyrighted — sources stay local, cards are tracked\n"
                  "library/books/*.txt\nlibrary/books/*.pdf\n\n# mine\n*.log\n")
        out = _gitignore_with_managed_block(legacy, "library/books/*.txt\nlibrary/papers/*.txt")
        self.assertEqual(out.count("library/books/*.txt"), 1)
        self.assertIn("library/papers/*.txt", out)
        self.assertIn("*.log", out)
        self.assertNotIn("# klode: the corpus", out)

class WorkNotDoneIsNeverExitZero(unittest.TestCase):
    """Success-on-work-not-done, the class the audit found four times.

    `check` printed OK without running citation-rot; `build` reported a successful build having
    refreshed nothing; `normalize --check` passed a gate that inspected no file; `zoom --level
    content` returned 0 for a source it never opened, while a missing CARD returned 1. Automation
    reads the exit code, so each of these certified something that had not happened.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-exit-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.kb = self.tmp / "kb"
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "kb-fixture", self.kb)
        self.cfg = str(self.kb / "library.toml")

    def _run(self, *argv):
        from klode.lib.cli import build_parser
        args = build_parser().parse_args(["-c", self.cfg] + list(argv))
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = args.func(args)
        return rc, buf.getvalue() + err.getvalue()

    def _strip_corpus(self):
        for p in (self.kb / "library").rglob("*.txt"):
            p.unlink()

    def test_with_a_corpus_these_all_still_succeed(self):
        # the guard must not simply refuse everything
        self.assertEqual(self._run("build")[0], 0)
        self.assertEqual(self._run("zoom", "brevity", "--level", "content")[0], 0)

    def test_build_that_built_nothing_does_not_report_success(self):
        self._strip_corpus()
        rc, out = self._run("build")
        self.assertEqual(rc, 2)
        self.assertIn("ABSTAINED", out)

    def test_a_normalize_gate_that_inspected_nothing_does_not_pass(self):
        self._strip_corpus()
        rc, out = self._run("normalize", "--check")
        self.assertEqual(rc, 2)
        self.assertIn("nothing was checked", out)

    def test_zoom_content_for_an_uninstalled_source_matches_a_missing_card(self):
        self._strip_corpus()
        rc, _ = self._run("zoom", "brevity", "--level", "content")
        self.assertNotEqual(rc, 0, "unavailable evidence reported as success")
        missing, _ = self._run("zoom", "nosuchcard", "--level", "meta")
        self.assertNotEqual(missing, 0)

    def test_every_abstention_can_be_opted_out_of_explicitly(self):
        self._strip_corpus()
        self.assertEqual(self._run("build", "--allow-unmeasured")[0], 0)
        self.assertEqual(self._run("normalize", "--check", "--allow-unmeasured")[0], 0)
        self.assertEqual(self._run("check", "--allow-unmeasured")[0], 0)

    def test_could_not_measure_is_distinguishable_from_measured_and_failed(self):
        # 1 = measured, failed. 2 = could not measure. Collapsing them loses the distinction
        # that makes an abstention actionable.
        rc_unmeasured, _ = self._run("check")           # corpus present, nothing unmeasured
        self.assertEqual(rc_unmeasured, 0)
        self._strip_corpus()
        self.assertEqual(self._run("check")[0], 2)

class SurfacesAgreeAndFlagsDoWhatTheySay(unittest.TestCase):
    """Findings whose common shape is a flag that parsed, did nothing, and reported success."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-cliflags-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.kb = self.tmp / "kb"
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "kb-fixture", self.kb)
        self.cfg = str(self.kb / "library.toml")

    def _rc(self, *argv):
        from klode.lib.cli import main
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            try:
                rc = main(["-c", self.cfg] + list(argv))
            except SystemExit as e:                 # argparse errors
                rc = e.code if isinstance(e.code, int) else 2
        return rc, buf.getvalue() + err.getvalue()

    def test_a_nonpositive_limit_is_rejected_rather_than_slicing_to_nothing(self):
        # `--limit 0` printed "no matching cards" — a false negative indistinguishable from a real
        # one — and `-1` dropped the last result, while JSON clamped both to 1
        for flag, cmd in (("--limit", ["search", "brevity"]), ("--max", ["zoom", "brevity",
                                                                        "--level", "content",
                                                                        "--grep", "x"])):
            for bad in ("0", "-1"):
                with self.subTest(flag=flag, value=bad):
                    self.assertEqual(self._rc(*cmd, flag, bad)[0], 2)

    def test_apply_and_check_cannot_be_combined(self):
        # together they MUTATED the corpus and silently disabled the gate
        self.assertEqual(self._rc("normalize", "--check", "--apply")[0], 2)

    def test_grep_and_max_are_refused_where_they_would_be_ignored(self):
        for lvl in ("meta", "thin", "full"):
            with self.subTest(level=lvl):
                rc, out = self._rc("zoom", "brevity", "--level", lvl, "--grep", "anything")
                self.assertEqual(rc, 2)
                self.assertIn("only to --level content", out)

    def test_an_explicit_max_is_distinguishable_from_the_default(self):
        # `--max` defaulted to 10, so an explicit `--max 10` looked like omission and the
        # dependency check could not tell "asked for and ignored" from "not asked for"
        rc, out = self._rc("zoom", "brevity", "--level", "thin", "--max", "10")
        self.assertEqual(rc, 2)
        self.assertIn("only to --level content", out)
        rc, out = self._rc("zoom", "brevity", "--level", "content", "--max", "10")
        self.assertEqual(rc, 2)
        self.assertIn("nothing without --grep", out)

    def test_an_empty_registry_is_a_successful_answer_on_both_surfaces(self):
        # "no KBs are registered" is the true state of a fresh install; an empty SEARCH result is
        # a miss. Both are an empty list, so shape cannot tell them apart and the op must.
        import tempfile as _t
        home = Path(_t.mkdtemp()); (home / ".klode").mkdir()
        (home / ".klode" / "registry.toml").write_text("", encoding="utf-8")
        from unittest import mock
        from klode.lib.cli import main
        for flag in ([], ["--json"]):
            with self.subTest(json=bool(flag)), \
                 mock.patch.dict(os.environ, {"HOME": str(home)}), \
                 contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(flag + ["kbs"]), 0)

    def test_an_explicitly_blank_grep_is_not_read_as_no_verification(self):
        rc, out = self._rc("zoom", "brevity", "--level", "content", "--grep", "")
        self.assertEqual(rc, 2)
        self.assertIn("empty phrase", out)

    def test_both_surfaces_reject_an_empty_query(self):
        # the JSON branch returned BEFORE prose's validation, so they disagreed about what a valid
        # request even is
        self.assertEqual(self._rc("search", "")[0], 1)
        self.assertEqual(self._rc("--json", "search", "")[0], 1)

    def test_json_on_a_command_that_cannot_emit_it_refuses_rather_than_printing_prose(self):
        for cmd in ("check", "build", "normalize"):
            with self.subTest(cmd=cmd):
                rc, out = self._rc("--json", cmd)
                self.assertEqual(rc, 2)
                self.assertIn("not implemented", out)

    def test_json_still_works_where_it_is_implemented(self):
        rc, out = self._rc("--json", "search", "brevity")
        self.assertEqual(rc, 0)
        json.loads(out)

    def test_entail_dependent_flags_require_entail(self):
        for flag, val in (("--entail-model", "x"), ("--entail-threshold", "0.9")):
            with self.subTest(flag=flag):
                rc, out = self._rc("check", flag, val)
                self.assertEqual(rc, 2)
                self.assertIn("no effect without --entail", out)

    def test_quiet_suppresses_notes(self):
        for p in (self.kb / "library").rglob("*.txt"):
            p.unlink()
        loud = self._rc("check", "--allow-unmeasured")[1]
        quiet = self._rc("check", "--quiet", "--allow-unmeasured")[1]
        self.assertIn("NOTE", loud)
        self.assertNotIn("NOTE", quiet)


class ProseVerifyGoesThroughTheSharedService(unittest.TestCase):
    """`zoom --level content --grep` called occurrence-only `query.verify` directly, skipping the
    stamped-source freshness and ambiguity checks the JSON path gets through `services.execute`.
    Stale evidence could therefore be CONFIRMED on one surface and refused on the other, for the
    same request — the exact drift the shared service exists to prevent, in the one place the
    guarantee matters most."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-verify-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.kb = self.tmp / "kb"
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "kb-fixture", self.kb)
        self.cfg = str(self.kb / "library.toml")

    def _run(self, *argv):
        from klode.lib.cli import main
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = main(["-c", self.cfg] + list(argv))
        return rc, buf.getvalue() + err.getvalue()

    def test_a_stale_source_is_reported_as_stale_not_as_citation_rot(self):
        src = self.kb / "library" / "books" / "brevity.txt"
        src.write_text(src.read_text(encoding="utf-8") + "\nA BRAND NEW SENTENCE.\n",
                       encoding="utf-8")
        rc, out = self._run("zoom", "brevity", "--level", "content", "--grep", "BRAND NEW")
        self.assertNotEqual(rc, 0)
        self.assertIn("changed since this card was stamped", out)
        # and NOT the wrong diagnosis, which sends the reader to fix an anchor that is fine
        self.assertNotIn("citation rot", out)

    def test_prose_and_json_agree_once_the_source_is_restamped(self):
        src = self.kb / "library" / "books" / "brevity.txt"
        src.write_text(src.read_text(encoding="utf-8") + "\nA BRAND NEW SENTENCE.\n",
                       encoding="utf-8")
        self._run("build", "--stamp")
        rc_prose, out = self._run("zoom", "brevity", "--level", "content", "--grep", "BRAND NEW")
        rc_json, _ = self._run("--json", "zoom", "brevity", "--level", "content",
                               "--grep", "BRAND NEW")
        self.assertEqual(rc_prose, 0, out)
        self.assertEqual(rc_prose, rc_json)


class TerminalControlSequencesAreNeutered(unittest.TestCase):
    """A corpus is whatever PDF you fed it and a card can arrive through the registry, so both are
    untrusted. Raw `\x1b[2J\x1b[H` clears the reader's screen; `\x1b]0;…\x07` sets the window title."""

    def test_source_lines_are_sanitised_before_printing(self):
        from klode.lib.cli import main
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        kb = tmp / "kb"
        shutil.copytree(Path(__file__).resolve().parent / "fixtures" / "kb-fixture", kb)
        src = kb / "library" / "books" / "brevity.txt"
        src.write_text(src.read_text(encoding="utf-8")
                       + "\nNEEDLE \x1b[2J\x1b[H spoofed \x1b]0;pwned\x07\n", encoding="utf-8")
        # re-stamp: the shared service refuses a source that changed since the card was stamped,
        # which is the freshness guarantee the prose path previously bypassed
        with contextlib.redirect_stdout(io.StringIO()):
            main(["-c", str(kb / "library.toml"), "build", "--stamp"])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["-c", str(kb / "library.toml"), "zoom", "brevity",
                  "--level", "content", "--grep", "NEEDLE"])
        out = buf.getvalue()
        self.assertIn("NEEDLE", out)
        self.assertIn("spoofed", out)                 # the TEXT survives
        self.assertNotIn("\x1b[", out)                # the control sequences do not
        self.assertNotIn("\x1b]", out)

    def test_sane_keeps_tabs_and_ordinary_unicode(self):
        from klode.lib.cli import sane
        self.assertEqual(sane("a\tb — café 世界"), "a\tb — café 世界")
        self.assertNotIn("\x1b", sane("x\x1b[31my"))
        self.assertNotIn("\x9b", sane("x\x9bmy"))    # C1 CSI, the single-byte form

if __name__ == "__main__":
    unittest.main()
