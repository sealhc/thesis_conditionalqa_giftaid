"""Regression tests for the weaknesses found in the standalone A1 judge (v7_A1Judge_v1): each fabricated judge reply that
the old validator accepted must now be REJECTED, and stale / tampered inputs must be caught."""
import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, stepwise_review as sr, judge_review as jr
from test_stepwise_review import make_run, complete, put
from test_judge_review import perfect_client


def mutated(template, edit):
    """A judge that behaves perfectly except that `edit(obj)` corrupts its A1 mapping reply."""
    good = perfect_client(template)
    def call(system, prompt):
        text, meta = good(system, prompt)
        if 'TASK: A1 mapping' in prompt:
            o = json.loads(text); edit(o); text = json.dumps(o)
        return text, meta
    return call


class A1JudgeStrictness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.run = make_run(self.base); self.t = sr.build_template(self.run)
    def tearDown(self): self.tmp.cleanup()

    def rejected(self, edit, fragment=None):
        (self.run / sr.REVIEW_JUDGE).unlink(missing_ok=True)
        res = jr.judge_run(self.run, mutated(self.t, edit), 'judge-x', force=True)
        self.assertTrue(res.startswith('REJECTED'), res)
        if fragment: self.assertIn(fragment, res)
        self.assertFalse((self.run / sr.REVIEW_JUDGE).exists())

    # --- the eight acceptances demonstrated in the audit ---
    def test_full_without_atoms(self):
        self.rejected(lambda o: o['reference_checks'][0].update(model_atom_ids=[]))          # rejected at reply level: a1 is then unassessed
        r = complete(self.t); r['A1']['reference_conditions'][0]['matched_A1_atom_ids'] = []   # and the worksheet validator itself says why
        self.assertIn('needs at least one matched atom', sr.validate_review(self.t, r)[1])
    def test_missing_with_atoms(self):
        self.rejected(lambda o: o['reference_checks'][0].update(coverage='MISSING'))
        r = complete(self.t); r['A1']['reference_conditions'][0]['coverage'] = 'MISSING'
        self.assertIn('MISSING must have no matched atoms', sr.validate_review(self.t, r)[1])
    def test_empty_rationale(self):
        self.rejected(lambda o: o['reference_checks'][0].update(rationale='  '), 'rationale')
    def test_atom_never_assessed(self):
        def e(o):
            o['reference_checks'][0].update(coverage='MISSING', model_atom_ids=[])     # a1 no longer covered -> must be assessed as extra
        self.rejected(e, 'no answer for')
    def test_atom_both_matched_and_unsupported(self):
        self.rejected(lambda o: o['extra_atoms'].append({'model_atom_id': 'a1', 'assessment': 'UNSUPPORTED', 'rationale': 'r'}), 'unknown ID')
    def test_undetermined_logic_is_accepted_but_flagged_for_human_check(self):
        res = jr.judge_run(self.run, mutated(self.t, lambda o: o['logic'].update(assessment='UNDETERMINED')), 'judge-x', force=True)
        self.assertEqual(res, 'OK')
        sr.score_cmd(type('A', (), {'run': str(self.run), 'source': 'judge'}))
        self.assertTrue(json.loads((self.run / sr.SCORE_JUDGE).read_text())['needs_human_check'])
    def test_invalid_confidence(self):
        self.rejected(lambda o: o.update(confidence='very sure'), 'confidence')
    def test_missing_keys_give_a_clean_rejection_not_a_crash(self):
        self.rejected(lambda o: o['logic'].pop('assessment'), 'logic')
        self.rejected(lambda o: o.pop('scope'), 'scope')
        self.rejected(lambda o: o.pop('reference_checks'), 'reference_checks')

    # --- more cross-field and enum rules ---
    def test_unknown_or_duplicate_reference_ids_and_bad_enums(self):
        self.rejected(lambda o: o['reference_checks'].append(dict(o['reference_checks'][0])), 'more than once')
        self.rejected(lambda o: o['reference_checks'][0].update(gold_id='G_ZZ'), 'unknown ID')
        self.rejected(lambda o: o['reference_checks'][0].update(coverage='GOOD'), 'coverage')
        self.rejected(lambda o: o['logic'].update(assessment='NOT_EQUIVALENT', issues=[]), 'concrete problem')
        self.rejected(lambda o: o['scope'].update(assessment='MISMATCH', issues=['']), 'concrete problem')
        self.rejected(lambda o: o.update(uncertain_cases='none'), 'uncertain_cases')

    def test_partial_and_incorrect_semantics(self):
        def partial(o):                       # a1 only PARTIALly covers G_C1: still counts as a matched (MATCHES_REFERENCE) atom, but recall stays strict
            o['reference_checks'][0].update(coverage='PARTIAL')
        self.assertEqual(jr.judge_run(self.run, mutated(self.t, partial), 'judge-x', force=True), 'OK')
        sr.score_cmd(type('A', (), {'run': str(self.run), 'source': 'judge'}))
        a1 = json.loads((self.run / sr.SCORE_JUDGE).read_text())['A1']
        self.assertEqual(a1['reference_condition_recall'], {'numerator': 7, 'denominator': 8, 'rate': 0.875})
        self.assertEqual(a1['reference_condition_recall_lenient_full_or_partial']['numerator'], 8)
        self.assertEqual(a1['coverage_counts']['PARTIAL'], 1)
        def incorrect_ok(o):                  # a1 is an INCORRECT attempt at G_C1 -> it must then be assessed UNSUPPORTED/AMBIGUOUS
            o['reference_checks'][0].update(coverage='INCORRECT')
            o['extra_atoms'].append({'model_atom_id': 'a1', 'assessment': 'UNSUPPORTED', 'rationale': 'wrong entity'})
        self.assertEqual(jr.judge_run(self.run, mutated(self.t, incorrect_ok), 'judge-x', force=True), 'OK')
        sr.score_cmd(type('A', (), {'run': str(self.run), 'source': 'judge'}))
        a1 = json.loads((self.run / sr.SCORE_JUDGE).read_text())['A1']
        self.assertEqual(a1['atom_assessment_counts']['UNSUPPORTED'], 1)
        self.assertEqual(a1['model_atom_precision_policy_supported'], {'numerator': 7, 'denominator': 8, 'rate': 0.875})   # precision exists now
        def incorrect_bad(o):
            incorrect_ok(o); o['extra_atoms'][-1]['assessment'] = 'SUPPORTED_ADDITIONAL'
        self.rejected(incorrect_bad, 'INCORRECT attempt')

    def test_strict_judge_validator_demands_rationales_confidence_and_uncertain_cases(self):
        base = complete(self.t); base.update(reviewer='LLM:x', confidence='HIGH', uncertain_cases=[])
        for c in base['A1']['reference_conditions']: c['rationale'] = 'r'
        for a in base['A1']['model_atoms']:
            if a['atom_assessment'] != 'MATCHES_REFERENCE': a['rationale'] = 'r'
        for i in base['A2']['items']: i['rationale'] = 'r'
        for o in base['final_outputs']:
            o['reasoning']['rationale'] = 'r'
            if o['conditional_answer']['applicable']: o['conditional_answer']['rationale'] = 'r'
        self.assertEqual(sr.validate_review(self.t, base, strict_judge=True), (None, None))
        for mutate, frag in (
            (lambda r: r['A1']['reference_conditions'][0].update(rationale=''), 'rationale'),
            (lambda r: r['A2']['items'][0].update(rationale=' '), 'rationale'),
            (lambda r: r['final_outputs'][0]['reasoning'].update(rationale=''), 'rationale'),
            (lambda r: r.update(confidence=None), 'confidence'),
            (lambda r: r.update(uncertain_cases=[1]), 'uncertain_cases')):
            r = json.loads(json.dumps(base)); mutate(r)
            status, msg = sr.validate_review(self.t, r, strict_judge=True)
            self.assertEqual(status, 'INVALID_REVIEW', msg); self.assertIn(frag, msg)
            self.assertEqual(sr.validate_review(self.t, r, strict_judge=False)[0] in (None, 'INVALID_REVIEW'), True)

    def test_validator_itself_rejects_each_inconsistent_worksheet(self):
        for mutate, frag in (
            (lambda r: r['A1']['model_atoms'][0].update(atom_assessment='UNSUPPORTED', rationale='r'), 'must be MATCHES_REFERENCE'),
            (lambda r: r['A1']['model_atoms'][0].update(atom_assessment='SUPPORTED_ADDITIONAL', rationale='r') or r['A1']['model_atoms'][1].update(atom_assessment='MATCHES_REFERENCE'), None),
            (lambda r: r['A1']['logic'].update(logic_assessment='maybe'), 'logic_assessment'),
            (lambda r: r.update(confidence='certain'), 'confidence')):
            r = complete(self.t); r['judge_meta'] = {}; mutate(r)
            status, msg = sr.validate_review(self.t, r, strict_judge=False)
            self.assertIsNotNone(status, msg)
            if frag: self.assertIn(frag, msg)


class LockedInputsAndIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_reference_list_cannot_be_trimmed_and_stale_a1_is_detected(self):
        run = make_run(self.base); t = sr.build_template(run)
        self.assertEqual(jr.judge_run(run, perfect_client(t), 'judge-x'), 'OK')
        review = json.loads((run / sr.REVIEW_JUDGE).read_text())
        trimmed = json.loads(json.dumps(review)); trimmed['A1']['reference_conditions'] = trimmed['A1']['reference_conditions'][:1]
        self.assertEqual(sr.validate_review(t, trimmed, strict_judge=True)[0], 'INVALID_REVIEW')          # 8 -> 1 condition: caught (old tool: recall 1.0)
        a1 = json.loads((run / 'A1_parsed.json').read_text()); a1['atoms'][0]['proposition'] = 'changed after judging'
        put(run, 'A1_parsed.json', a1)
        t2 = sr.build_template(run)
        self.assertEqual(sr.validate_review(t2, review, strict_judge=True)[0], 'INVALID_REVIEW')          # stale: A1 edited after the judge saw it
        with self.assertRaises(SystemExit):
            sr.score_cmd(type('A', (), {'run': str(run), 'source': 'judge'}))

    def test_judge_identity_and_settings_are_recorded(self):
        run = make_run(self.base); t = sr.build_template(run)
        jr.judge_run(run, perfect_client(t), 'judge-x', settings={'temperature': 0.0, 'max_tokens': 8192, 'mode': 'api'})
        meta = json.loads((run / sr.REVIEW_JUDGE).read_text())['judge_meta']
        self.assertEqual(meta['judge_model_requested'], 'judge-x'); self.assertEqual(meta['judge_models_returned'], ['fake-judge'])
        self.assertEqual(meta['settings']['temperature'], 0.0)
        self.assertEqual(set(meta['prompt_hashes']), {'system.txt', 'map.txt', 'state.txt', 'final.txt'})
        self.assertIn('A1', meta['inputs_provenance']['run_stage_sha256']); self.assertIn('gold_per_run.json', meta['inputs_provenance']['gold_sha256'])

    def test_judge_must_not_be_the_model_under_test(self):
        run = make_run(self.base); t = sr.build_template(run)           # tested model is 'mock'
        res = jr.judge_run(run, perfect_client(t), 'MOCK')
        self.assertTrue(res.startswith('REFUSED'), res)
        self.assertFalse((run / sr.REVIEW_JUDGE).exists())

    def test_offline_manual_mode_round_trip(self):
        run = make_run(self.base); t = sr.build_template(run); good = perfect_client(t)
        manual = self.base / 'manual'; client = jr.manual_client(manual)
        replies = 0
        for _ in range(10):                                              # pretend a human pastes each prompt into a chat model
            try:
                res = jr.judge_run(run, client, 'pasted-model', force=True)
                break
            except jr.ManualPending:
                for f in manual.glob('*.prompt.txt'):
                    out = manual / f.name.replace('.prompt.txt', '.response.json')
                    if not out.exists():
                        text = f.read_text(encoding='utf8'); out.write_text(good('', text.split('USER:\n', 1)[1])[0]); replies += 1
        self.assertEqual(res, 'OK'); self.assertEqual(replies, 4)

    def test_categorical_kappa_and_agreement_use_four_level_coverage(self):
        self.assertAlmostEqual(jr.cohen_kappa([('FULL', 'FULL')] * 4 + [('MISSING', 'MISSING')] * 4 + [('PARTIAL', 'PARTIAL')] * 2), 1.0)
        self.assertLess(jr.cohen_kappa([('FULL', 'PARTIAL'), ('PARTIAL', 'FULL'), ('MISSING', 'MISSING'), ('FULL', 'FULL')]), 0.7)
        run = make_run(self.base); t = sr.build_template(run)
        (run / sr.REVIEW).write_text(json.dumps(complete(t)))
        jr.judge_run(run, perfect_client(t, flips=('coverage',)), 'judge-x')
        fam = jr.label_pairs(json.loads((run / sr.REVIEW).read_text()), json.loads((run / sr.REVIEW_JUDGE).read_text()))
        self.assertIn(('FULL', 'PARTIAL'), fam['A1_coverage'])


if __name__ == '__main__':
    unittest.main()
