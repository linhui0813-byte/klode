"""CriterionSpec v1 — one test per fail-closed rule.

The artifact exists because object-level provenance let a mixed bag of claims inherit one `source:`
pointer. These pin the two properties that closes it:

  * an `explicit` field grounds its OWN value, so a statement cannot contradict its anchor;
  * anything inferred must name its warrant, and "the author did not state this" is sayable
    (`unknown`) rather than manufactured.

Plus the structural guarantees a rubric needs to be worth labelling against: stable ids, a pinned
corpus, behaviorally anchored levels, and an admission gate only a human passes.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import _rubric                                                            # noqa: E402
from klode import lib                                                     # noqa: E402
from klode.gate import (FixtureJudge, SpecError, authoring, load_spec,     # noqa: E402
                        parse_spec, review_draft, validate_spec)
from klode.gate import spec as _specmod                                   # noqa: E402


def reseal(doc):
    """Recompute the approval digest after a test mutates a built doc.

    `approved_digest` binds approval to the body, so any post-build edit invalidates it — correct
    behaviour, but it would mask the rule each negative test is actually probing."""
    if doc.get("admission") == "human_approved":
        doc.pop("approved_digest", None)
        doc["approved_digest"] = _specmod.content_digest(doc)
    return doc

FIX = REPO / "tests" / "fixtures" / "kb-fixture" / "library.toml"
SCHEMA_ID = _specmod.SCHEMA


def _kb(root: Path, *, source_text="the anchor phrase is here", cid="src1", dim="craft",
        stamp=True) -> Path:
    libd = root / "library"
    (libd / "books").mkdir(parents=True, exist_ok=True)
    (libd / "cards").mkdir(exist_ok=True)
    (libd / "frameworks" / "_syntheses").mkdir(parents=True, exist_ok=True)
    (root / "library.toml").write_text(
        '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
        '[bibliography]\nenabled = false\n'
        '[frameworks]\nenabled = true\ndir = "frameworks"\nsyntheses = "_syntheses"\n', encoding="utf-8")
    (libd / "books" / f"{cid}.txt").write_text(source_text, encoding="utf-8")
    fm = [f"id: {cid}", "shelf: books", f"file: library/books/{cid}.txt", "grep_ready: true"]
    if stamp:
        import hashlib
        fm.append(f"source_sha256: {hashlib.sha256(source_text.encode()).hexdigest()}")
    (libd / "cards" / f"{cid}.md").write_text("---\n" + "\n".join(fm) + f"\n---\n# {cid}\n", encoding="utf-8")
    (libd / "cards" / "INDEX.md").write_text(f"# I\n\n- [{cid}]({cid}.md)\n", encoding="utf-8")
    (libd / "frameworks" / "_syntheses" / f"{dim}.md").write_text(
        f"---\ntitle: {dim}\nstatus: canonical\ndimension: {dim}\ncards: [{cid}]\n---\n\n# {dim}\n\n"
        "**Core question:** q?\n\n## Craft\n\nintro.\n\n- **Move.** (grep: `the anchor phrase`)\n",
        encoding="utf-8")
    return root / "library.toml"


def _levels(n=3):
    return [{"score": i, "descriptor": {"value": f"band {i}", "kind": "operator_policy",
                                        "warrant": "fixture band"}} for i in range(n)]


def _doc(cfg, **over):
    """A minimal VALID spec over the `_kb` fixture; `over` mutates one criterion for the negative."""
    crit = {
        "id": "craft.move",
        "statement": {"value": "Do the move.", "kind": "paraphrase", "warrant": "restates the phrase",
                      "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]},
        "evidence": [{"card": "src1", "phrase": "the anchor phrase"}],
        "levels": _levels(),
    }
    crit.update(over)
    return authoring.build(cfg, "craft", ["src1"], [crit], admission="human_approved")


def _mutate(doc, **top):
    for k, v in top.items():
        doc[k] = v
    return reseal(doc)


class EpistemicEnvelope(unittest.TestCase):
    """The load-bearing rules: what each `kind` must carry, and what it may not."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def _validate(self, doc):
        validate_spec(self.cfg, parse_spec(doc), require_stamp=False)

    def test_a_valid_spec_round_trips(self):
        self._validate(_doc(self.cfg))

    def test_explicit_field_must_ground_its_own_value(self):
        # THE defect this artifact exists to close: under object-level provenance a statement could
        # say the OPPOSITE of its anchor and still ground, because only the anchor was checked.
        doc = _doc(self.cfg, statement={
            "value": "Never do the move — keep every one.",     # contradicts the source
            "kind": "explicit",
            "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]})
        with self.assertRaises(SpecError) as e:
            self._validate(doc)
        self.assertIn("does not occur in any of its cited cards", str(e.exception))

    def test_explicit_field_grounding_its_own_value_is_accepted(self):
        self._validate(_doc(self.cfg, statement={
            "value": "the anchor phrase", "kind": "explicit",
            "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]}))

    def test_explicit_takes_no_warrant(self):
        with self.assertRaises(SpecError):
            parse_spec(_doc(self.cfg, statement={
                "value": "the anchor phrase", "kind": "explicit", "warrant": "why",
                "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]}))

    def test_inference_without_a_warrant_is_rejected(self):
        for kind in ("paraphrase", "derived", "operator_policy"):
            with self.subTest(kind=kind), self.assertRaises(SpecError) as e:
                parse_spec(_doc(self.cfg, statement={
                    "value": "Do the move.", "kind": kind,
                    "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]}))
            self.assertIn("warrant", str(e.exception))

    def test_paraphrase_must_cite_something(self):
        with self.assertRaises(SpecError):
            parse_spec(_doc(self.cfg, statement={
                "value": "Do the move.", "kind": "paraphrase", "warrant": "w"}))

    def test_unknown_means_not_stated_and_carries_no_value(self):
        # a schema slot with no `unknown` is a manufacturing order; this is what makes it optional
        doc = _doc(self.cfg, fields={"exceptions": {"value": None, "kind": "unknown"}})
        self._validate(doc)
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, fields={
                "exceptions": {"value": "none that the author lists", "kind": "unknown"}}))
        self.assertIn("must be null", str(e.exception))

    def test_unknown_takes_no_warrant_or_evidence(self):
        # both halves, separately: testing only the warrant left the evidence rule unguarded
        for extra in ({"warrant": "smuggled"},
                      {"evidence": [{"card": "src1", "phrase": "the anchor phrase"}]}):
            with self.subTest(extra=extra), self.assertRaises(SpecError) as e:
                parse_spec(_doc(self.cfg, fields={
                    "exceptions": {"value": None, "kind": "unknown", **extra}}))
            self.assertIn("takes no warrant and no evidence", str(e.exception))

    def test_an_unrecognised_kind_is_rejected(self):
        with self.assertRaises(SpecError):
            parse_spec(_doc(self.cfg, statement={"value": "x", "kind": "probably-true"}))


class UntrustedJson(unittest.TestCase):
    """A rubric is hand-edited JSON, so it is untrusted input. `load`/`parse` promise a clean
    SpecError; an escaping TypeError/AttributeError means a malformed file crashed the tool instead
    of being diagnosed, and an `or`-default silently ERASED a supplied wrong type."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec-json-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def _bad(self, **over):
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, **over))
        return str(e.exception)

    def test_wrong_scalar_types_raise_specerror_not_typeerror(self):
        ev = [{"card": "src1", "phrase": "the anchor phrase"}]
        cases = [
            ({"statement": {"value": "x", "kind": "paraphrase", "warrant": 1, "evidence": ev}}, "warrant"),
            ({"statement": {"value": 1, "kind": "paraphrase", "warrant": "w", "evidence": ev}}, "value"),
            ({"statement": {"value": "x", "kind": "paraphrase", "warrant": "w", "evidence": ""}}, "must be a list"),
            ({"fields": [1]}, "must be an object"),
            ({"levels": {"a": 1}}, "must be a list"),
            ({"id": 7}, "must be a string"),
            ({"evidence": [{"card": "src1", "phrase": "the anchor phrase", "before": 1}]}, "before"),
            ({"evidence": [{"card": "src1", "phrase": "the anchor phrase", "nth": "1"}]}, "nth"),
            ({"evidence": [{"card": "src1", "phrase": "the anchor phrase", "nth": True}]}, "nth"),
            ({"evidence": [{"card": "src1", "phrase": "the anchor phrase", "nth": 0}]}, ">= 1"),
            ({"evidence": [{"card": 1, "phrase": "p"}]}, "card"),
            ({"levels": [{"score": True, "descriptor": {"value": "b", "kind": "operator_policy",
                                                        "warrant": "w"}}] * 2}, "integer"),
        ]
        for over, word in cases:
            with self.subTest(over=over):
                self.assertIn(word, self._bad(**over))

    def test_a_falsey_wrong_type_is_rejected_not_silently_erased(self):
        # `evidence: ""` used to become [] and `fields: []` used to become {} — the wrong type
        # vanished instead of being diagnosed, which is how a citation quietly disappears
        self.assertIn("must be a list", self._bad(
            statement={"value": "x", "kind": "paraphrase", "warrant": "w", "evidence": ""}))
        self.assertIn("must be an object", self._bad(fields=[]))

    def test_invisible_text_does_not_satisfy_non_empty(self):
        ev = [{"card": "src1", "phrase": "the anchor phrase"}]
        self.assertIn("visible text", self._bad(
            statement={"value": "​", "kind": "paraphrase", "warrant": "w", "evidence": ev}))
        self.assertIn("visible text", self._bad(
            statement={"value": "x", "kind": "paraphrase", "warrant": "​", "evidence": ev}))

    def test_malformed_top_level_shapes_are_rejected(self):
        for doc, word in (([], "must be an object"), ({"schema": SCHEMA_ID}, "dimension")):
            with self.subTest(doc=doc), self.assertRaises(SpecError) as e:
                parse_spec(doc)
            self.assertIn(word, str(e.exception))

    def test_a_dimension_that_is_not_a_safe_name_is_rejected(self):
        for bad in ("../escape", "a/b", "", ".", "9lives"):
            with self.subTest(bad=bad):
                doc = _doc(self.cfg)
                _mutate(doc, dimension=bad)
                with self.assertRaises(SpecError):
                    parse_spec(doc)

    def test_a_malformed_fingerprint_digest_is_rejected(self):
        doc = _doc(self.cfg)
        _mutate(doc, fingerprint={"sources": {"src1": 7}})
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("sha256", str(e.exception))

    def test_duplicate_json_keys_are_rejected(self):
        # json.loads keeps the LAST duplicate silently, so a reviewer reading top-down and the
        # parser could disagree about whether a rubric is approved
        out = Path(self.cfg.criteria) / "craft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(_doc(self.cfg))
        out.write_text(body[:-1] + ', "admission": "human_approved"}', encoding="utf-8")
        with self.assertRaises(SpecError) as e:
            load_spec(self.cfg, "craft", require_stamp=False)
        self.assertIn("duplicate key", str(e.exception))

    def test_unreadable_or_non_utf8_files_raise_specerror(self):
        out = Path(self.cfg.criteria) / "craft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b'{"schema": "\xff\xfe bad utf8"}')
        with self.assertRaises(SpecError) as e:
            load_spec(self.cfg, "craft", require_stamp=False)
        self.assertIn("UTF-8", str(e.exception))
        out.write_text("{not json", encoding="utf-8")
        with self.assertRaises(SpecError) as e:
            load_spec(self.cfg, "craft", require_stamp=False)
        self.assertIn("invalid JSON", str(e.exception))

    def test_a_traversing_dimension_cannot_escape_the_criteria_dir(self):
        from klode.gate.spec import spec_path
        for bad in ("../../etc/passwd", "/etc/passwd", "a/b", "..", "x/../../y"):
            with self.subTest(bad=bad), self.assertRaises(SpecError):
                spec_path(self.cfg, bad)

    def test_a_filed_dimension_mismatch_is_rejected(self):
        out = Path(self.cfg.criteria) / "other.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(_doc(self.cfg)), encoding="utf-8")   # declares "craft"
        with self.assertRaises(SpecError) as e:
            load_spec(self.cfg, "other", require_stamp=False)
        self.assertIn("filed as", str(e.exception))

    def test_advisory_criticality_is_refused_while_unimplemented(self):
        self.assertIn("not implemented", self._bad(criticality="advisory"))
        self.assertIn("must be 'required'", self._bad(criticality="optional"))

    def test_panel_membership_covers_field_level_citations_too(self):
        # the panel check used to be probed only through criterion-level evidence
        self.assertIn("not in the panel", self._bad(statement={
            "value": "x", "kind": "paraphrase", "warrant": "w",
            "evidence": [{"card": "elsewhere", "phrase": "the anchor phrase"}]}))

    def test_a_duplicate_panel_card_is_rejected(self):
        doc = _doc(self.cfg)
        _mutate(doc, panel=["src1", "src1"])
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("duplicate card", str(e.exception))


class ApprovalBinding(unittest.TestCase):
    """Approval is honor-based about the human and mechanical about the content."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec-appr-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def test_editing_an_approved_rubric_invalidates_the_approval(self):
        doc = _doc(self.cfg)
        parse_spec(doc)                                   # approved and sealed
        doc["criteria"][0]["statement"]["value"] = "Something the approver never read."
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("EDITED since it was approved", str(e.exception))

    def test_approval_without_a_digest_is_refused(self):
        doc = _doc(self.cfg)
        doc.pop("approved_digest")
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("requires an `approved_digest`", str(e.exception))

    def test_reformatting_does_not_invalidate_approval(self):
        # the digest is canonical, so whitespace/key-order changes are not "edits"
        doc = _doc(self.cfg)
        again = json.loads(json.dumps(doc, indent=4, sort_keys=True))
        parse_spec(again)


class StructuralGuarantees(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def test_positional_ids_are_rejected(self):
        # `C1` is a position, not a name: every human label collected against it dies on a reorder
        for positional in ("C1", "C1a", "C1A"):
            with self.subTest(positional=positional), self.assertRaises(SpecError) as e:
                parse_spec(_doc(self.cfg, id=positional))
            self.assertIn("positional", str(e.exception))

    def test_duplicate_ids_are_rejected(self):
        doc = _doc(self.cfg)
        doc["criteria"].append(json.loads(json.dumps(doc["criteria"][0])))
        reseal(doc)
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("duplicate", str(e.exception))

    def test_regex_evidence_is_refused_in_a_canonical_rubric(self):
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, evidence=[
                {"card": "src1", "phrase": "the .* phrase", "regex": True}]))
        self.assertIn("regex", str(e.exception))

    def test_levels_must_be_anchored_and_contiguous(self):
        with self.assertRaises(SpecError):                       # a one-point "scale" is not one
            parse_spec(_doc(self.cfg, levels=_levels(1)))
        gappy = _levels(3)
        gappy[2]["score"] = 5
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, levels=gappy))
        self.assertIn("contiguously", str(e.exception))

    def test_a_level_descriptor_cannot_be_unknown(self):
        lv = _levels(3)
        lv[1]["descriptor"] = {"value": None, "kind": "unknown"}
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, levels=lv))
        self.assertIn("unlabelled level", str(e.exception))

    def test_a_citation_outside_the_panel_is_rejected(self):
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(self.cfg, evidence=[{"card": "elsewhere", "phrase": "the anchor phrase"}]))
        self.assertIn("not in the panel", str(e.exception))

    def test_wrong_schema_is_rejected(self):
        doc = _doc(self.cfg)
        _mutate(doc, schema="klode.criterion-spec/v2")
        with self.assertRaises(SpecError):
            parse_spec(doc)


class CorpusPinning(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec3-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def test_an_unpinned_panel_card_is_rejected(self):
        doc = _doc(self.cfg)
        _mutate(doc, fingerprint={"sources": {}})
        with self.assertRaises(SpecError) as e:
            validate_spec(self.cfg, parse_spec(doc), require_stamp=False)
        self.assertIn("does not pin its corpus", str(e.exception))

    def test_a_moved_source_fails_the_pin(self):
        doc = _doc(self.cfg)
        (self.tmp / "library" / "books" / "src1.txt").write_text("the anchor phrase is here now",
                                                                 encoding="utf-8")
        with self.assertRaises(SpecError) as e:
            validate_spec(self.cfg, parse_spec(doc), require_stamp=False)
        self.assertIn("has changed since this rubric was authored", str(e.exception))

    def test_the_fingerprint_is_computed_not_authored(self):
        digest = lib.source_digest(self.cfg, "src1")
        self.assertEqual(authoring.fingerprint(self.cfg, ["src1"])["sources"]["src1"], digest)
        self.assertIsNone(lib.source_digest(self.cfg, "no-such-card"))


class AdmissionGate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec4-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def _write(self, doc):
        out = Path(self.cfg.criteria) / "craft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc), encoding="utf-8")

    def test_the_gate_refuses_a_candidate_rubric(self):
        # agents generate candidates; only a human promotes them to canon
        doc = _doc(self.cfg)
        doc["admission"] = "candidate"
        self._write(doc)
        with self.assertRaises(SpecError) as e:
            review_draft(self.cfg, "d", "craft", FixtureJudge({}, default_fraction=1.0),
                         require_stamp=False)
        self.assertIn("not 'human_approved'", str(e.exception))

    def test_an_approved_rubric_scores(self):
        self._write(_doc(self.cfg))
        v = review_draft(self.cfg, "d", "craft", FixtureJudge({}, default_fraction=1.0),
                         require_stamp=False)
        self.assertEqual(v.decision, "Go")

    def test_a_missing_rubric_names_the_authoring_step(self):
        with self.assertRaises(SpecError) as e:
            review_draft(self.cfg, "d", "craft", FixtureJudge({}, default_fraction=1.0))
        self.assertIn("klode.gate derive", str(e.exception))


class BehavioralScales(unittest.TestCase):
    """A rubric declares its own 0..N levels; the verdict must not let scale length become weight."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec5-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp, source_text="alpha here and beta there"))

    def _mixed(self):
        def crit(cid, phrase, n):
            return {"id": cid,
                    "statement": {"value": cid, "kind": "paraphrase", "warrant": "w",
                                  "evidence": [{"card": "src1", "phrase": phrase}]},
                    "evidence": [{"card": "src1", "phrase": phrase}],
                    "levels": _levels(n)}
        doc = authoring.build(self.cfg, "craft", ["src1"],
                              [crit("craft.short", "alpha", 4),     # 0..3
                               crit("craft.long", "beta", 11)],     # 0..10
                              admission="human_approved")
        out = Path(self.cfg.criteria) / "craft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(doc), encoding="utf-8")

    def test_scales_are_normalized_before_averaging(self):
        self._mixed()
        # top of both scales -> 100%, regardless of how many points each scale has
        v = review_draft(self.cfg, "d", "craft", FixtureJudge({}, default_fraction=1.0),
                         require_stamp=False)
        self.assertEqual(v.score, 100)
        # half of each scale is ~50% for both: raw summing would let the 0..10 criterion dominate
        v = review_draft(self.cfg, "d", "craft",
                         FixtureJudge({"craft.short": (0, "floor"), "craft.long": (10, "ceiling")}),
                         require_stamp=False)
        self.assertEqual(v.score, 50)                    # (0% + 100%) / 2, not 10/13

    def test_a_score_above_a_criterions_own_ceiling_fails_loud(self):
        self._mixed()
        with self.assertRaises(ValueError) as e:
            review_draft(self.cfg, "d", "craft",
                         FixtureJudge({"craft.short": (7, "off-scale")}, default_fraction=0.5),
                         require_stamp=False)
        self.assertIn("out of range 0..3", str(e.exception))


class Authoring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec6-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def test_a_derived_candidate_does_not_validate(self):
        # the design: `derive` never invents a warrant or a descriptor, so its output is a WORKLIST,
        # not a rubric. If this ever passes, the tool has started manufacturing the judgment.
        doc = authoring.derive(self.cfg, "craft")
        self.assertEqual(doc["admission"], "candidate")
        with self.assertRaises(SpecError):
            parse_spec(doc)

    def test_each_derive_omission_is_independently_fatal(self):
        # The blanket "it raises" above is mutation-vacuous: the candidate has SEVERAL holes at once,
        # so deleting any one validator rule still leaves another raising. Repair all but one and
        # assert the exact remaining failure — that is what pins each omission individually.
        def repaired():
            d = authoring.derive(self.cfg, "craft")
            for c in d["criteria"]:
                c["statement"]["warrant"] = "w"
                if c.get("guidance", {}).get("kind") == "derived":
                    c["guidance"]["warrant"] = "w"
                for i, lv in enumerate(c["levels"]):
                    lv["descriptor"]["value"] = f"band {i}"
                    lv["descriptor"]["warrant"] = "w"
            return d

        parse_spec(repaired())                       # every hole filled -> it parses

        d = repaired()                               # ONLY the statement warrant missing
        d["criteria"][0]["statement"].pop("warrant")
        with self.assertRaises(SpecError) as e:
            parse_spec(d)
        self.assertIn("requires a `warrant`", str(e.exception))

        d = repaired()                               # ONLY a level descriptor blank
        d["criteria"][0]["levels"][2]["descriptor"]["value"] = ""
        with self.assertRaises(SpecError) as e:
            parse_spec(d)
        self.assertIn("visible text", str(e.exception))

        d = repaired()                               # ONLY a descriptor warrant missing
        d["criteria"][0]["levels"][2]["descriptor"].pop("warrant")
        with self.assertRaises(SpecError) as e:
            parse_spec(d)
        self.assertIn("requires a `warrant`", str(e.exception))

    def test_derived_ids_are_stable_slugs_not_positions(self):
        doc = authoring.derive(self.cfg, "craft")
        self.assertEqual(doc["criteria"][0]["id"], "craft.move")

    def test_the_shipped_fixture_rubric_is_valid_and_approved(self):
        cfg = lib.Config.load(FIX)
        spec = load_spec(cfg, "pacing")
        self.assertTrue(spec.approved)
        self.assertEqual([c.id for c in spec.criteria],
                         ["pacing.cut-inferable", "pacing.vary-sentence-length", "pacing.end-on-a-turn"])
        self.assertTrue(all(c.max_score == 5 for c in spec.criteria))
        kinds = {f.kind for c in spec.criteria for f in
                 (c.statement, c.guidance, *c.fields.values(), *(l.descriptor for l in c.levels))}
        self.assertEqual(kinds, {"explicit", "paraphrase", "derived", "operator_policy", "unknown"})




class Round2Regressions(unittest.TestCase):
    """Defects introduced BY the round-1 fixes, each reproduced before being closed.

    A fix that creates a new failure is not a fix. These are the cases an audit caught after the
    first pass, kept as tests so the second pass cannot quietly undo itself."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec-r2-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _two_card_kb(self) -> "lib.Config":
        """card `a` holds a hyphen-folded near-miss; card `b` holds the exact quote."""
        import hashlib
        root = self.tmp
        libd = root / "library"
        (libd / "books").mkdir(parents=True, exist_ok=True)
        (libd / "cards").mkdir(exist_ok=True)
        (libd / "frameworks" / "_syntheses").mkdir(parents=True, exist_ok=True)
        (root / "library.toml").write_text(
            '[library]\ndir = "library"\ncards = "cards"\nshelves = ["books"]\n'
            '[bibliography]\nenabled = false\n'
            '[frameworks]\nenabled = true\ndir = "frameworks"\nsyntheses = "_syntheses"\n',
            encoding="utf-8")
        texts = {"a": "Writers must re-sign the agreement.\n",
                 "b": "Writers must resign the agreement.\n"}
        for cid, text in texts.items():
            (libd / "books" / f"{cid}.txt").write_text(text, encoding="utf-8")
            sha = hashlib.sha256(text.encode()).hexdigest()
            (libd / "cards" / f"{cid}.md").write_text(
                f"---\nid: {cid}\nshelf: books\nfile: library/books/{cid}.txt\ngrep_ready: true\n"
                f"source_sha256: {sha}\n---\n# {cid}\n", encoding="utf-8")
        (libd / "cards" / "INDEX.md").write_text("# I\n\n- [a](a.md)\n- [b](b.md)\n", encoding="utf-8")
        return lib.Config.load(root / "library.toml")

    def test_an_exact_quote_in_a_later_card_is_accepted(self):
        # the round-1 fix raised on the FIRST folded card and never looked at the rest, so a field
        # whose exact quote lived in its second citation was falsely rejected
        cfg = self._two_card_kb()
        quote = "Writers must resign the agreement"
        crit = {
            "id": "craft.quote",
            "statement": {"value": quote, "kind": "explicit",
                          "evidence": [{"card": "a", "phrase": "Writers must re-sign"},
                                       {"card": "b", "phrase": quote}]},
            "evidence": [{"card": "b", "phrase": quote}],
            "levels": _levels(),
        }
        doc = authoring.build(cfg, "craft", ["a", "b"], [crit], admission="human_approved")
        validate_spec(cfg, parse_spec(doc), require_stamp=False)      # must NOT raise

    def test_a_folded_only_quote_still_fails_and_names_every_card(self):
        cfg = self._two_card_kb()
        crit = {
            "id": "craft.folded",
            "statement": {"value": "Writers must resign the agreement", "kind": "explicit",
                          "evidence": [{"card": "a", "phrase": "Writers must re-sign"}]},
            "evidence": [{"card": "a", "phrase": "Writers must re-sign"}],
            "levels": _levels(),
        }
        doc = authoring.build(cfg, "craft", ["a"], [crit], admission="human_approved")
        with self.assertRaises(SpecError) as e:
            validate_spec(cfg, parse_spec(doc), require_stamp=False)
        self.assertIn("only after normalization", str(e.exception))

    def test_unpaired_surrogates_are_rejected_as_specerror(self):
        # they survive JSON but not .encode('utf-8'), and escaped as UnicodeEncodeError from
        # content_digest — outside the promised SpecError boundary
        cfg = lib.Config.load(_kb(self.tmp / "s"))
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(cfg, statement={
                "value": "bad \ud800 text", "kind": "paraphrase", "warrant": "w",
                "evidence": [{"card": "src1", "phrase": "the anchor phrase"}]}))
        self.assertIn("surrogate", str(e.exception))

    def test_a_wrong_typed_fingerprint_is_rejected_not_erased(self):
        cfg = lib.Config.load(_kb(self.tmp / "f"))
        doc = _doc(cfg)
        _mutate(doc, fingerprint=[])            # `or {}` silently turned this into {}
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("must be an object", str(e.exception))

    def test_a_falsey_regex_key_is_still_refused(self):
        cfg = lib.Config.load(_kb(self.tmp / "r"))
        with self.assertRaises(SpecError) as e:
            parse_spec(_doc(cfg, evidence=[
                {"card": "src1", "phrase": "the anchor phrase", "regex": 0}]))
        self.assertIn("regex evidence is not allowed", str(e.exception))

    def test_the_fingerprint_is_frozen_all_the_way_down(self):
        cfg = lib.Config.load(_kb(self.tmp / "z"))
        spec = parse_spec(_doc(cfg))
        with self.assertRaises(TypeError):       # the inner `sources` map was still writable
            spec.fingerprint["sources"]["src1"] = "0" * 64


class Round3(unittest.TestCase):
    """Round-2 fixes that the round-2 verify found still open, plus the untested ones."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-spec-r3-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.cfg = lib.Config.load(_kb(self.tmp))

    def test_a_huge_integer_is_a_specerror_not_a_bare_valueerror(self):
        # the int digit-conversion limit raises ValueError, not JSONDecodeError, so it escaped the
        # boundary `load` promises
        out = Path(self.cfg.criteria) / "craft.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text('{"schema": "x", "n": ' + "9" * 5000 + "}", encoding="utf-8")
        with self.assertRaises(SpecError) as e:
            load_spec(self.cfg, "craft", require_stamp=False)
        self.assertIn("invalid JSON", str(e.exception))

    def test_non_string_field_names_are_rejected(self):
        crit = json.loads(json.dumps(_doc(self.cfg)["criteria"][0]))
        crit["fields"] = {}
        doc = authoring.build(self.cfg, "craft", ["src1"], [crit], admission="candidate")
        parse_spec(doc)                                    # empty fields is fine
        spec_crit = doc["criteria"][0]
        spec_crit["fields"] = {"": {"value": None, "kind": "unknown"}}
        with self.assertRaises(SpecError) as e:
            parse_spec(doc)
        self.assertIn("field names must be non-empty strings", str(e.exception))

    def test_content_digest_never_raises_on_a_parsed_document(self):
        import klode.gate.spec as sp
        self.assertIsInstance(sp.content_digest({"a": "\ud800"}), str)     # lone surrogate
        with self.assertRaises(SpecError):                                 # unserializable
            sp.content_digest({"a": {1, 2}})

    def test_the_fingerprint_is_frozen_at_every_depth(self):
        doc = _doc(self.cfg)
        doc["fingerprint"]["extra"] = {"nested": {"deep": 1}}
        _mutate(doc)
        spec = parse_spec(doc)
        with self.assertRaises(TypeError):
            spec.fingerprint["extra"]["nested"]["deep"] = 2

    def test_half_up_quantization_does_not_lose_the_tie_to_binary_float(self):
        # 0.58 * 25 is 14.499999999999998 in binary float; the intended 14.5 must round to 15
        class Item:
            id, max_score = "x", 25
        self.assertEqual(FixtureJudge({}, default_fraction=0.58).score("d", [Item()])[0].score, 15)
        Item.max_score = 5
        self.assertEqual(FixtureJudge({}, default_fraction=0.9).score("d", [Item()])[0].score, 5)


class AtomicWrites(unittest.TestCase):
    """`derive` must never overwrite, and must never publish a half-written rubric."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="klode-write-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_derive_refuses_to_overwrite_and_leaves_no_temp_files(self):
        from klode.gate.__main__ import _write
        target = self.tmp / "r.json"
        _write(target, {"a": 1}, exclusive=True)
        self.assertEqual(json.loads(target.read_text()), {"a": 1})
        with self.assertRaises(SystemExit):
            _write(target, {"a": 2}, exclusive=True)
        self.assertEqual(json.loads(target.read_text()), {"a": 1})      # untouched
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["r.json"])

    def test_replace_is_atomic_and_cleans_up(self):
        from klode.gate.__main__ import _write
        target = self.tmp / "r.json"
        _write(target, {"a": 1}, exclusive=True)
        _write(target, {"a": 2})
        self.assertEqual(json.loads(target.read_text()), {"a": 2})
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["r.json"])

    def test_the_temp_name_is_not_predictable_from_the_target(self):
        # a name derived from target+pid can be pre-created as a symlink, and plain "w" follows it,
        # truncating whatever it points at
        from klode.gate.__main__ import _write
        import os
        victim = self.tmp / "victim"
        victim.write_text("precious")
        target = self.tmp / "r.json"
        (self.tmp / f".r.json.{os.getpid()}.tmp").symlink_to(victim)
        _write(target, {"a": 1}, exclusive=True)
        self.assertEqual(victim.read_text(), "precious")


if __name__ == "__main__":
    unittest.main()
