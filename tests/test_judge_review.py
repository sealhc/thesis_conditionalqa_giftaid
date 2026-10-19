import copy, json, re, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, stepwise_review as sr, judge_review as jr
from test_stepwise_review import make_run, complete, IDS


def perfect_client(template, flips=()):
    """A fake judge that answers exactly like the 'complete()' human review (so agreement is perfect),
    except for the keys in `flips`, where it gives a different answer."""
    human = complete(template)
    calls = []
    def call(system, prompt):
        calls.append(prompt)
        if 'TASK: A1 mapping' in prompt:
            ref = human['A1']
            return json.dumps({'reference_checks': [{'gold_id': c['gold_condition_id'], 'coverage': ('PARTIAL' if ('coverage' in flips and c['gold_condition_id'] == 'G_C1') else c['coverage']),
                                                       'model_atom_ids': c['matched_A1_atom_ids'], 'rationale': 'r'} for c in ref['reference_conditions']],
                               'extra_atoms': [{'model_atom_id': a['atom_id'], 'assessment': a['atom_assessment'], 'rationale': 'r'} for a in ref['model_atoms'] if a['atom_assessment'] != 'MATCHES_REFERENCE'],
                               'logic': {'assessment': 'EQUIVALENT', 'issues': []}, 'scope': {'assessment': 'MATCH', 'issues': []},
                               'uncertain_cases': [], 'confidence': 'HIGH'}), {'model': 'fake-judge'}
        if 'TASK: A2 status check' in prompt:
            return json.dumps({'items': [{'gold_condition_id': i['gold_condition_id'], 'status_correct': ('status' not in flips), 'quote_supports_status': True, 'reason': 'r'}
                                         for i in human['A2']['items'] if i['status_correct'] is not None],
                               'uncertain_cases': [], 'confidence': 'HIGH'}), {'model': 'fake-judge'}
        if 'TASK: Final-answer check' in prompt:
            n = len(re.search(r'"listed_missing_facts": (\[.*?\])\s*\}', prompt, re.S) and json.loads(re.search(r'"listed_missing_facts": (\[.*?\])\s*\}', prompt, re.S).group(1)))
            return json.dumps({'missing_facts': [{'index': i, 'matches_reference_fact_ids': [], 'unnecessary': True} for i in range(n)],
                               'reasoning': {'decisive_conditions_correct': True, 'asserts_unsupported_fact': False, 'scope_correct': True, 'reason': 'r'},
                               'conditional_answer': {'branches_match_reference': 'FULL', 'asserts_unsupported_condition': False, 'reason': 'r'},
                               'uncertain_cases': [], 'confidence': 'HIGH'}), {'model': 'fake-judge'}
        raise AssertionError(prompt[:80])
    call.calls = calls
    return call


