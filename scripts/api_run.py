#!/usr/bin/env python3
"""Hosted-API runner (Anthropic Messages API or OpenAI Chat Completions) for the strong-model arm.

It reuses the exact run folder layout, prompts, recorder (pilot.record), technical-failure rules and audit of the Qwen
runner, so judge_review.py / stepwise_review.py / pilot.py summary work unchanged. Differences that matter for the
paper are RECORDED, not hidden (see run_metadata.json inference_settings):
  * Thinking/reasoning is OFF: no `thinking` parameter (Anthropic); OpenAI `reasoning_effort` is sent only if you pass
    --reasoning-effort, and is then part of the approved configuration.
  * Output format (default --format-mode schema): the SAME structure-only JSON Schema per stage (stage_schemas.py) is sent to every arm:
    Ollama `format`, Anthropic `output_config.format`, OpenAI `response_format` json_schema strict. The schemas are restricted to what all
    three accept (no recursion, no minLength, minItems 0/1) and their hashes are part of the approved configuration. `--format-mode native`
    restores the old behaviour (OpenAI json_object, Anthropic prompt only). Anthropic has no seed parameter; OpenAI's is best-effort.
  * Repetitions are independent calls; their identity is the run ID. A seed is sent only where the API accepts one.
  * The model ID is fixed by --model; the model the API reports having used is stored for every call, and a run whose
    calls report different models is flagged.
  * Transient server errors (HTTP 429/500/502/503/529, timeouts) are retried up to --retries times with the IDENTICAL
    request; every attempt is logged in <stage>_api_attempts.json. No retry is made for a bad answer.

COMMANDS (API keys are read from the environment: ANTHROPIC_API_KEY / OPENAI_API_KEY)
  smoke  --provider P --model M     one real four-stage chain (A1,A2,A3,B1; R0 input) ; writes an approval for this exact configuration
  run    --provider P --model M --world W --run-id ID
  batch  --provider P --model M [--worlds ...] [--repeats 3]
Nothing here was run against a live API in the package it ships in: do `smoke` first and read its report.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pilot
import smoke_chain
import stage_schemas

HERE = Path(__file__).resolve().parent
REPORT_DIR = pilot.BASE / 'smoke_reports'
APPROVAL_DIR = REPORT_DIR / 'approved_api'
ENDPOINTS = {'anthropic': 'https://api.anthropic.com/v1/messages', 'openai': 'https://api.openai.com/v1/chat/completions'}
KEY_ENV = {'anthropic': 'ANTHROPIC_API_KEY', 'openai': 'OPENAI_API_KEY'}
WORLDS = ['R0', 'R1_L', 'R1_H', 'R2_L', 'R2_H', 'R3_L', 'R3_H']
RETRY_STATUS = {429, 500, 502, 503, 504, 529}


# ------------------------------------------------------------------ transport (patched in tests) ------------------------------------------------------------------
def http_post(url, headers, body, timeout):
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode('utf8'), headers=headers, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf8'))


def _need_stage(stage):
    if stage not in stage_schemas.SCHEMAS:
        raise ValueError('schema mode needs the stage (A1, A2, A3 or B1) to pick its schema')
    return stage


def build_request(cfg, prompt, seed=None, stage=None):
    """Return (url, headers, body). `cfg` is the approval configuration (see approval_config)."""
    key = os.environ.get(KEY_ENV[cfg['provider']], '')
    if cfg['provider'] == 'anthropic':
        body = {'model': cfg['model'], 'max_tokens': cfg['max_tokens'], 'temperature': cfg['temperature'],
                'messages': [{'role': 'user', 'content': prompt}]}
        if cfg['format_mode'] == 'schema':
            body['output_config'] = {'format': {'type': 'json_schema', 'schema': stage_schemas.stage_schema(_need_stage(stage))}}
        headers = {'content-type': 'application/json', 'x-api-key': key, 'anthropic-version': '2023-06-01'}
    else:
        body = {'model': cfg['model'], 'max_completion_tokens': cfg['max_tokens'], 'temperature': cfg['temperature'],
                'messages': [{'role': 'user', 'content': prompt}]}
        if cfg['format_mode'] == 'schema':
            body['response_format'] = {'type': 'json_schema', 'json_schema': {'name': f'{_need_stage(stage)}_output', 'strict': True,
                                                                             'schema': stage_schemas.stage_schema(stage)}}
        elif cfg['format_mode'] == 'json':
            body['response_format'] = {'type': 'json_object'}
        if cfg.get('reasoning_effort'):
            body['reasoning_effort'] = cfg['reasoning_effort']
        if seed is not None:
            body['seed'] = seed
        headers = {'content-type': 'application/json', 'authorization': f'Bearer {key}'}
    return ENDPOINTS[cfg['provider']], headers, body


def normalize(provider, data):
    """Provider response -> {text, stop, model, usage, thinking_chars}. Never invents a field."""
    if not isinstance(data, dict):
        raise ValueError('API result is not a JSON object')
    if provider == 'anthropic':
        blocks = data.get('content') if isinstance(data.get('content'), list) else []
        text = ''.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text')
        thinking = ''.join(b.get('thinking', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'thinking')
        return {'text': text, 'stop': data.get('stop_reason'), 'model': data.get('model'), 'usage': data.get('usage'), 'thinking_chars': len(thinking)}
    choices = data.get('choices') if isinstance(data.get('choices'), list) else []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get('message') if isinstance(first.get('message'), dict) else {}
    content = message.get('content')
    return {'text': content if isinstance(content, str) else '', 'stop': first.get('finish_reason'), 'model': data.get('model'),
            'usage': data.get('usage'), 'thinking_chars': 0, 'refusal': message.get('refusal')}


def classify(provider, norm):
    """(reason, detail) for a technical failure, else (None, None). Invalid model JSON is NOT decided here."""
    stop = norm['stop']
    if stop in ('max_tokens', 'length'):
        return 'OUTPUT_TRUNCATED', f'stop reason {stop!r}: output budget reached; raise --max-tokens and use a NEW run ID'
    if stop in ('refusal', 'content_filter') or norm.get('refusal'):
        return 'CONFIGURATION_ERROR', f'API reported a refusal / filter ({stop!r}); inspect before scoring'
    if stop is None:
        return 'METADATA_INCOMPLETE_REVIEW', 'response has no stop reason; cannot establish that the output is complete'
    if not norm['text'].strip():
        return 'METADATA_INCOMPLETE_REVIEW', 'empty text content'
    if not norm.get('model'):
        return 'METADATA_INCOMPLETE_REVIEW', 'response does not report which model answered'
    if stop not in ('end_turn', 'stop', 'stop_sequence'):
        return 'CONFIGURATION_ERROR', f'unexpected stop reason {stop!r}'
    return None, None


def call_with_retries(cfg, prompt, seed, timeout, retries, sleep=time.sleep, stage=None):
    """Returns (norm, raw_response, attempts). Raises RuntimeError('HTTP n: ...') / TimeoutError after the last attempt."""
    attempts = []
    for n in range(1, retries + 2):
        url, headers, body = build_request(cfg, prompt, seed, stage)
        entry = {'attempt': n, 'time_utc': pilot.now()}
        try:
            data = http_post(url, headers, body, timeout)
            entry['outcome'] = 'ok'; attempts.append(entry)
            return normalize(cfg['provider'], data), data, attempts, body
        except urllib.error.HTTPError as exc:
            entry.update(outcome=f'HTTP {exc.code}', detail=str(exc.reason)); attempts.append(entry)
            if exc.code not in RETRY_STATUS or n > retries:
                err = RuntimeError(f'HTTP {exc.code}: {exc.reason}'); err.attempts = attempts; err.http_status = exc.code
                raise err
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            entry.update(outcome='transport', detail=repr(exc)); attempts.append(entry)
            if n > retries:
                err = RuntimeError(f'transport: {exc!r}'); err.attempts = attempts; err.http_status = None
                raise err
        sleep(min(60, 2 ** n))


# ------------------------------------------------------------------ approval ------------------------------------------------------------------
def approval_config(args):
    mode = getattr(args, 'format_mode', 'schema')
    cfg = {'provider': args.provider, 'model': args.model, 'temperature': args.temperature, 'max_tokens': args.max_tokens,
           'format_mode': 'schema' if mode == 'schema' else ('json' if args.provider == 'openai' else 'prompt_only'),
           'reasoning_effort': getattr(args, 'reasoning_effort', None), 'thinking': False}
    if mode == 'schema':
        cfg['schemas'] = stage_schemas.all_hashes()
    return cfg


def config_id(cfg):
    return pilot.hash_(pilot.canon(cfg))[:16]


def assert_approved(cfg):
    path = APPROVAL_DIR / f'{config_id(cfg)}.json'
    if not path.exists():
        raise RuntimeError('No API smoke approval for this exact configuration. Run: python3 scripts/api_run.py smoke '
                           f'--provider {cfg["provider"]} --model {cfg["model"]} --temperature {cfg["temperature"]} --max-tokens {cfg["max_tokens"]}'
                           + (f' --reasoning-effort {cfg["reasoning_effort"]}' if cfg.get('reasoning_effort') else '') + f'  (configuration: {cfg!r})')
    rec = json.loads(path.read_text(encoding='utf8'))
    if not rec.get('passed') or rec.get('config') != cfg:
        raise RuntimeError(f'Approval file {path.name} is invalid or does not match the requested configuration.')
    return rec


def smoke(args):
    """One live four-stage chain (A1, A2, A3, B1; R0 input, no Gold). Approves this exact configuration only if every stage was complete and structurally valid."""
    cfg = approval_config(args)
    result = {'time_utc': pilot.now(), 'config': cfg, 'probe_type': 'chain_R0_A1_A2_A3_B1_without_gold', 'passed': False, 'evidence_is_not_gold_evaluation': True}
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    models = set()

    def call(stage, prompt):
        norm, raw, attempts, body = call_with_retries(cfg, prompt, args.seed, args.timeout, args.retries, stage=stage)
        models.add(norm['model'])
        pilot.write(REPORT_DIR / f'{tag}_{stage}_api_request.json', pilot.canon(body) + '\n')
        pilot.write(REPORT_DIR / f'{tag}_{stage}_api_response.json', pilot.canon(raw) + '\n')
        pilot.write(REPORT_DIR / f'{tag}_{stage}_api_raw_content.txt', norm['text'])
        technical, detail = classify(cfg['provider'], norm)
        return norm['text'], {'technical_failure': technical, 'technical_failure_detail': detail, 'stop': norm['stop'],
                              'returned_model': norm['model'], 'usage': norm['usage'], 'thinking_chars': norm['thinking_chars']}
    try:
        passed, rows = smoke_chain.run_chain(call)
        result['stages'] = rows; result['passed'] = passed
    except RuntimeError as exc:
        result['api_error'] = str(exc)      # e.g. HTTP 400: the model rejects a parameter (temperature, response_format, schema ...)
    except ValueError as exc:
        result['api_error'] = str(exc)
    result['returned_models'] = sorted(m for m in models if m)
    if len(result['returned_models']) > 1:
        result['passed'] = False; result['api_error'] = f'different models answered within one chain: {result["returned_models"]}'
    pilot.write(REPORT_DIR / f'{tag}_api_smoke_report.json', pilot.canon(result) + '\n')
    per = ' '.join(f"{r['stage']}={'ok' if r['ok'] else 'FAIL'}" for r in result.get('stages', []))
    print(f'API smoke {"PASS" if result["passed"] else "FAIL"}: models={result["returned_models"]} {per} error={result.get("api_error")}')
    if result['passed']:
        APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
        path = APPROVAL_DIR / f'{config_id(cfg)}.json'
        pilot.write(path, pilot.canon({'passed': True, 'time_utc': pilot.now(), 'config': cfg, 'config_id': config_id(cfg),
                                       'returned_model': result['returned_models'][0], 'probe_type': result['probe_type'],
                                       'note': 'Compatibility preflight only. Not a Gold evaluation.'}) + '\n')
        print(f'APPROVED configuration {path.stem}.')
    else:
        print('NOT APPROVED. Read the report in smoke_reports/ (a rejected parameter or schema shows up as api_error).'); raise SystemExit(2)


# ------------------------------------------------------------------ runs ------------------------------------------------------------------
def run_stage(run, stage, cfg, args, seed):
    prompt = (run / f'{stage}_PROMPT.txt').read_text(encoding='utf8')
    print(f'Calling {cfg["provider"]}:{cfg["model"]} {run.name}/{stage} ...', flush=True)
    try:
        norm, raw, attempts, body = call_with_retries(cfg, prompt, seed, args.timeout, args.retries, stage=stage)
    except RuntimeError as exc:
        status = getattr(exc, 'http_status', None)
        pilot.write(run / f'{stage}_api_attempts.json', pilot.canon(getattr(exc, 'attempts', [])) + '\n')
        reason = 'CONFIGURATION_ERROR' if status in (400, 401, 403, 404, 422) else 'TRANSPORT_ERROR'
        pilot._write_stage_event(run, stage, status='technical_failure', reason=reason, detail=str(exc))
        return False
    pilot.write(run / f'{stage}_api_attempts.json', pilot.canon(attempts) + '\n')
    pilot.write(run / f'{stage}_api_request.json', pilot.canon({k: v for k, v in body.items()}) + '\n')
    pilot.write(run / f'{stage}_api_response.json', pilot.canon(raw) + '\n')
    metrics = {'stop': norm['stop'], 'returned_model': norm['model'], 'usage': norm['usage'], 'thinking_chars': norm['thinking_chars'],
               'attempts': len(attempts)}
    pilot.write(run / f'{stage}_api_metrics.json', pilot.canon(metrics) + '\n')
    reason, why = classify(cfg['provider'], norm)
    if reason:
        pilot._write_stage_event(run, stage, status='technical_failure', reason=reason, detail=why, raw=norm['text'], api=metrics)
        print(f'{stage}: TECHNICAL DEFERRED [{reason}]; not scored as model failure.', flush=True)
        return False
    raw_file = run / f'{stage}_RECEIVED_RAW_UNPROCESSED.txt'
    pilot.write(raw_file, norm['text'])
    pilot.record(argparse.Namespace(run=str(run), stage=stage, response=str(raw_file)))
    rec_file = run / f'{stage}_record.json'
    record = json.loads(rec_file.read_text(encoding='utf8')); record['api_metrics'] = metrics
    pilot.write(rec_file, pilot.canon(record) + '\n')
    return record['valid_json']


def run_stage_with_retries(run, stage, cfg, args, seed):
    return pilot.run_with_retries(run, stage, lambda s: run_stage(run, stage, cfg, args, s), getattr(args, 'max_attempts', 1), seed)


def run_one(args):
    cfg = approval_config(args)
    approval = assert_approved(cfg)
    seed = args.seed if cfg['provider'] == 'openai' else None
    settings = json.dumps({'options': {'temperature': cfg['temperature'], 'max_tokens': cfg['max_tokens'], 'seed': seed},
                           'think': False, 'provider': cfg['provider'], 'api_format': cfg['format_mode'], 'schema_sha256': cfg.get('schemas'),
                           'reasoning_effort': cfg['reasoning_effort'], 'approved_config_id': config_id(cfg),
                           'retry_policy': {'max_attempts_per_stage': getattr(args, 'max_attempts', 1), 'seed_rule': f'base + {pilot.RETRY_SEED_STEP}*(attempt-1); Anthropic has no seed', 'error_feedback': False},
                           'smoke_gate': 'APPROVED'}, ensure_ascii=False)
    run = pilot.prepare(argparse.Namespace(world=args.world, run_id=args.run_id, model=args.model,
                                           model_version=f'{cfg["provider"]} API; smoke returned_model={approval.get("returned_model")}', settings=settings))
    ok_a = True
    for stage in ('A1', 'A2', 'A3'):
        if not run_stage_with_retries(run, stage, cfg, args, seed):
            ok_a = False; print(f'A stopped after {stage}; B is independent and will still run.', flush=True); break
    ok_b = run_stage_with_retries(run, 'B1', cfg, args, seed)
    models = set()
    for f in run.glob('*_api_metrics.json'):
        models.add(json.loads(f.read_text(encoding='utf8')).get('returned_model'))
    meta = pilot.metadata(run)
    meta['returned_models'] = sorted(m for m in models if m)
    meta['returned_model_consistent'] = len(meta['returned_models']) <= 1
    pilot.write(run / 'run_metadata.json', pilot.canon(meta) + '\n')
    if not meta['returned_model_consistent']:
        print(f'WARNING: calls in this run were answered by different models: {meta["returned_models"]}', flush=True)
    report = pilot.audit_run(run)
    print(f"Saved run: {run}\nA={report['paths']['A']['status']}; B={report['paths']['B']['status']}.", flush=True)
    if not (ok_a and ok_b):
        raise SystemExit(2)


def batch(args):
    cfg = approval_config(args); assert_approved(cfg)             # fail fast before any run starts
    failures = []
    for rep in range(1, args.repeats + 1):
        for world in args.worlds:
            run_id = f'{args.prefix}_rep{rep:02d}'
            cmd = [sys.executable, str(HERE / 'api_run.py'), 'run', '--provider', args.provider, '--model', args.model, '--world', world, '--run-id', run_id,
                   '--temperature', str(args.temperature), '--max-tokens', str(args.max_tokens), '--timeout', str(args.timeout),
                   '--retries', str(args.retries), '--max-attempts', str(args.max_attempts), '--format-mode', args.format_mode, '--seed', str(args.seed_start + rep - 1)]
            if args.reasoning_effort:
                cmd += ['--reasoning-effort', args.reasoning_effort]
            print(f'\n===== {world} repetition {rep}: {run_id} =====', flush=True)
            status = subprocess.run(cmd, check=False).returncode
            if status:
                failures.append({'run_id': run_id, 'world': world, 'process_exit_code': status})
    subprocess.run([sys.executable, str(HERE / 'pilot.py'), 'summary'], check=False)
    print(f'Finished batch, {len(failures)} incomplete or failed runs. Raw records retained.', flush=True)
    if failures:
        print('Investigate and rerun confirmed technical failures with NEW run IDs:', failures, flush=True); raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    def common(p):
        p.add_argument('--provider', required=True, choices=['anthropic', 'openai'])
        p.add_argument('--model', required=True, help='exact API model ID (do not use a moving alias if a dated ID exists)')
        p.add_argument('--temperature', type=float, default=0.6)
        p.add_argument('--max-tokens', type=int, default=8192)
        p.add_argument('--reasoning-effort', default=None, help='OpenAI only; omitted by default (reasoning behaviour is then the API default and must be reported)')
        p.add_argument('--timeout', type=int, default=300)
        p.add_argument('--format-mode', choices=['schema', 'native'], default='schema', help='schema = the shared structure-only JSON Schema per stage (all arms); native = OpenAI json_object / Anthropic prompt only')
        p.add_argument('--retries', type=int, default=3, help='HTTP-level retries of one request after transport errors (not model format failures)')
        p.add_argument('--max-attempts', type=int, default=3, help='attempts per stage after a MODEL format failure (1 = never retry); failures after the cap are UNFINISHED')
    p = sub.add_parser('smoke'); common(p); p.add_argument('--seed', type=int, default=4101)
    p = sub.add_parser('run'); common(p); p.add_argument('--world', required=True, choices=WORLDS); p.add_argument('--run-id', required=True); p.add_argument('--seed', type=int, default=4101)
    p = sub.add_parser('batch'); common(p); p.add_argument('--worlds', nargs='+', default=WORLDS, choices=WORLDS)
    p.add_argument('--repeats', type=int, default=3); p.add_argument('--seed-start', type=int, default=4101); p.add_argument('--prefix', default=None)
    a = ap.parse_args()
    if a.cmd == 'batch' and a.prefix is None:
        a.prefix = f'{a.provider}_nothink_v9'
    {'smoke': smoke, 'run': run_one, 'batch': batch}[a.cmd](a)


if __name__ == '__main__':
    main()
