#!/usr/bin/env python3
"""DIAGNOSTIC ONLY: does JSON-Schema constrained decoding (Ollama structured outputs) end cleanly where format=json looped?

Runs the real A1 -> A2 -> A3 chain and the independent B1 call for one world, for several seeds and several decoding modes:
  schema = generic structure-only schema from stage_schemas.py (identical to each prompt's own OUTPUT SCHEMA, no case-specific values)
  json   = format=json (the current pipeline setting)
  none   = no format constraint
Nothing is written to runs/, nothing is scored, no smoke approval is written, the model output is never repaired.
For every call it records finish reason, token counts, output length, share of whitespace, characters before the first '{',
how many complete top-level JSON objects the output contains, whether the pipeline's own parser and schema_validate accept it,
and the size of Ollama's separate "thinking" field. Everything lands in diagnostics/<timestamp>/.

  python3 scripts/ollama_schema_diagnose.py --world R1_L --seeds 4101 4102 4103 --modes schema json none
  python3 scripts/ollama_schema_diagnose.py --think-check        (a few seconds: is think=false really off?)
"""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pilot
import stage_schemas
import ollama_run
from ollama_run import ollama_chat

DIAG_DIR = pilot.BASE / 'diagnostics'
MODES = ('schema', 'json', 'none')


def count_top_level_objects(text):
    """Number of complete JSON objects the output contains when scanned left to right (1 is what the prompt asks for)."""
    dec, i, n = json.JSONDecoder(), 0, 0
    while True:
        i = text.find('{', i)
        if i < 0:
            return n
        try:
            _, end = dec.raw_decode(text, i)
            n += 1; i = end
        except json.JSONDecodeError:
            i += 1


def call_metrics(result, content, elapsed):
    msg = result.get('message') if isinstance(result.get('message'), dict) else {}
    thinking = msg.get('thinking') if isinstance(msg.get('thinking'), str) else ''
    first = content.find('{')
    return {'done_reason': result.get('done_reason'), 'eval_count': result.get('eval_count'), 'prompt_eval_count': result.get('prompt_eval_count'),
            'content_chars': len(content), 'whitespace_fraction': round(sum(c.isspace() for c in content) / len(content), 3) if content else None,
            'chars_before_first_brace': first if first >= 0 else None, 'complete_objects_in_output': count_top_level_objects(content),
            'thinking_chars': len(thinking), 'seconds': round(elapsed, 1)}


from smoke_chain import stage_prompt


def one_call(chat, stage, prompt, mode, folder):
    folder.mkdir(parents=True, exist_ok=True)
    pilot.write(folder / f'{stage}_prompt.txt', prompt)
    t0 = time.time()
    try:
        payload, result, content = chat(prompt, mode, stage_schemas.stage_schema(stage) if mode == 'schema' else None)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf8', 'replace')[:2000]
        pilot.write(folder / f'{stage}_http_error.txt', body)
        return {'stage': stage, 'ok': False, 'http_error': exc.code, 'server_said': body, 'seconds': round(time.time() - t0, 1)}, None
    pilot.write(folder / f'{stage}_request.json', pilot.canon(payload) + '\n')
    pilot.write(folder / f'{stage}_response.json', pilot.canon(result) + '\n')
    pilot.write(folder / f'{stage}_raw_content.txt', content)
    row = {'stage': stage, **call_metrics(result, content, time.time() - t0), 'schema_sha256': stage_schemas.schema_sha256(stage) if mode == 'schema' else None}
    try:
        obj, _, _ = pilot.parse_json_envelope(content)
        pilot.schema_validate(stage, obj)
        row['pipeline_parse_and_schema_validate'] = 'PASS'
    except (ValueError, TypeError) as exc:                      # json.JSONDecodeError is a ValueError
        obj = None
        row['pipeline_parse_and_schema_validate'] = f'FAIL: {str(exc)[:160]}'
    row['ok'] = obj is not None and result.get('done_reason') == 'stop'
    return row, obj


def verbatim_quote_share(obj, scenario):
    quoted = [r for r in obj['statuses'] if r['evidence_quote']]
    return None if not quoted else round(sum(r['evidence_quote'] in scenario for r in quoted) / len(quoted), 2)


def run_world(chat, world, mode, seed, outdir):
    m = pilot.read(f'model_inputs/{world}.json')
    base = outdir / f'{mode}_seed{seed}'
    rows, a1, a2 = [], None, None
    row, a1 = one_call(chat, 'A1', stage_prompt('A1', m), mode, base); rows.append(row)
    if a1 is not None:
        row, a2 = one_call(chat, 'A2', stage_prompt('A2', m, a1), mode, base); rows.append(row)
        if a2 is not None:
            rows[-1]['verbatim_quote_share'] = verbatim_quote_share(a2, m['customer_scenario'])
            row, _ = one_call(chat, 'A3', stage_prompt('A3', m, a1, a2), mode, base); rows.append(row)
        else:
            rows.append({'stage': 'A3', 'ok': None, 'note': 'not run: A2 did not produce a valid object'})
    else:
        rows += [{'stage': 'A2', 'ok': None, 'note': 'not run: A1 did not produce a valid object'}, {'stage': 'A3', 'ok': None, 'note': 'not run'}]
    row, _ = one_call(chat, 'B1', stage_prompt('B1', m), mode, base); rows.append(row)     # B1 is independent of A
    return {'mode': mode, 'seed': seed, 'stages': rows}


