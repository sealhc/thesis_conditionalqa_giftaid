import json, sys, tempfile, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot
from test_stepwise_review import make_run


class R0Separation(unittest.TestCase):
    def test_r0_is_reported_separately_and_never_pooled_with_synthetic(self):
        with tempfile.TemporaryDirectory() as d:
            make_run(d, world='R0', run_id='rep01')
            make_run(d, world='R3_L', run_id='rep01')
            make_run(d, world='R3_H', run_id='rep01')
            summary = pilot.summarize_runs(d)
            self.assertEqual(len(summary['cohorts']), 1)
            c = next(iter(summary['cohorts'].values()))
            # synthetic statistics contain only R3_L and R3_H
            self.assertEqual(c['path_A']['planned_or_prepared'], 2)
            self.assertEqual(c['path_B']['planned_or_prepared'], 2)
            self.assertEqual(c['paired_quality']['n'], 2)
            self.assertEqual(c['path_A']['answer_evaluated'], 2)
            # R0 has its own block with its own review status; no YES/NO/NEI accuracy is computed for it
            r0 = c['R0_official']
            self.assertEqual(r0['runs'], 1)
            self.assertEqual(r0['native_semantic_review']['manually_reviewed_runs'], 0)
            self.assertEqual(r0['native_semantic_review']['pending_or_incomplete_runs'], 1)
            self.assertIsNone(r0['native_semantic_review']['pooled_official_condition_recall'])
            self.assertEqual(r0['path_A']['answer_evaluated'], 0)
            self.assertIn('Never merge', r0['reporting_rule'])
            self.assertIn('SYNTHETIC', c['scope_of_path_and_paired_statistics'])
            self.assertIn('R0_vs_synthetic', summary['definitions'])

    def test_synthetic_audit_says_stepwise_is_unscored(self):
        with tempfile.TemporaryDirectory() as d:
            run = make_run(d, world='R3_L', run_id='rep01')
            report = pilot.audit_run(run, save=False)
            self.assertEqual(report['stepwise_semantic_review']['status'], 'PENDING_MANUAL_REVIEW')


if __name__ == '__main__':
    unittest.main()
