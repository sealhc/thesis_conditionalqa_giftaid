#!/usr/bin/env python3
"""Ollama Qwen runner with explicit technical-vs-model failure accounting.

A3 receives full source + A1 + A2 and ALWAYS asks the LLM to make the decision.
Technical truncation/timeout means this attempt needs a fresh run ID after fixing setup;
model-format failures are retained as model pipeline completion costs.
"""
import argparse
import json
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import urllib.error
import urllib.request

import pilot
import stage_schemas


def local_model_info(url, model):
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/api/tags', timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
        for row in data.get('models', []):
            if row.get('name') == model or row.get('model') == model:
                return {'tag': row.get('name'), 'digest': row.get('digest'),
                        'quantization': row.get('details', {}).get('quantization_level')}
    except Exception as exc:
        return {'tag': model, 'digest': 'UNAVAILABLE', 'lookup_error': repr(exc)}
    return {'tag': model, 'digest': 'NOT_FOUND_IN_INSTALLED_TAGS'}


def ollama_chat(url, model, prompt, options, think, timeout, format_mode="json", schema=None):
    """Return complete API result even if message.content is blank or truncated."""
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
               'stream': False, 'options': options, 'think': think}
    if format_mode == 'json':
        payload['format'] = 'json'
    elif format_mode == 'schema':
        if not isinstance(schema, dict):
            raise ValueError('format_mode "schema" needs a JSON-Schema dict')
        payload['format'] = schema
    elif format_mode != 'none':
        raise ValueError(f'Unsupported format_mode: {format_mode!r}')
    request = urllib.request.Request(url.rstrip('/') + '/api/chat',
        data=json.dumps(payload, ensure_ascii=False).encode('utf8'),
        headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode('utf8'))
    if not isinstance(result, dict):
        raise ValueError('Ollama result is not a JSON object')
    content = result.get('message', {}).get('content', '') if isinstance(result.get('message'), dict) else ''
    return payload, result, content if isinstance(content, str) else ''


def api_metrics(response, options):
    """Record observed counts; context-pressure is heuristic, never fabricated certainty."""
    message = response.get('message') if isinstance(response.get('message'), dict) else {}
    context_size = options['num_ctx']
    prompt_tokens = response.get('prompt_eval_count')
    generated_tokens = response.get('eval_count')
    thinking = message.get('thinking') if isinstance(message.get('thinking'), str) else ''
    total_tokens = prompt_tokens + generated_tokens if isinstance(prompt_tokens, int) and isinstance(generated_tokens, int) else None
    pressure = ((isinstance(prompt_tokens, int) and prompt_tokens >= int(context_size * .95))
                or (total_tokens is not None and total_tokens >= int(context_size * .95)))
    return {'done': response.get('done'), 'done_reason': response.get('done_reason'),
            'prompt_eval_count': prompt_tokens, 'eval_count': generated_tokens,
            'num_ctx_configured': context_size, 'num_predict_configured': options['num_predict'],
            'thinking_output_characters': len(thinking), 'thinking_field_present': bool(thinking),
            'prompt_plus_generated_tokens_observed': total_tokens,
            'context_pressure_95pct_heuristic': pressure,
            'context_note': 'Context pressure is only a heuristic: tokens counted by Ollama do not prove original input was or was not truncated.'}


