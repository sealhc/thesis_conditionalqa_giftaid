"""v9.8: a stage that fails with a MODEL format error is re-sampled (same frozen prompt, new seed, nothing edited) up to a cap; every attempt is kept."""
import io, json, shutil, sys, tempfile, unittest, urllib.error
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, ollama_run
from test_stepwise_review import make_run

OPTS = {'seed': 4101, 'num_ctx': 32768, 'num_predict': 8192, 'temperature': .6}
ARGS = lambda n: type('A', (), dict(model='m', world='R3_L', url='u', timeout=1, format_mode='json', max_attempts=n))


class Retries(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.run = make_run(self.tmp)
        self.good = {s: (self.run / f'{s}_parsed.json').read_text() for s in ('A1', 'A2', 'A3', 'B1')}
    def tearDown(self): shutil.rmtree(self.tmp)

    def reset(self, stage):
        for f in list(self.run.glob(f'{stage}_*')): f.unlink()
        (self.run / f'{stage}_PROMPT.txt').write_text('frozen prompt')

    def go(self, stage, replies, n=5):
        self.reset(stage); seeds = []; it = iter(replies)
        def chat(url, model, prompt, options, think, timeout, format_mode='json', schema=None):
            seeds.append(options['seed']); self.assertEqual(prompt, 'frozen prompt')
            r = next(it)
            if isinstance(r, Exception): raise r
            return {}, {'done': True, 'done_reason': 'stop', 'message': {'content': r}, 'prompt_eval_count': 10, 'eval_count': 5}, r
        with mock.patch.object(ollama_run, 'ollama_chat', chat):
            ok = ollama_run.run_stage_with_retries(self.run, stage, ARGS(n), dict(OPTS), False)
        return ok, seeds

    def test_invalid_then_valid_uses_new_seed_and_keeps_the_failed_attempt(self):
        ok, seeds = self.go('A2', ['{"statuses": [', self.good['A2']])
        self.assertTrue(ok); self.assertEqual(seeds, [4101, 5101])
        s = pilot.stage_attempt_summary(self.run, 'A2')
        self.assertEqual([a['valid'] for a in s['attempts']], [False, True]); self.assertFalse(s['first_attempt_valid']); self.assertFalse(s['unfinished_after_retries'])
        self.assertEqual(s['attempts'][0]['reason'], 'MODEL_JSON_INVALID')
        old = self.run / 'attempts' / 'A2_attempt01'
        self.assertEqual((old / 'A2_RECEIVED_RAW_UNPROCESSED.txt').read_text(), '{"statuses": [')           # kept byte for byte, never edited
        self.assertTrue((old / 'A2_record.json').exists()); self.assertTrue((self.run / 'A2_parsed.json').exists())
        self.assertEqual(json.loads((self.run / 'A2_parsed.json').read_text()), json.loads(self.good['A2']))
        u = pilot.stage_usage(self.run, 'A2'); self.assertEqual((u['calls'], u['prompt'], u['generated'], u['complete']), (2, 20, 10, True))

    def test_schema_invalid_type_error_is_retried(self):
        bad = json.loads(self.good['B1']); bad['missing_facts'] = 'a plain string, not a list'
        ok, seeds = self.go('B1', [json.dumps(bad), self.good['B1']])
        self.assertTrue(ok)
        self.assertEqual(pilot.stage_attempt_summary(self.run, 'B1')['attempts'][0]['reason'], 'MODEL_SCHEMA_INVALID')

    def test_cap_reached_stays_failed_and_is_unfinished(self):
        ok, seeds = self.go('A2', ['{"x":'] * 3, n=3)
        self.assertFalse(ok); self.assertEqual(seeds, [4101, 5101, 6101])
        s = pilot.stage_attempt_summary(self.run, 'A2'); self.assertTrue(s['unfinished_after_retries']); self.assertFalse(s['valid'])
        self.assertEqual(len(list((self.run / 'attempts').glob('A2_attempt*'))), 2)                  # two archived + the last failure left in place
        self.assertTrue((self.run / 'A2_record.json').exists()); self.assertFalse((self.run / 'A2_parsed.json').exists())
        res = pilot._path_result(self.run, 'A', 'NEED_MORE_INFORMATION')
        self.assertEqual((res['status'], res['completion_category'], res['stop_stage']), ('no_conclusion', 'model_format_failure', 'A2'))
        self.assertEqual(res['attempts']['unfinished_stage'], 'A2'); self.assertFalse(res['attempts']['first_attempt_all_valid'])

    def test_technical_failures_are_not_retried(self):
        err = urllib.error.HTTPError('u', 404, 'nf', {}, io.BytesIO(b'{"error":"model not found"}'))
        ok, seeds = self.go('A2', [err, self.good['A2']])
        self.assertFalse(ok); self.assertEqual(seeds, [4101])
        s = pilot.stage_attempt_summary(self.run, 'A2'); self.assertEqual(len(s['attempts']), 1); self.assertIn('not_retried', s['attempts'][0]); self.assertFalse(s['unfinished_after_retries'])

    def test_repetition_abort_is_retried(self):
        err = urllib.error.HTTPError('u', 500, 'x', {}, io.BytesIO(b'{"error":"prediction aborted, token repeat limit reached"}'))
        ok, seeds = self.go('A2', [err, self.good['A2']])
        self.assertTrue(ok); self.assertEqual(pilot.stage_attempt_summary(self.run, 'A2')['attempts'][0]['reason'], 'MODEL_DEGENERATE_REPETITION')
        u = pilot.stage_usage(self.run, 'A2'); self.assertEqual(u['calls'], 2); self.assertFalse(u['complete'])      # the aborted call has no token counts: never guessed

    def test_max_attempts_one_is_the_old_behaviour(self):
        ok, seeds = self.go('A2', ['{"x":', self.good['A2']], n=1)
        self.assertFalse(ok); self.assertEqual(seeds, [4101]); self.assertFalse((self.run / 'attempts').exists())

    def test_first_attempt_statistics(self):
        self.go('B1', [self.good['B1']])
        self.assertTrue(pilot.path_attempts(self.run, ('B1',))['first_attempt_all_valid'])
        self.go('B1', ['{"x":', self.good['B1']])
        a = pilot.path_attempts(self.run, ('B1',)); self.assertFalse(a['first_attempt_all_valid']); self.assertEqual((a['total_model_calls'], a['retry_calls']), (2, 1))
        rep = pilot.audit_run(self.run, save=False)
        st = pilot._stats_for_path([rep['paths']['B']])
        self.assertEqual((st['completed'], st['first_attempt_all_valid'], st['unfinished_after_retries'], st['retry_calls_total']), (1, 0, 0, 1))

    def test_api_runner_uses_the_same_retry_loop_and_seedless_providers_keep_none(self):
        import api_run
        for seed, want in ((4101, [4101, 5101]), (None, [None, None])):
            self.reset('A2'); shutil.rmtree(self.run / 'attempts', ignore_errors=True); seen = []
            def fake(run, stage, cfg, args, s):
                seen.append(s)
                if len(seen) == 1:
                    pilot._write_stage_event(run, stage, status='model_format_failure', reason='MODEL_JSON_INVALID', detail='x', raw='{'); return False
                pilot.write(run / f'{stage}_record.json', json.dumps({'valid_json': True, 'status': 'parsed', 'diagnostic_flags': []})); pilot.write(run / f'{stage}_parsed.json', '{}'); return True
            with mock.patch.object(api_run, 'run_stage', fake):
                self.assertTrue(api_run.run_stage_with_retries(self.run, 'A2', {}, type('A', (), {'max_attempts': 3}), seed))
            self.assertEqual(seen, want)

    def test_seed_rule(self):
        self.assertEqual([pilot.retry_seed(4101, k) for k in (1, 2, 3)], [4101, 5101, 6101]); self.assertIsNone(pilot.retry_seed(None, 3))


if __name__ == '__main__':
    unittest.main()
