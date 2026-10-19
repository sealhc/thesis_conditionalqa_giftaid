import json, sys, tempfile, unittest
from pathlib import Path
from unittest import mock
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import pilot, ollama_smoke as sm

RAW = json.dumps({"decision_target": "t", "decision_unit": "u", "atoms": [{"id": "a1", "proposition": "p", "entity_scope": "e", "source_ids": ["E01"]}],
                  "eligibility_expression": {"ref": "a1"}, "scope_issues": [], "limitations": []})
A2 = json.dumps({"statuses": [{"id": "a1", "status": "TRUE", "evidence_quote": "q", "justification": "j"}], "scope_issues": []})
A3 = json.dumps({"decision": "YES", "supporting_policy_ids": ["E01"], "decisive_condition_ids": ["a1"], "unresolved_condition_ids": [], "missing_facts": [], "justification": "j",
                 "scope_issues": [], "ir_discrepancies": [], "conditional_branches": []})
B1 = json.dumps({"decision": "YES", "supporting_policy_ids": ["E01"], "missing_facts": [], "justification": "j", "scope_issues": [], "conditional_branches": []})
CHAIN = [RAW, A2, A3, B1]                      # the four stages in call order: A1, A2, A3, B1
RESP = {"message": {"content": RAW}, "done": True, "done_reason": "stop", "eval_count": 50, "prompt_eval_count": 900}


def chain_chat(resp=RESP):
    """Fake Ollama: answers each probe's four calls with a valid A1, A2, A3, B1 in turn."""
    n = [0]
    def chat(*a, **k):
        raw = CHAIN[n[0] % 4]; n[0] += 1
        return {}, dict(resp, message={"content": raw}), raw
    return chat
INFO = {"tag": "qwen3:4b-instruct", "digest": "sha256:abc"}
OPTS = {"temperature": 0.6, "top_p": 0.95, "top_k": 20, "num_ctx": 32768, "num_predict": 8192}


class SmokeGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.patches = [mock.patch.object(sm, 'REPORT_DIR', base), mock.patch.object(sm, 'APPROVAL_DIR', base / 'approved'),
                        mock.patch.object(sm, 'ollama_chat', chain_chat()),
                        mock.patch.object(sm, 'local_model_info', lambda *a, **k: INFO),
                        mock.patch.object(sm, 'ollama_version', lambda *a, **k: 'test')]
        for p in self.patches: p.start()
    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()
    def smoke(self, *argv):
        with mock.patch.object(sys, 'argv', ['ollama_smoke.py', *argv]):
            try: sm.main()
            except SystemExit as e: return e.code
    def gate(self, seed=4101, think=False, fmt='schema', **over):
        o = dict(OPTS, seed=seed); o.update(over)
        sm.assert_approved_smoke('qwen3:4b-instruct', INFO, think, fmt, o)

    def test_no_approval_means_blocked(self):
        with self.assertRaises(RuntimeError): self.gate()
    def test_formal_smoke_approves_every_planned_seed_and_only_those(self):
        self.smoke()                                    # defaults: temperature 0.6, seeds 4101-4103, think OFF
        for s in (4101, 4102, 4103): self.gate(seed=s)
        with self.assertRaises(RuntimeError): self.gate(seed=9999)
    def test_sampling_parameters_are_part_of_approval(self):
        self.smoke()
        for over in ({'temperature': 0.0}, {'top_p': 0.9}, {'top_k': 40}, {'num_ctx': 8192}, {'num_predict': 4096}):
            with self.assertRaises(RuntimeError, msg=str(over)): self.gate(**over)
        with self.assertRaises(RuntimeError): self.gate(think=True)
        with self.assertRaises(RuntimeError): self.gate(fmt='none')
        with self.assertRaises(RuntimeError): self.gate(fmt='json')
    def test_model_digest_is_part_of_approval(self):
        self.smoke()
        with self.assertRaises(RuntimeError):
            sm.assert_approved_smoke('qwen3:4b-instruct', {"digest": "sha256:other"}, False, 'schema', dict(OPTS, seed=4101))
    def test_matrix_is_diagnostic_only_and_never_writes_or_overwrites_approval(self):
        self.smoke()
        before = {p.name: p.read_text() for p in sm.APPROVAL_DIR.glob('*.json')}
        self.assertEqual(len(before), 1)
        self.assertEqual(self.smoke('--matrix', '--temperature', '0.0'), 0)
        after = {p.name: p.read_text() for p in sm.APPROVAL_DIR.glob('*.json')}
        self.assertEqual(before, after)
        with self.assertRaises(RuntimeError): self.gate(temperature=0.0)
        self.gate(seed=4102)
    def test_failed_seed_writes_no_approval_and_keeps_old_one(self):
        self.smoke()
        old = {p.name for p in sm.APPROVAL_DIR.glob('*.json')}
        bad = dict(RESP, done_reason='length')
        with mock.patch.object(sm, 'ollama_chat', chain_chat(bad)):
            self.assertEqual(self.smoke('--temperature', '0.7'), 2)
        self.assertEqual(old, {p.name for p in sm.APPROVAL_DIR.glob('*.json')})
    def test_smoke_exercises_all_four_stages_and_any_failing_stage_blocks_approval(self):
        calls = []
        base = chain_chat()
        def spy(url, model, prompt, options, think, timeout, format_mode='json', schema=None):
            calls.append((prompt[:30], schema is not None)); return base(url, model, prompt, options, think, timeout)
        with mock.patch.object(sm, 'ollama_chat', spy): self.smoke('--seeds', '4101')
        self.assertEqual(len(calls), 4); self.assertTrue(all(s for _, s in calls))          # schema mode: a schema accompanies every stage
        self.gate(seed=4101)
        # A2 answers with junk: the chain must fail even though A1 was fine (the 2026-10 smoke test only probed A1)
        n = [0]
        def broken(*a, **k):
            raw = RAW if n[0] % 4 == 0 else '{"statuses": [' if n[0] % 4 == 1 else CHAIN[n[0] % 4]; n[0] += 1
            return {}, RESP, raw
        with mock.patch.object(sm, 'ollama_chat', broken): self.assertEqual(self.smoke('--seeds', '4102', '--temperature', '0.7'), 2)
        with self.assertRaises(RuntimeError): self.gate(seed=4102, temperature=0.7)

    def test_approval_binds_the_schema_content(self):
        self.smoke()
        import stage_schemas
        with mock.patch.object(stage_schemas, 'all_hashes', lambda: {'A1': 'x', 'A2': 'y', 'A3': 'z', 'B1': 'w'}):
            with self.assertRaises(RuntimeError): self.gate()

    def test_two_arms_can_be_approved_side_by_side(self):
        self.smoke(); self.smoke('--think')
        self.gate(think=False); self.gate(think=True)
        self.assertEqual(len(list(sm.APPROVAL_DIR.glob('*.json'))), 2)


if __name__ == '__main__':
    unittest.main()