def detect_technical_failure(response, options):
    """Pure classifier; model JSON invalidity is handled by pilot.record, not here."""
    m = api_metrics(response, options)
    reason = m['done_reason']
    if reason == 'length':
        return 'OUTPUT_TRUNCATED', 'Ollama done_reason=length; increase output/context budgets before rerunning'
    if m['done'] is False:
        return 'CONFIGURATION_ERROR', 'Non-stream request did not finish (done=false)'
    if (not isinstance(response.get('message',{}).get('content'),str)
            or not response.get('message',{}).get('content','').strip()) and m['thinking_field_present']:
        return 'THINKING_OUTPUT_COMPATIBILITY_REVIEW', ('Ollama returned thinking content but no final message.content; '
                                                       'review JSON-mode/thinking compatibility before rerunning')
    if m['done'] is not True:
        return 'METADATA_INCOMPLETE_REVIEW', 'Ollama response lacks done=true; review transport/API compatibility'
    if reason is None:
        return 'METADATA_INCOMPLETE_REVIEW', 'Missing done_reason; cannot establish whether output was truncated'
    if m['context_pressure_95pct_heuristic']:
        return 'CONTEXT_PRESSURE_REVIEW', 'Prompt+generated token usage >=95% of num_ctx; suspected context risk, not proof of truncation; hold for review'
    if (isinstance(m['eval_count'], int) and m['eval_count'] >= options['num_predict']
            and not (isinstance(response.get('message', {}).get('content'), str)
                     and response['message']['content'].strip())):
        return 'OUTPUT_TRUNCATED', 'Empty content at num_predict token budget'
    if reason and reason != 'stop':
        return 'CONFIGURATION_ERROR', f'Unexpected Ollama done_reason={reason!r}; inspect before scoring'
    return None, None


def record_api_metrics(run, stage, result, options):
    summary = api_metrics(result, options)
    pilot.write(run / f'{stage}_ollama_metrics.json', pilot.canon(summary) + '\n')
    return summary


def record_technical_failure(run, stage, reason, details, raw=None, metrics=None):
    return pilot._write_stage_event(run, stage, status='technical_failure', reason=reason,
                                    detail=details, raw=raw, api=metrics)


def run_stage(run, stage, args, options, think):
    prompt = (run / f'{stage}_PROMPT.txt').read_text(encoding='utf8')
    print(f'Calling {args.model} {args.world}/{stage} ...', flush=True)
    try:
        payload, result, content = ollama_chat(
            args.url, args.model, prompt, options, think, args.timeout,
            format_mode=getattr(args, 'format_mode', 'json'),
            schema=stage_schemas.stage_schema(stage) if getattr(args, 'format_mode', 'json') == 'schema' else None)
    except urllib.error.HTTPError as exc:
        reason = 'CONFIGURATION_ERROR' if exc.code in (400, 401, 403, 404, 422) else 'TRANSPORT_ERROR'
        try:
            body = exc.read().decode('utf8', 'replace')[:4000]     # Ollama puts the real cause here ({"error": "..."})
        except Exception:
            body = ''
        if 'token repeat limit reached' in body:
            # The model looped until the server cut it off: a model failure (counted), not an infrastructure error (excluded).
            pilot._write_stage_event(run, stage, status='model_format_failure', reason='MODEL_DEGENERATE_REPETITION',
                                     detail=f'HTTP {exc.code}; server said: {body}',
                                     api={'prompt_characters': len(prompt), 'options': options, 'think': think,
                                          'format_mode': getattr(args, 'format_mode', 'json')})
            print(f'{stage}: MODEL_DEGENERATE_REPETITION (server aborted a repetition loop); counted as a model failure.', flush=True)
            return False
        record_technical_failure(run, stage, reason, f'HTTP {exc.code}: {exc.reason}; server said: {body or "(no body)"}',
                                 metrics={'prompt_characters': len(prompt), 'options': options, 'think': think,
                                          'format_mode': getattr(args, 'format_mode', 'json')})
        print(f'{stage}: HTTP {exc.code}; server said: {body or "(no body)"}', flush=True)
        return False
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        record_technical_failure(run, stage, 'TRANSPORT_ERROR', repr(exc))
        return False
    except (ValueError, TypeError, KeyError) as exc:
        record_technical_failure(run, stage, 'CONFIGURATION_ERROR', repr(exc))
        return False

    pilot.write(run / f'{stage}_ollama_request.json', pilot.canon(payload) + '\n')
    pilot.write(run / f'{stage}_ollama_response.json', pilot.canon(result) + '\n')
    metrics = record_api_metrics(run, stage, result, options)
    reason, why = detect_technical_failure(result, options)
    if reason:
        record_technical_failure(run, stage, reason, why, raw=content, metrics=metrics)
        print(f'{stage}: TECHNICAL DEFERRED [{reason}]; not scored as model failure.', flush=True)
        return False

    # No silent fixes: every byte of content is handed to the same manual recorder.
    raw_filename = run / f'{stage}_RECEIVED_RAW_UNPROCESSED.txt'
    pilot.write(raw_filename, content)
    pilot.record(argparse.Namespace(run=str(run), stage=stage, response=str(raw_filename)))
    record_file = run / f'{stage}_record.json'
    record = json.loads(record_file.read_text(encoding='utf8'))
    record['api_metrics'] = metrics
    pilot.write(record_file, pilot.canon(record) + '\n')
    return record['valid_json']