def think_check(url, model):
    """think=false vs true on a trivial question: is the thinking switch honoured by this Ollama build?"""
    out = []
    for think in (False, True):
        body = {'model': model, 'stream': False, 'think': think, 'messages': [{'role': 'user', 'content': 'What is 17 times 23? Answer with just the number.'}],
                'options': {'temperature': 0.6, 'seed': 1, 'num_predict': 800}}
        req = urllib.request.Request(url.rstrip('/') + '/api/chat', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        d = json.loads(urllib.request.urlopen(req, timeout=180).read()); msg = d['message']
        out.append({'think': think, 'content_head': (msg.get('content') or '')[:120], 'thinking_field_chars': len(msg.get('thinking') or ''),
                    'eval_count': d.get('eval_count'), 'content_contains_think_tag': '<think>' in (msg.get('content') or '')})
    return out


def print_table(runs):
    print(f"\n{'mode':7} {'seed':5} {'stage':5} {'done':7} {'eval':>5} {'chars':>6} {'ws%':>5} {'pre{':>5} {'objs':>4} {'think':>5}  pipeline parse + schema_validate")
    for r in runs:
        for s in r['stages']:
            if 'http_error' in s:
                print(f"{r['mode']:7} {r['seed']:<5} {s['stage']:5} HTTP {s['http_error']}: {s['server_said'][:90]}"); continue
            if s.get('ok') is None:
                print(f"{r['mode']:7} {r['seed']:<5} {s['stage']:5} {s['note']}"); continue
            print(f"{r['mode']:7} {r['seed']:<5} {s['stage']:5} {str(s['done_reason']):7} {str(s['eval_count']):>5} {s['content_chars']:>6} {str(round((s['whitespace_fraction'] or 0) * 100)):>5} "
                  f"{str(s['chars_before_first_brace']):>5} {s['complete_objects_in_output']:>4} {s['thinking_chars']:>5}  {s['pipeline_parse_and_schema_validate']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--world', default='R1_L'); ap.add_argument('--model', default='qwen3:4b-instruct'); ap.add_argument('--url', default='http://127.0.0.1:11434')
    ap.add_argument('--seeds', type=int, nargs='+', default=[4101, 4102, 4103]); ap.add_argument('--modes', nargs='+', choices=MODES, default=['schema', 'json', 'none'])
    ap.add_argument('--temperature', type=float, default=0.6); ap.add_argument('--top-p', type=float, default=0.95); ap.add_argument('--top-k', type=int, default=20)
    ap.add_argument('--num-ctx', type=int, default=32768); ap.add_argument('--num-predict', type=int, default=8192); ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--think-check', action='store_true')
    a = ap.parse_args()
    if a.think_check:
        for row in think_check(a.url, a.model):
            print(row)
        print('think=false honoured if: thinking_field_chars == 0, content has no <think> tag and eval_count is small (compare with think=true).')
        return
    outdir = DIAG_DIR / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    outdir.mkdir(parents=True)
    runs = []
    for mode in a.modes:
        for seed in a.seeds:
            opts = {'temperature': a.temperature, 'top_p': a.top_p, 'top_k': a.top_k, 'seed': seed, 'num_ctx': a.num_ctx, 'num_predict': a.num_predict}
            chat = lambda prompt, md, schema, _o=opts: ollama_chat(a.url, a.model, prompt, _o, False, a.timeout, format_mode=md, schema=schema)
            print(f'--- mode={mode} seed={seed} ---', flush=True)
            runs.append(run_world(chat, a.world, mode, seed, outdir))
    summary = {'diagnostic_only': True, 'not_scored': True, 'world': a.world, 'model': a.model, 'model_info': ollama_run.local_model_info(a.url, a.model), 'think_param_sent': False, 'time_utc': pilot.now(),
               'options_except_seed': {'temperature': a.temperature, 'top_p': a.top_p, 'top_k': a.top_k, 'num_ctx': a.num_ctx, 'num_predict': a.num_predict},
               'schema_sha256': stage_schemas.all_hashes(), 'runs': runs}
    pilot.write(outdir / 'summary.json', pilot.canon(summary) + '\n')
    print_table(runs)
    print(f'\nSaved: {outdir}/summary.json (+ every request, response and raw output). DIAGNOSTIC ONLY: nothing in runs/ was created or scored.')


if __name__ == '__main__':
    main()