class Judge(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_judge_writes_valid_separate_file_and_never_touches_human_files(self):
        run = make_run(self.base)
        t = sr.build_template(run)
        self.assertEqual(jr.judge_run(run, perfect_client(t), 'judge-x'), 'OK')
        self.assertTrue((run / sr.REVIEW_JUDGE).exists())
        self.assertFalse((run / sr.REVIEW).exists())
        review = json.loads((run / sr.REVIEW_JUDGE).read_text())
        self.assertEqual(review['reviewer'], 'LLM:judge-x')
        self.assertEqual(sr.validate_review(t, review), (None, None))
        self.assertEqual(sr.review_status(run)['status'], 'PENDING_MANUAL_REVIEW')   # judge file alone is not a score
        sr.score_cmd(type('A', (), {'run': str(run), 'source': 'judge'}))
        s = json.loads((run / sr.SCORE_JUDGE).read_text())
        self.assertEqual(s['status'], 'LLM_JUDGE_LABELLED')
        self.assertFalse((run / sr.SCORE).exists())
        self.assertEqual(sr.review_status(run)['status'], 'LLM_JUDGE_LABELLED_NOT_HUMAN_CONFIRMED')
        self.assertEqual(jr.judge_run(run, perfect_client(t), 'judge-x'), 'SKIPPED_EXISTS')

    def test_prompts_are_blind_to_model_pipeline_and_run(self):
        run = make_run(self.base, run_id='secret_run_id')
        t = sr.build_template(run)
        client = perfect_client(t); jr.judge_run(run, client, 'judge-x')
        self.assertGreaterEqual(len(client.calls), 4)
        for prompt in client.calls:
            for forbidden in ('secret_run_id', 'mock', 'pipeline', 'A3', 'B1', 'Pipeline', 'STEPWISE_BLIND_KEY'):
                self.assertNotIn(forbidden, prompt, forbidden)
        self.assertTrue(list((run / 'judge_logs').glob('*_prompt.txt')))   # everything is logged

    def test_malformed_or_incomplete_judge_output_is_rejected_not_repaired(self):
        run = make_run(self.base)
        t = sr.build_template(run); good = perfect_client(t)
        def drops_a_condition(system, prompt):
            text, meta = good(system, prompt)
            if 'TASK: A1 mapping' in prompt:
                o = json.loads(text); o['reference_checks'] = o['reference_checks'][1:]; text = json.dumps(o)
            return text, meta
        res = jr.judge_run(run, drops_a_condition, 'judge-x')
        self.assertTrue(res.startswith('REJECTED'))
        self.assertFalse((run / sr.REVIEW_JUDGE).exists())
        self.assertTrue((run / 'STEPWISE_REVIEW_JUDGE_REJECTED.json').exists())
        run2 = make_run(self.base, run_id='t_rep02'); n = []
        def garbage(system, prompt): n.append(1); return 'not json at all', {}
        self.assertTrue(jr.judge_run(run2, garbage, 'judge-x').startswith('REJECTED'))
        self.assertEqual(len(n), 2)                           # exactly one retry
        run3 = make_run(self.base, run_id='t_rep03')
        def non_bool(system, prompt):
            text, meta = good(system, prompt)
            if 'TASK: Final' in prompt:
                o = json.loads(text); o['reasoning']['scope_correct'] = 'yes'; text = json.dumps(o)
            return text, meta
        self.assertTrue(jr.judge_run(run3, non_bool, 'judge-x').startswith('REJECTED'))

    def test_output_that_passes_filling_but_fails_the_validator_is_rejected(self):
        run = make_run(self.base)
        t = sr.build_template(run); good = perfect_client(t)
        def unknown_atom(system, prompt):
            text, meta = good(system, prompt)
            if 'TASK: A1 mapping' in prompt:
                o = json.loads(text); o['reference_checks'][0]['model_atom_ids'] = ['zz']; text = json.dumps(o)
            return text, meta
        res = jr.judge_run(run, unknown_atom, 'judge-x')
        self.assertTrue(res.startswith('REJECTED'), res)
        self.assertFalse((run / sr.REVIEW_JUDGE).exists())

    def _calibrate(self, flips=(), runs=3):
        out = []
        for i in range(runs):
            run = make_run(self.base, run_id=f'cal{i}')
            t = sr.build_template(run)
            (run / sr.REVIEW).write_text(json.dumps(complete(t)))
            self.assertEqual(jr.judge_run(run, perfect_client(t, flips), 'judge-x'), 'OK')
            out.append(run)
        return out

    def _agree(self, min_minority, min_pairs=5):
        with mock.patch.object(sr, 'REPORTS_DIR', self.base / 'reports'):
            jr.agreement_cmd(type('A', (), {'runs_root': str(self.base), 'min_kappa': 0.7, 'min_pairs': min_pairs, 'min_minority': min_minority}))
            return json.loads((self.base / 'reports' / sr.AGREEMENT).read_text())

    def test_agreement_cmd_reads_runs_and_never_passes_constant_labels_even_with_minority_check_off(self):
        self._calibrate()
        for mm in (3, 0):                                     # 0 = the old escape hatch: must NOT turn constant labels into a pass
            rep = self._agree(mm)
            self.assertFalse(rep['judge_acceptable'], mm)
            self.assertEqual(len(rep['calibration_runs']), 3)
            self.assertEqual(rep['families']['A2_status_correct']['verdict'], 'INSUFFICIENT_VARIATION')

    def test_disagreement_or_too_few_pairs_blocks_acceptance(self):
        varied = {'f': [(True, False)] * 6 + [(False, True)] * 6 + [(True, True)] * 3 + [(False, False)] * 3}
        rep = jr.agreement_report(varied, 0.7, 10, ['r'])
        self.assertEqual(rep['families']['f']['verdict'], 'FAIL'); self.assertFalse(rep['judge_acceptable'])
        rep = jr.agreement_report({'f': [(True, True)] * 10 + [(False, False)] * 5}, 0.7, 10 ** 6, ['r'])
        self.assertEqual(rep['families']['f']['verdict'], 'INSUFFICIENT_PAIRS'); self.assertFalse(rep['judge_acceptable'])
        rep = jr.agreement_report({'f': [(True, True)] * 15}, 0.7, 10, ['r'], min_minority=0)
        self.assertEqual(rep['families']['f']['verdict'], 'INSUFFICIENT_VARIATION')

    def test_variation_with_agreement_passes(self):
        pairs = {'f': [(True, True)] * 10 + [(False, False)] * 5}
        self.assertTrue(jr.agreement_report(pairs, 0.7, 10, ['r'])['judge_acceptable'])
        pairs = {'f': [(True, True)] * 14 + [(False, False)] * 2}
        self.assertEqual(jr.agreement_report(pairs, 0.7, 10, ['r'])['families']['f']['verdict'], 'INSUFFICIENT_VARIATION')

    def test_kappa(self):
        self.assertAlmostEqual(jr.cohen_kappa([(True, True)] * 5 + [(False, False)] * 5), 1.0)
        self.assertAlmostEqual(jr.cohen_kappa([(True, True), (False, False), (True, False), (False, True)]), 0.0)
        self.assertIsNone(jr.cohen_kappa([(True, True)] * 4))        # undefined: no variation

    def test_summary_marks_judge_numbers_uncalibrated_until_agreement_passes(self):
        runs = self._calibrate()
        out = self.base / 'sum.json'
        for run in runs: sr.score_cmd(type('A', (), {'run': str(run), 'source': 'judge'}))
        with mock.patch.object(sr, 'REPORTS_DIR', self.base / 'reports'):
            sr.summary_cmd(type('A', (), {'runs_root': str(self.base), 'output': str(out)}))
            g = json.loads(out.read_text())['groups'][0]
            self.assertIn('UNCALIBRATED', g['llm_judge_labelled']['status'])
            self.assertEqual(g['llm_judge_labelled']['runs'], 3); self.assertEqual(g['human_confirmed']['runs'], 0)
            self._agree(3)                                    # constant labels -> not acceptable -> still UNCALIBRATED
            sr.summary_cmd(type('A', (), {'runs_root': str(self.base), 'output': str(out)}))
            self.assertIn('UNCALIBRATED', json.loads(out.read_text())['groups'][0]['llm_judge_labelled']['status'])
            good = jr.agreement_report({'f': [(True, True)] * 10 + [(False, False)] * 5}, 0.7, 10, ['r'])   # a genuinely passing report
            (self.base / 'reports' / sr.AGREEMENT).write_text(json.dumps(good))
            sr.summary_cmd(type('A', (), {'runs_root': str(self.base), 'output': str(out)}))
            self.assertIn('CALIBRATED', json.loads(out.read_text())['groups'][0]['llm_judge_labelled']['status'])

    def test_sample_is_stratified_and_deterministic(self):
        for w in ('R3_L', 'R3_H'):
            for i in range(3): make_run(self.base, world=w, run_id=f'x{i}')
        a = jr.sample_runs(self.base, 4, 1); b = jr.sample_runs(self.base, 4, 1)
        self.assertEqual(a, b)
        self.assertEqual({r.name for r in a}, {'R3_L', 'R3_H'})
        self.assertEqual(sum(r.name == 'R3_L' for r in a), 2)


if __name__ == '__main__':
    unittest.main()