def run_stage_with_retries(run, stage, args, options, think):
    """One stage, re-sampled (new seed, same frozen prompt, nothing edited) after a MODEL format failure, up to --max-attempts."""
    return pilot.run_with_retries(run, stage, lambda seed: run_stage(run, stage, args, dict(options, seed=seed), think),
                                  getattr(args, 'max_attempts', 1), options['seed'])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--world', required=True, choices=['R0','R1_L','R1_H','R2_L','R2_H','R3_L','R3_H'])
    ap.add_argument('--model', default='qwen3:4b-instruct')
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--url', default='http://127.0.0.1:11434')
    ap.add_argument('--temperature', type=float, default=0.6)
    ap.add_argument('--top-p', type=float, default=0.95)
    ap.add_argument('--top-k', type=int, default=20)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--num-ctx', type=int, default=32768)
    ap.add_argument('--num-predict', type=int, default=8192)
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--think', action='store_true', help='Enable model thinking (default: OFF for the main matrix)')
    ap.add_argument('--format-mode', choices=['json','none','schema'], default='schema',
                    help='Ollama response format, must be matched by smoke preflight')
    ap.add_argument('--max-attempts', type=int, default=3,
                    help='Max attempts per stage after a MODEL format failure (1 = never retry). Failures after the cap are UNFINISHED.')
    ap.add_argument('--skip-smoke-gate', action='store_true',
                    help='UNVERIFIED testing only; recorded in run metadata (not recommended)')
    args = ap.parse_args()
    options = {'temperature': args.temperature, 'top_p': args.top_p,
               'top_k': args.top_k, 'seed': args.seed,
               'num_ctx': args.num_ctx, 'num_predict': args.num_predict}
    think = bool(args.think)
    info = local_model_info(args.url, args.model)
    if not args.skip_smoke_gate:
        from ollama_smoke import assert_approved_smoke
        assert_approved_smoke(args.model, info, think, args.format_mode, options)
    else:
        print('WARNING: smoke preflight explicitly bypassed; experimental validity not established', flush=True)
    settings = json.dumps({'options': options, 'think': think, 'api_format': args.format_mode,
                            'endpoint': args.url, 'model_info': info,
                            'schema_sha256': stage_schemas.all_hashes() if args.format_mode == 'schema' else None,
                            'retry_policy': {'max_attempts_per_stage': args.max_attempts, 'seed_rule': f'base + {pilot.RETRY_SEED_STEP}*(attempt-1)', 'error_feedback': False},
                            'smoke_gate': 'BYPASSED_UNVERIFIED' if args.skip_smoke_gate else 'APPROVED'},
                          ensure_ascii=False)
    run = pilot.prepare(argparse.Namespace(world=args.world, run_id=args.run_id,
        model=args.model, model_version='ollama ' + json.dumps(info, ensure_ascii=False),
        settings=settings))

    a_ready = True
    for stage in ('A1','A2','A3'):
        if not run_stage_with_retries(run, stage, args, options, think):
            a_ready = False
            print(f'A stopped after {stage}; B is independent and will still run.', flush=True)
            break
    b_ready = run_stage_with_retries(run, 'B1', args, options, think)
    report = pilot.audit_run(run)
    print(f"Saved run: {run}\nA={report['paths']['A']['status']} "
          f"({report['paths']['A']['reason']} at {report['paths']['A']['stop_stage']}); "
          f"B={report['paths']['B']['status']} ({report['paths']['B']['reason']}).", flush=True)
    if not (a_ready and b_ready):
        raise SystemExit(2)


if __name__ == '__main__':
    main()
