import copy, json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pilot, stepwise_review as sr

IDS = ['G_C1', 'G_C2', 'G_C3', 'G_X1', 'G_X2', 'G_X3', 'G_X4', 'G_X5']
REFSTAT = {'G_C1': 'TRUE', 'G_C2': 'UNKNOWN', 'G_C3': 'UNKNOWN', 'G_X1': 'FALSE', 'G_X2': 'FALSE', 'G_X3': 'FALSE', 'G_X4': 'FALSE', 'G_X5': 'FALSE'}


def put(run, name, obj):
    (run / name).write_text(json.dumps(obj), encoding='utf8')


def make_run(base, world='R3_L', run_id='t_rep01', a2_wrong=(), b_missing=None, a_missing=None, with_a=True):
    run = Path(base) / run_id / world
    run.mkdir(parents=True)
    put(run, 'run_metadata.json', {'world': world, 'run_id': run_id, 'model': 'mock', 'inference_settings': json.dumps({'options': {'seed': 1, 'temperature': 0.6}, 'think': False})})
    stages = {}
    if with_a:
        stages['A1'] = {'decision_target': 't', 'decision_unit': 'u', 'scope_issues': [], 'limitations': [],
                        'atoms': [{'id': f'a{i}', 'proposition': f'prop {g}', 'entity_scope': 'receipt', 'source_ids': ['E01']} for i, g in enumerate(IDS, 1)],
                        'eligibility_expression': {'op': 'ALL', 'args': [{'ref': f'a{i}'} for i in range(1, 9)]}}
        stages['A2'] = {'statuses': [{'id': f'a{i}', 'status': ('TRUE' if g in a2_wrong else REFSTAT[g]), 'evidence_quote': '' if REFSTAT[g] == 'UNKNOWN' else 'quote', 'justification': 'j'} for i, g in enumerate(IDS, 1)],
                        'scope_issues': []}
        stages['A3'] = {'decision': 'NEED_MORE_INFORMATION', 'supporting_policy_ids': ['E01'], 'decisive_condition_ids': ['a2', 'a3'], 'unresolved_condition_ids': ['a2', 'a3'],
                        'missing_facts': a_missing if a_missing is not None else ["Devon's 2025-26 income tax paid", 'whether the declaration covers this gift'],
                        'justification': 'j', 'scope_issues': [], 'ir_discrepancies': [], 'conditional_branches': []}
    stages['B1'] = {'decision': 'NEED_MORE_INFORMATION', 'supporting_policy_ids': ['E04'], 'missing_facts': b_missing if b_missing is not None else ['the donor name'],
                    'justification': 'j', 'scope_issues': [], 'conditional_branches': []}
    for st, obj in stages.items():
        put(run, f'{st}_parsed.json', obj)
        put(run, f'{st}_record.json', {'valid_json': True, 'status': 'parsed', 'diagnostic_flags': []})
    return run


def complete(template, a1_match=None, a2_correct=True):
    r = copy.deepcopy(template)
    r['reviewer'] = 'tester'
    a1_match = IDS if a1_match is None else a1_match
    for i, c in enumerate(r['A1']['reference_conditions'], 1):
        ok = c['gold_condition_id'] in a1_match
        c['coverage'] = 'FULL' if ok else 'MISSING'
        c['matched_A1_atom_ids'] = [f'a{IDS.index(c["gold_condition_id"]) + 1}'] if ok else []
    covered = {i for c in r['A1']['reference_conditions'] for i in c['matched_A1_atom_ids']}
    for a in r['A1']['model_atoms']: a['atom_assessment'] = 'MATCHES_REFERENCE' if a['atom_id'] in covered else 'SUPPORTED_ADDITIONAL'
    r['A1']['logic']['logic_assessment'] = 'EQUIVALENT'; r['A1']['logic']['scope_assessment'] = 'MATCH'
    quotes = {x['atom_id']: x['evidence_quote'] for x in r['A2']['model_rows']}
    for it in r['A2']['items']:
        if it['gold_condition_id'] in a1_match:
            it['status_correct'] = a2_correct
            aid = f'a{IDS.index(it["gold_condition_id"]) + 1}'
            it['quote_supports_status'] = True if quotes[aid].strip() else None
    for out in r['final_outputs']:
        for mf in out['model_missing_facts']:
            mf['matches_reference_fact_ids'] = []; mf['unnecessary'] = True
        out['reasoning'].update(decisive_conditions_correct=True, asserts_unsupported_fact=False, scope_correct=True)
        if out['conditional_answer']['applicable']:
            out['conditional_answer'].update(branches_match_reference='FULL', asserts_unsupported_condition=False)
    return r


