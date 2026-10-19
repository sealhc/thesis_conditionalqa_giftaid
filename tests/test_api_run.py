import json, shutil, sys, tempfile, unittest, urllib.error
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, api_run, stage_schemas
from test_stepwise_review import make_run

ANTH = lambda text, stop='end_turn', model='claude-test-1': {'id': 'm1', 'type': 'message', 'model': model, 'stop_reason': stop, 'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 5, 'output_tokens': 7}}
OAI = lambda text, stop='stop', model='gpt-test-1': {'id': 'c1', 'model': model, 'choices': [{'finish_reason': stop, 'message': {'role': 'assistant', 'content': text}}], 'usage': {'prompt_tokens': 5, 'completion_tokens': 7}}


def args(provider='anthropic', **kw):
    d = dict(provider=provider, model='m-1', temperature=0.6, max_tokens=8192, reasoning_effort=None, timeout=5, retries=2, seed=4101, world='R3_L', run_id='api_rep01', format_mode='schema')
    d.update(kw); return type('A', (), d)


class ApiRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name)
        self.pkg = self.base / 'pkg'
        for d in ('prompts', 'model_inputs', 'data', 'gold', 'judge_prompts'):
            shutil.copytree(ROOT / d, self.pkg / d)
        self.patches = [mock.patch.object(pilot, 'BASE', self.pkg), mock.patch.object(api_run, 'REPORT_DIR', self.pkg / 'smoke_reports'),
                        mock.patch.object(api_run, 'APPROVAL_DIR', self.pkg / 'smoke_reports' / 'approved_api'),
                        mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'OPENAI_API_KEY': 'k'}),
                        mock.patch.object(api_run.time, 'sleep', lambda s: None)]
        for p in self.patches: p.start()
        src = make_run(self.base / 'src'); self.stage = {s: (src / f'{s}_parsed.json').read_text() for s in ('A1', 'A2', 'A3', 'B1')}
    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def sequence(self, provider, texts, **kw):
        wrap = ANTH if provider == 'anthropic' else OAI
        it = iter(texts); seen = []
        def post(url, headers, body, timeout):
            seen.append((url, headers, body)); t = next(it)
            if isinstance(t, Exception): raise t
            return wrap(t, **kw)
        post.seen = seen; return post

    def test_request_shapes_and_no_thinking_parameters(self):
        for provider in ('anthropic', 'openai'):
            cfg = api_run.approval_config(args(provider))
            url, headers, body = api_run.build_request(cfg, 'PROMPT', seed=7, stage='A2')
            self.assertEqual(body['temperature'], 0.6); self.assertNotIn('thinking', body); self.assertNotIn('reasoning_effort', body)
            schema = stage_schemas.stage_schema('A2')
            if provider == 'anthropic':
                self.assertIn('x-api-key', headers); self.assertNotIn('seed', body); self.assertNotIn('response_format', body)
                self.assertEqual(body['output_config'], {'format': {'type': 'json_schema', 'schema': schema}})
            else:
                self.assertEqual(body['seed'], 7); self.assertIn('max_completion_tokens', body); self.assertNotIn('output_config', body)
                self.assertEqual(body['response_format'], {'type': 'json_schema', 'json_schema': {'name': 'A2_output', 'strict': True, 'schema': schema}})
            self.assertEqual(cfg['schemas'], stage_schemas.all_hashes())
            with self.assertRaises(ValueError): api_run.build_request(cfg, 'PROMPT')           # schema mode needs the stage
            native = api_run.approval_config(args(provider, format_mode='native'))
            nb = api_run.build_request(native, 'PROMPT', seed=7)[2]
            self.assertNotIn('output_config', nb); self.assertNotIn('schemas', native)
            self.assertEqual(nb.get('response_format'), {'type': 'json_object'} if provider == 'openai' else None)
        cfg = api_run.approval_config(args('openai', reasoning_effort='low'))
        self.assertEqual(api_run.build_request(cfg, 'P', stage='B1')[2]['reasoning_effort'], 'low')

    def test_schemas_are_portable_and_hashes_change_with_schema(self):
        self.assertEqual(stage_schemas.portable_problems(), [])
        for bad in ({'type': 'object', 'properties': {'a': {'type': 'string', 'minLength': 1}}, 'required': ['a'], 'additionalProperties': False},
                    {'type': 'object', 'properties': {'a': {'type': 'array', 'items': {'type': 'string'}, 'minItems': 2}}, 'required': ['a'], 'additionalProperties': False},
                    {'type': 'object', 'properties': {'a': {'type': 'string'}, 'b': {'type': 'string'}}, 'required': ['a'], 'additionalProperties': False},
                    {'type': 'object', 'properties': {'a': {'$ref': '#'}}, 'required': ['a'], 'additionalProperties': False},
                    {'type': 'array', 'items': {'type': 'string'}}):
            self.assertTrue(stage_schemas.portable_problems(bad), bad)

    def test_classification(self):
        n = lambda **k: dict({'text': '{}', 'stop': 'end_turn', 'model': 'm', 'usage': {}, 'thinking_chars': 0}, **k)
        self.assertEqual(api_run.classify('anthropic', n())[0], None)
        self.assertEqual(api_run.classify('anthropic', n(stop='max_tokens'))[0], 'OUTPUT_TRUNCATED')
        self.assertEqual(api_run.classify('openai', n(stop='length'))[0], 'OUTPUT_TRUNCATED')
        self.assertEqual(api_run.classify('openai', n(stop='content_filter'))[0], 'CONFIGURATION_ERROR')
        self.assertEqual(api_run.classify('anthropic', n(text='  '))[0], 'METADATA_INCOMPLETE_REVIEW')
        self.assertEqual(api_run.classify('anthropic', n(model=None))[0], 'METADATA_INCOMPLETE_REVIEW')
        self.assertEqual(api_run.classify('anthropic', n(stop=None))[0], 'METADATA_INCOMPLETE_REVIEW')

    def test_run_is_refused_without_a_matching_smoke_approval(self):
        with self.assertRaises(RuntimeError) as cm:
            api_run.run_one(args())
        self.assertIn('smoke', str(cm.exception))

    def _approve(self, provider):
        post = self.sequence(provider, [self.stage[s] for s in ('A1', 'A2', 'A3', 'B1')])
        with mock.patch.object(api_run, 'http_post', post):
            api_run.smoke(args(provider))
        self.assertEqual(len(post.seen), 4)                      # four-stage chain
        return post

    def test_smoke_approves_exact_configuration_only(self):
        self._approve('anthropic')
        api_run.assert_approved(api_run.approval_config(args()))
        for changed in (args(format_mode='native'), args(temperature=0.2), args(max_tokens=100), args(model='other'), args('openai')):
            with self.assertRaises(RuntimeError): api_run.assert_approved(api_run.approval_config(changed))

    def test_smoke_failure_writes_no_approval(self):
        post = self.sequence('anthropic', [urllib.error.HTTPError('u', 400, 'temperature not supported', {}, None)])
        with mock.patch.object(api_run, 'http_post', post), self.assertRaises(SystemExit):
            api_run.smoke(args())
        self.assertFalse((self.pkg / 'smoke_reports' / 'approved_api').exists())

    def test_full_run_both_providers_uses_pipeline_layout_and_records_models(self):
        for provider in ('anthropic', 'openai'):
            self._approve(provider)
            post = self.sequence(provider, [self.stage[s] for s in ('A1', 'A2', 'A3', 'B1')])
            with mock.patch.object(api_run, 'http_post', post):
                api_run.run_one(args(provider, run_id=f'{provider}_rep01'))
            run = self.pkg / 'runs' / f'{provider}_rep01' / 'R3_L'
            self.assertEqual(len(post.seen), 4)
            self.assertIn('Gift', post.seen[0][2]['messages'][0]['content'] + 'Gift')       # prompt sent
            for st in ('A1', 'A2', 'A3', 'B1'):
                self.assertTrue((run / f'{st}_parsed.json').exists(), st)
                self.assertTrue((run / f'{st}_api_attempts.json').exists())
            meta = pilot.metadata(run)
            self.assertTrue(meta['returned_model_consistent']); self.assertEqual(len(meta['returned_models']), 1)
            settings = json.loads(meta['inference_settings'])
            self.assertFalse(settings['think']); self.assertEqual(settings['provider'], provider)
            self.assertEqual(pilot.settings_for_cohort(meta['inference_settings'])['options'].get('seed', 'gone'), 'gone')
            self.assertIn(provider, meta['model_version'])

    def test_retries_identical_request_on_transient_errors_and_logs_attempts(self):
        self._approve('anthropic')
        err = lambda c: urllib.error.HTTPError('u', c, 'busy', {}, None)
        post = self.sequence('anthropic', [err(529), err(429), self.stage['A1'], self.stage['A2'], self.stage['A3'], self.stage['B1']])
        with mock.patch.object(api_run, 'http_post', post):
            api_run.run_one(args(run_id='retry_rep01'))
        run = self.pkg / 'runs' / 'retry_rep01' / 'R3_L'
        att = json.loads((run / 'A1_api_attempts.json').read_text())
        self.assertEqual([a['outcome'] for a in att], ['HTTP 529', 'HTTP 429', 'ok'])
        self.assertEqual(post.seen[0][2], post.seen[1][2])                       # identical request each time

    def test_permanent_error_and_truncation_are_technical_failures_not_model_failures(self):
        self._approve('anthropic')
        post = self.sequence('anthropic', [urllib.error.HTTPError('u', 400, 'bad', {}, None), self.stage['B1']])
        with mock.patch.object(api_run, 'http_post', post), self.assertRaises(SystemExit):
            api_run.run_one(args(run_id='bad_rep01'))
        rec = json.loads((self.pkg / 'runs' / 'bad_rep01' / 'R3_L' / 'A1_record.json').read_text())
        self.assertEqual((rec['reason'], rec['retry_required']), ('CONFIGURATION_ERROR', True))
        self.assertTrue((self.pkg / 'runs' / 'bad_rep01' / 'R3_L' / 'B1_parsed.json').exists())     # B still ran, independently
        post = self.sequence('anthropic', [self.stage['A1'], self.stage['B1']], stop='max_tokens')
        with mock.patch.object(api_run, 'http_post', post), self.assertRaises(SystemExit):
            api_run.run_one(args(run_id='trunc_rep01'))
        rec = json.loads((self.pkg / 'runs' / 'trunc_rep01' / 'R3_L' / 'A1_record.json').read_text())
        self.assertEqual(rec['reason'], 'OUTPUT_TRUNCATED')

    def test_model_change_inside_a_run_is_flagged(self):
        self._approve('anthropic')
        models = iter(['claude-a', 'claude-a', 'claude-b', 'claude-b'])
        it = iter(self.stage[s] for s in ('A1', 'A2', 'A3', 'B1'))
        def post(url, headers, body, timeout): return ANTH(next(it), model=next(models))
        with mock.patch.object(api_run, 'http_post', post):
            api_run.run_one(args(run_id='mix_rep01'))
        self.assertFalse(pilot.metadata(self.pkg / 'runs' / 'mix_rep01' / 'R3_L')['returned_model_consistent'])


