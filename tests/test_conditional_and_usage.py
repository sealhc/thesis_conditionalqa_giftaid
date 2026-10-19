"""v9.7: conditional-answer judging only when the model answered NEED_MORE_INFORMATION; branch-consistency flags; calls/tokens per path."""
import copy, json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, stepwise_review as sr, judge_review as jr
from test_stepwise_review import make_run, complete, put
from test_judge_review import perfect_client


def decided_b1(run, decision='YES'):
    b = json.loads((run / 'B1_parsed.json').read_text()); b.update(decision=decision, missing_facts=[]); put(run, 'B1_parsed.json', b)


class ConditionalAnswer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.run = make_run(self.base); decided_b1(self.run)          # A3 = NEED_MORE_INFORMATION, B1 = YES
        self.t = sr.build_template(self.run)
        self.by = {o['conditional_answer']['applicable']: o for o in self.t['final_outputs']}
    def tearDown(self): self.tmp.cleanup()

    def test_applicable_only_for_need_more_information(self):
        self.assertEqual(sorted(self.by), [False, True])
        self.assertEqual(self.by[True]['decision'], 'NEED_MORE_INFORMATION'); self.assertEqual(self.by[False]['decision'], 'YES')

    def test_validator_rules(self):
        base = complete(self.t); self.assertEqual(sr.validate_review(self.t, base), (None, None))
        def ca(r, applicable): return next(o for o in r['final_outputs'] if o['conditional_answer']['applicable'] is applicable)['conditional_answer']
        cases = ((lambda r: ca(r, True).update(branches_match_reference=None), 'INCOMPLETE_REVIEW'),
                 (lambda r: ca(r, True).update(asserts_unsupported_condition=None), 'INCOMPLETE_REVIEW'),
                 (lambda r: ca(r, True).update(branches_match_reference='GOOD'), 'INVALID_REVIEW'),
                 (lambda r: ca(r, False).update(branches_match_reference='FULL'), 'INVALID_REVIEW'),        # decided answers must stay unscored
                 (lambda r: ca(r, False).update(asserts_unsupported_condition=False), 'INVALID_REVIEW'),
                 (lambda r: ca(r, True).update(applicable=False), 'INVALID_REVIEW'))                      # frozen field
        for mutate, want in cases:
            r = copy.deepcopy(base); mutate(r); self.assertEqual(sr.validate_review(self.t, r)[0], want)
        r = copy.deepcopy(base); r.update(confidence='HIGH', uncertain_cases=[])
        for a in r['A1']['reference_conditions']: a['rationale'] = 'r'
        for a in r['A1']['model_atoms']:
            if a['atom_assessment'] != 'MATCHES_REFERENCE': a['rationale'] = 'r'
        for i in r['A2']['items']: i['rationale'] = 'r'
        for o in r['final_outputs']: o['reasoning']['rationale'] = 'r'
        self.assertEqual(sr.validate_review(self.t, r, strict_judge=True)[0], 'INVALID_REVIEW')   # applicable conditional rationale still empty
        next(o for o in r['final_outputs'] if o['conditional_answer']['applicable'])['conditional_answer']['rationale'] = 'r'
        self.assertEqual(sr.validate_review(self.t, r, strict_judge=True), (None, None))

    def test_score_reports_scored_and_unscored(self):
        r = complete(self.t); next(o for o in r['final_outputs'] if o['conditional_answer']['applicable'])['conditional_answer'].update(branches_match_reference='PARTIAL')
        s = sr.compute_score(self.run, self.t, r)['final_outputs']
        self.assertEqual(s['A']['conditional_answer'], {'applicable': True, 'branches_match_reference': 'PARTIAL', 'asserts_unsupported_condition': False})
        self.assertFalse(s['B']['conditional_answer']['applicable']); self.assertIn('Not scored', s['B']['conditional_answer']['note'])

    def judge(self, reply):
        good = perfect_client(self.t)
        def call(system, prompt):
            text, meta = good(system, prompt)
            if 'TASK: Final-answer check' in prompt and '"conditional_answer_applicable": true' in prompt:
                o = json.loads(text); reply(o); text = json.dumps(o)
            return text, meta
        (self.run / sr.REVIEW_JUDGE).unlink(missing_ok=True)
        return jr.judge_run(self.run, call, 'judge-x', force=True)

    def test_judge_must_answer_when_applicable_and_is_ignored_when_not(self):
        self.assertTrue(self.judge(lambda o: o.pop('conditional_answer')).startswith('REJECTED'))
        self.assertTrue(self.judge(lambda o: o['conditional_answer'].update(branches_match_reference='GREAT')).startswith('REJECTED'))
        self.assertTrue(self.judge(lambda o: o['conditional_answer'].update(asserts_unsupported_condition='no')).startswith('REJECTED'))
        self.assertEqual(self.judge(lambda o: o['conditional_answer'].update(branches_match_reference='NONE', asserts_unsupported_condition=True)), 'OK')
        j = json.loads((self.run / sr.REVIEW_JUDGE).read_text()); ca = {o['conditional_answer']['applicable']: o['conditional_answer'] for o in j['final_outputs']}
        self.assertEqual((ca[True]['branches_match_reference'], ca[True]['asserts_unsupported_condition']), ('NONE', True))
        self.assertEqual((ca[False]['branches_match_reference'], ca[False]['asserts_unsupported_condition']), (None, None))   # fake judge answered anyway: ignored

    def test_judge_prompt_shows_branches_and_applicability(self):
        prompts = []
        good = perfect_client(self.t)
        def spy(system, prompt): prompts.append(prompt); return good(system, prompt)
        jr.judge_run(self.run, spy, 'judge-x', force=True)
        final = [p for p in prompts if 'TASK: Final-answer check' in p]
        self.assertEqual(sum('"conditional_answer_applicable": true' in p for p in final), 1); self.assertEqual(sum('"conditional_answer_applicable": false' in p for p in final), 1)


