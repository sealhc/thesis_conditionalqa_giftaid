import json, shutil, sys, tempfile, unittest, urllib.error, io
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts')); sys.path.insert(0, str(ROOT / 'tests'))
import pilot, stage_schemas, ollama_schema_diagnose as diag, ollama_run
from test_stepwise_review import make_run


def check(schema, value, defs, path='$'):
    """Tiny validator for the JSON-Schema subset stage_schemas uses. Returns list of problems."""
    if '$ref' in schema:
        return check(defs[schema['$ref'].split('/')[-1]], value, defs, path)
    if 'anyOf' in schema:
        alts = [check(s, value, defs, path) for s in schema['anyOf']]
        return [] if any(not a for a in alts) else [f'{path}: matches no anyOf branch']
    t, errs = schema.get('type'), []
    if t == 'object':
        if not isinstance(value, dict): return [f'{path}: not object']
        for k in schema.get('required', []):
            if k not in value: errs.append(f'{path}: missing {k}')
        if schema.get('additionalProperties') is False:
            errs += [f'{path}: extra key {k}' for k in value if k not in schema.get('properties', {})]
        for k, s in schema.get('properties', {}).items():
            if k in value: errs += check(s, value[k], defs, f'{path}.{k}')
    elif t == 'array':
        if not isinstance(value, list): return [f'{path}: not array']
        if len(value) < schema.get('minItems', 0): errs.append(f'{path}: too short')
        for i, v in enumerate(value): errs += check(schema['items'], v, defs, f'{path}[{i}]')
    elif t == 'string':
        if not isinstance(value, str): return [f'{path}: not string']
        if len(value) < schema.get('minLength', 0): errs.append(f'{path}: empty')
        if 'enum' in schema and value not in schema['enum']: errs.append(f'{path}: {value!r} not in enum')
    return errs


def validate(stage, obj):
    s = stage_schemas.stage_schema(stage)
    return check(s, obj, s.get('$defs', {}))


class Schemas(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); run = make_run(Path(self.tmp.name) / 'src')
        self.ok = {s: json.loads((run / f'{s}_parsed.json').read_text()) for s in ('A1', 'A2', 'A3', 'B1')}
    def tearDown(self): self.tmp.cleanup()

    def test_valid_pipeline_outputs_satisfy_schema_and_pipeline_validator(self):
        for stage, obj in self.ok.items():
            self.assertEqual(validate(stage, obj), [], stage); pilot.schema_validate(stage, obj)

    def test_schema_rejects_what_the_pipeline_validator_rejects(self):
        bad = json.loads(json.dumps(self.ok['A2'])); bad['statuses'][0]['status'] = 'MAYBE'
        self.assertTrue(validate('A2', bad)); self.assertRaises(ValueError, pilot.schema_validate, 'A2', bad)
        bad = json.loads(json.dumps(self.ok['A3'])); bad['decision'] = 'MAYBE'
        self.assertTrue(validate('A3', bad)); self.assertRaises(ValueError, pilot.schema_validate, 'A3', bad)
        bad = json.loads(json.dumps(self.ok['B1'])); del bad['justification']
        self.assertTrue(validate('B1', bad)); self.assertRaises(ValueError, pilot.schema_validate, 'B1', bad)
        bad = json.loads(json.dumps(self.ok['A1'])); bad['eligibility_expression'] = {'op': 'XOR', 'args': []}
        self.assertTrue(validate('A1', bad))

    def test_schemas_contain_no_case_specific_values(self):
        text = json.dumps(stage_schemas.SCHEMAS)
        policy_ids = [p['id'] for p in pilot.read('data/policy_dev38_13.json')['passages']]
        for pid in policy_ids: self.assertNotIn(pid, text)
        self.assertNotIn('"a1"', text); self.assertNotIn('maxItems', text)
        a2 = stage_schemas.stage_schema('A2')
        self.assertNotIn('enum', a2['properties']['statuses']['items']['properties']['id'])
        self.assertNotIn('minItems', a2['properties']['statuses'])

    def test_hashes_are_stable_and_distinct(self):
        h = stage_schemas.all_hashes(); self.assertEqual(h, stage_schemas.all_hashes()); self.assertEqual(len(set(h.values())), 4)