class JudgeOpenAI(unittest.TestCase):
    def test_openai_judge_client_request_and_parse(self):
        import judge_review as jr, io
        seen = {}
        class R(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): pass
        def fake(request, timeout):
            seen['body'] = json.loads(request.data); seen['auth'] = request.headers.get('Authorization')
            return R(json.dumps(OAI('{"ok":true}', model='gpt-judge-1')).encode())
        with mock.patch.object(jr.urllib.request, 'urlopen', fake):
            text, meta = jr.openai_client('gpt-judge', 'KEY')('SYS', 'USER')
        self.assertEqual(text, '{"ok":true}'); self.assertEqual(meta['model'], 'gpt-judge-1')
        self.assertEqual(seen['body']['temperature'], 0.0); self.assertEqual(seen['body']['messages'][0]['role'], 'system'); self.assertEqual(seen['auth'], 'Bearer KEY')


class OllamaErrorBody(unittest.TestCase):
    def test_http_500_body_is_kept_in_the_failure_record(self):
        import io, ollama_run
        tmp = Path(tempfile.mkdtemp()); run = make_run(tmp)
        for st in ('A2',):
            (run / f'{st}_PROMPT.txt').write_text('prompt text')
            for f in (run / 'A2_parsed.json', run / 'A2_record.json'): f.unlink()
        err = urllib.error.HTTPError('u', 500, 'Internal Server Error', {}, io.BytesIO(b'{"error":"llama runner process has terminated: exit status 2"}'))
        a = type('A', (), dict(model='m', world='R3_L', url='u', timeout=1, format_mode='json'))
        with mock.patch.object(ollama_run, 'ollama_chat', side_effect=err):
            self.assertFalse(ollama_run.run_stage(run, 'A2', a, {'num_ctx': 1, 'num_predict': 1}, False))
        rec = json.loads((run / 'A2_record.json').read_text())
        self.assertIn('llama runner process has terminated', rec['detail']); self.assertEqual(rec['reason'], 'TRANSPORT_ERROR')
        shutil.rmtree(tmp)

    def test_repetition_loop_is_a_counted_model_failure_not_an_excluded_technical_error(self):
        import io, ollama_run
        tmp = Path(tempfile.mkdtemp()); run = make_run(tmp)
        (run / 'A2_PROMPT.txt').write_text('prompt text')
        for f in (run / 'A2_parsed.json', run / 'A2_record.json'): f.unlink()
        err = urllib.error.HTTPError('u', 500, 'Internal Server Error', {}, io.BytesIO(b'{"error":"prediction aborted, token repeat limit reached"}'))
        a = type('A', (), dict(model='m', world='R3_L', url='u', timeout=1, format_mode='json'))
        with mock.patch.object(ollama_run, 'ollama_chat', side_effect=err):
            self.assertFalse(ollama_run.run_stage(run, 'A2', a, {'num_ctx': 1, 'num_predict': 1}, False))
        rec = json.loads((run / 'A2_record.json').read_text())
        self.assertEqual((rec['status'], rec['reason'], rec['retry_required']), ('model_format_failure', 'MODEL_DEGENERATE_REPETITION', False))
        res = pilot._path_result(run, 'A', 'NEED_MORE_INFORMATION')
        self.assertEqual((res['status'], res['completion_category'], res['stop_stage']), ('no_conclusion', 'model_format_failure', 'A2'))
        self.assertFalse(res['retry_required'])
        shutil.rmtree(tmp)


if __name__ == '__main__':
    unittest.main()