class BranchFlags(unittest.TestCase):
    def flags(self, decision, branches):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        run = make_run(tmp.name)
        obj = {'decision': decision, 'supporting_policy_ids': ['E01'], 'missing_facts': ['x'] if decision == 'NEED_MORE_INFORMATION' else [], 'justification': 'j',
               'scope_issues': [], 'conditional_branches': branches}
        return {f['code'] for f in pilot.diagnostic_flags('B1', obj, run, 'R3_L')}
    def test_flags(self):
        b = [{'possible_answer': 'YES', 'conditions_in_words': 'if paid tax'}]
        self.assertIn('NEI_WITHOUT_CONDITIONAL_BRANCHES', self.flags('NEED_MORE_INFORMATION', []))
        self.assertNotIn('NEI_WITHOUT_CONDITIONAL_BRANCHES', self.flags('NEED_MORE_INFORMATION', b))
        self.assertIn('DECIDED_WITH_CONDITIONAL_BRANCHES', self.flags('YES', b))
        self.assertNotIn('DECIDED_WITH_CONDITIONAL_BRANCHES', self.flags('YES', []))


class Usage(unittest.TestCase):
    def setUp(self): self.tmp = tempfile.TemporaryDirectory(); self.run = make_run(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_ollama_and_api_counts_and_incomplete_data_is_never_summed(self):
        for s, (p, g) in {'A1': (100, 20), 'A2': (150, 30), 'A3': (200, 40), 'B1': (90, 10)}.items():
            put(self.run, f'{s}_ollama_metrics.json', {'prompt_eval_count': p, 'eval_count': g})
        a = pilot.path_usage(self.run, ('A1', 'A2', 'A3')); b = pilot.path_usage(self.run, ('B1',))
        self.assertEqual((a['calls'], a['prompt_tokens'], a['generated_tokens'], a['usage_complete']), (3, 450, 90, True))
        self.assertEqual((b['calls'], b['prompt_tokens'], b['generated_tokens']), (1, 90, 10))
        put(self.run, 'A2_ollama_metrics.json', {'prompt_eval_count': None, 'eval_count': 30})
        a = pilot.path_usage(self.run, ('A1', 'A2', 'A3')); self.assertFalse(a['usage_complete']); self.assertIsNone(a['prompt_tokens']); self.assertEqual(a['calls'], 3)
        (self.run / 'A2_ollama_metrics.json').unlink(); put(self.run, 'A2_api_metrics.json', {'usage': {'input_tokens': 7, 'output_tokens': 3}, 'attempts': 2})
        a = pilot.path_usage(self.run, ('A1', 'A2', 'A3')); self.assertEqual((a['prompt_tokens'], a['generated_tokens'], a['attempts']), (307, 63, 4))
        self.assertEqual(pilot.stage_usage(self.run, 'A3')['called'], True); self.assertEqual(pilot.path_usage(self.run, ('A1',))['calls'], 1)

    def test_audit_and_cohort_summary_carry_usage(self):
        for s in ('A1', 'A2', 'A3', 'B1'): put(self.run, f'{s}_ollama_metrics.json', {'prompt_eval_count': 10, 'eval_count': 5})
        rep = pilot.audit_run(self.run, save=False)
        self.assertEqual(rep['paths']['A']['usage']['calls'], 3); self.assertEqual(rep['paths']['B']['usage']['calls'], 1)
        st = pilot._stats_for_path([rep['paths']['A'], rep['paths']['B']])['usage']
        self.assertEqual((st['total_calls'], st['mean_total_tokens_per_run']), (4, 30.0))
        self.assertEqual(pilot._stats_for_path([rep['paths']['A']])['repetition_loop_aborts'], 0)


if __name__ == '__main__':
    unittest.main()