class Stepwise(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
    def tearDown(self): self.tmp.cleanup()

    def test_template_is_blinded_and_pending_until_reviewed(self):
        run = make_run(self.base)
        self.assertEqual(sr.review_status(run)['status'], 'PENDING_MANUAL_REVIEW')
        t = sr.build_template(run)
        self.assertEqual([o['label'] for o in t['final_outputs']], ['F1', 'F2'])
        self.assertNotIn('pipeline', json.dumps(t['final_outputs']))
        key = json.loads((run / sr.KEY).read_text())
        self.assertEqual({v['pipeline'] for v in key.values()}, {'A', 'B'})
        status, _ = sr.validate_review(t, t)
        self.assertEqual(status, 'INCOMPLETE_REVIEW')       # reviewer empty / unanswered

    def test_complete_review_scores_from_human_judgements(self):
        run = make_run(self.base, a2_wrong=('G_X1',))
        t = sr.build_template(run); r = complete(t)
        r['A2']['items'][3]['status_correct'] = False        # human agrees G_X1 status is wrong
        self.assertEqual(sr.validate_review(t, r), (None, None))
        s = sr.compute_score(run, t, r)
        self.assertEqual(s['A1']['reference_condition_recall'], {'numerator': 8, 'denominator': 8, 'rate': 1.0})
        self.assertEqual(s['A2']['status_semantic_accuracy_on_matched_conditions']['numerator'], 7)
        self.assertEqual(s['A2']['mechanical_proxy_exact_status_equality']['numerator'], 7)
        self.assertEqual(s['A2']['reviewer_vs_mechanical_disagreements'], [])
        for pipe in ('A', 'B'):
            self.assertTrue(s['final_outputs'][pipe]['correct_for_right_reasons'])
        self.assertEqual(s['final_outputs']['A']['missing_information']['listed_facts_unnecessary'], 2)   # complete() marks all listed facts unnecessary
        self.assertEqual(s['final_outputs']['B']['missing_information']['listed_facts_unnecessary'], 1)
        self.assertEqual(s['final_outputs']['A']['missing_information']['reference_fact_recall'], {'numerator': 0, 'denominator': 2, 'rate': 0.0})

    def test_missing_fact_matching_recall_and_unmatched_conditions(self):
        run = make_run(self.base)
        t = sr.build_template(run); r = complete(t, a1_match=[g for g in IDS if g != 'G_C3'])
        key = json.loads((run / sr.KEY).read_text())
        a_label = [l for l, v in key.items() if v['pipeline'] == 'A'][0]
        out = [o for o in r['final_outputs'] if o['label'] == a_label][0]
        out['model_missing_facts'][0].update(matches_reference_fact_ids=['G_C2'], unnecessary=None)
        out['model_missing_facts'][1].update(matches_reference_fact_ids=['G_C3'], unnecessary=None)
        self.assertEqual(sr.validate_review(t, r), (None, None))
        s = sr.compute_score(run, t, r)
        self.assertEqual(s['A1']['unmatched_reference_conditions'], ['G_C3'])
        self.assertEqual(s['A2']['not_scorable_because_A1_missed_condition'], 1)
        self.assertEqual(s['final_outputs']['A']['missing_information']['reference_fact_recall'], {'numerator': 2, 'denominator': 2, 'rate': 1.0})
        self.assertEqual(s['final_outputs']['B']['missing_information']['reference_fact_recall']['numerator'], 0)

    def test_human_and_mechanical_disagreement_is_reported_not_hidden(self):
        run = make_run(self.base, a2_wrong=('G_X2',))
        t = sr.build_template(run); r = complete(t)            # reviewer (wrongly) says everything correct
        s = sr.compute_score(run, t, r)
        self.assertEqual([d['gold_condition_id'] for d in s['A2']['reviewer_vs_mechanical_disagreements']], ['G_X2'])
        self.assertEqual(s['A2']['status_semantic_accuracy_on_matched_conditions']['numerator'], 8)   # the HUMAN judgement is what is scored
        self.assertEqual(s['A2']['mechanical_proxy_exact_status_equality']['numerator'], 7)

    def test_tampering_and_inconsistency_are_rejected(self):
        run = make_run(self.base)
        t = sr.build_template(run)
        for mutate, expect in (
            (lambda r: r['A1']['reference_conditions'][0].update(proposition='changed'), 'INVALID_REVIEW'),
            (lambda r: r['final_outputs'][0].update(justification='edited model output'), 'INVALID_REVIEW'),
            (lambda r: r['A2']['model_rows'][0].update(status='FALSE'), 'INVALID_REVIEW'),
            (lambda r: r['A1']['reference_conditions'][1].update(matched_A1_atom_ids=[]), 'INVALID_REVIEW'),   # G_C2: true match with no atom
            (lambda r: r['A1']['reference_conditions'][0].update(matched_A1_atom_ids=['zz']), 'INVALID_REVIEW'),
            (lambda r: r['A1']['reference_conditions'][0].update(coverage=None), 'INCOMPLETE_REVIEW'),
            (lambda r: r.update(reviewer=''), 'INCOMPLETE_REVIEW'),
            (lambda r: r['final_outputs'][0]['reasoning'].update(scope_correct=None), 'INCOMPLETE_REVIEW'),
            (lambda r: r['final_outputs'][0]['model_missing_facts'][0].update(matches_reference_fact_ids=['G_C2'], unnecessary=True), 'INVALID_REVIEW')):
            r = complete(t); mutate(r)
            self.assertEqual(sr.validate_review(t, r)[0], expect)
        r = complete(t)                                          # 'MISSING' that still lists an atom (A2 left consistent)
        r['A1']['reference_conditions'][1]['coverage'] = 'MISSING'
        r['A2']['items'][1].update(status_correct=None, quote_supports_status=None)
        self.assertEqual(sr.validate_review(t, r)[0], 'INVALID_REVIEW')
        r = complete(t, a1_match=[g for g in IDS if g != 'G_C1'])
        r['A2']['items'][0]['status_correct'] = True            # A2 judged on a condition A1 never matched
        self.assertEqual(sr.validate_review(t, r)[0], 'INVALID_REVIEW')

    def test_b_only_run_and_r0_and_wrong_decision(self):
        run = make_run(self.base, run_id='b_only', with_a=False)
        t = sr.build_template(run)
        self.assertIsNone(t['A1']); self.assertEqual(len(t['final_outputs']), 1)
        r0 = Path(self.base) / 'x' / 'R0'; r0.mkdir(parents=True)
        put(r0, 'run_metadata.json', {'world': 'R0', 'run_id': 'x', 'model': 'm'})
        with self.assertRaises(ValueError): sr.build_template(r0)
        self.assertEqual(sr.review_status(r0)['status'], 'NOT_APPLICABLE')

    def test_summary_counts_pending_and_never_imputes(self):
        done = make_run(self.base, run_id='r_done'); pend = make_run(self.base, run_id='r_pend')
        t = sr.build_template(done); r = complete(t)
        (done / sr.REVIEW).write_text(json.dumps(r)); sr.score_cmd(type('A', (), {'run': str(done)}))
        sr.build_template(pend)
        out = Path(self.base) / 'sum.json'
        sr.summary_cmd(type('A', (), {'runs_root': str(self.base), 'output': str(out)}))
        g = json.loads(out.read_text())['groups'][0]
        self.assertEqual((g['runs_total'], g['human_confirmed']['runs'], g['pending_no_label_at_all']), (2, 1, 1))
        self.assertEqual(g['human_confirmed']['metrics']['A1_reference_condition_recall']['denominator'], 8)
        self.assertEqual(g['llm_judge_labelled']['runs'], 0)
        self.assertIn('UNCALIBRATED', g['llm_judge_labelled']['status'])


if __name__ == '__main__':
    unittest.main()