class Diagnose(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name); self.pkg = self.base / 'pkg'
        for d in ('prompts', 'model_inputs', 'data', 'gold'): shutil.copytree(ROOT / d, self.pkg / d)
        run = make_run(self.base / 'src')
        self.text = {s: (run / f'{s}_parsed.json').read_text() for s in ('A1', 'A2', 'A3', 'B1')}
        self.p = [mock.patch.object(pilot, 'BASE', self.pkg), mock.patch.object(diag, 'DIAG_DIR', self.pkg / 'diagnostics')]
        for x in self.p: x.start()
    def tearDown(self):
        for x in self.p: x.stop()
        self.tmp.cleanup()

    def chat(self, contents, reason='stop'):
        it, seen = iter(contents), []
        def f(prompt, mode, schema):
            c = next(it); seen.append((mode, schema))
            if isinstance(c, Exception): raise c
            return {'mode': mode}, {'done_reason': reason, 'eval_count': 9, 'prompt_eval_count': 3, 'message': {'content': c}}, c
        return f, seen

    def test_count_top_level_objects(self):
        self.assertEqual(diag.count_top_level_objects('{"a":1}\n{"a":1}\n{"b":{"c":2}}'), 3)
        self.assertEqual(diag.count_top_level_objects('   \n\n'), 0)

    def test_metrics_report_whitespace_loop(self):
        content = '{"a":1}' + ' \n' * 500
        m = diag.call_metrics({'done_reason': 'length', 'message': {'thinking': 'xyz'}}, content, 1.0)
        self.assertGreater(m['whitespace_fraction'], .9); self.assertEqual(m['thinking_chars'], 3); self.assertEqual(m['chars_before_first_brace'], 0)

    def test_full_chain_passes_and_writes_only_to_diagnostics(self):
        f, seen = self.chat([self.text[s] for s in ('A1', 'A2', 'A3', 'B1')])
        r = diag.run_world(f, 'R1_L', 'schema', 4101, self.pkg / 'diagnostics' / 't')
        self.assertEqual([s['stage'] for s in r['stages']], ['A1', 'A2', 'A3', 'B1'])
        self.assertTrue(all(s['ok'] for s in r['stages']), r)
        self.assertEqual([m for m, _ in seen], ['schema'] * 4)
        self.assertEqual([sc for _, sc in seen], [stage_schemas.stage_schema(s) for s in ('A1', 'A2', 'A3', 'B1')])
        self.assertFalse((self.pkg / 'runs').exists())

    def test_a2_failure_stops_a_branch_but_b1_still_runs_and_nothing_is_repaired(self):
        f, seen = self.chat([self.text['A1'], '{"statuses": [', self.text['B1']])
        r = diag.run_world(f, 'R1_L', 'json', 4101, self.pkg / 'diagnostics' / 't')
        s = {x['stage']: x for x in r['stages']}
        self.assertFalse(s['A2']['ok']); self.assertIsNone(s['A3']['ok']); self.assertTrue(s['B1']['ok'])
        self.assertTrue(s['A2']['pipeline_parse_and_schema_validate'].startswith('FAIL'))
        raw = (self.pkg / 'diagnostics' / 't' / 'json_seed4101' / 'A2_raw_content.txt').read_text()
        self.assertEqual(raw, '{"statuses": [')

    def test_length_stop_is_not_ok_even_if_json_parses(self):
        f, _ = self.chat([self.text['A1']], reason='length')
        row, obj = diag.one_call(f, 'A1', 'p', 'none', self.pkg / 'diagnostics' / 't')
        self.assertFalse(row['ok'])

    def test_http_500_body_is_recorded(self):
        err = urllib.error.HTTPError('u', 500, 'x', {}, io.BytesIO(b'{"error":"prediction aborted, token repeat limit reached"}'))
        f, _ = self.chat([err])
        row, obj = diag.one_call(f, 'A2', 'p', 'json', self.pkg / 'diagnostics' / 't')
        self.assertEqual(row['http_error'], 500); self.assertIn('token repeat limit', row['server_said']); self.assertIsNone(obj)

    def test_ollama_chat_sends_schema_as_format(self):
        captured = {}
        class R:
            def __enter__(s): return s
            def __exit__(s, *a): pass
            def read(s): return b'{"message":{"content":"{}"},"done_reason":"stop"}'
        def fake(req, timeout): captured.update(json.loads(req.data)); return R()
        with mock.patch.object(ollama_run.urllib.request, 'urlopen', fake):
            ollama_run.ollama_chat('http://x', 'm', 'p', {}, False, 5, format_mode='schema', schema=stage_schemas.stage_schema('A2'))
            self.assertEqual(captured['format'], stage_schemas.stage_schema('A2')); self.assertIs(captured['think'], False)
            self.assertRaises(ValueError, ollama_run.ollama_chat, 'http://x', 'm', 'p', {}, False, 5, format_mode='schema')
            self.assertRaises(ValueError, ollama_run.ollama_chat, 'http://x', 'm', 'p', {}, False, 5, format_mode='bogus')


if __name__ == '__main__':
    unittest.main()
