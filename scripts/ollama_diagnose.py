#!/usr/bin/env python3
"""Replay ONE stored stage prompt against Ollama under several settings to find why a call fails (e.g. HTTP 500).

Diagnostic only: nothing is recorded into the run, nothing is scored, no approval is written. Variants differ in ONE
respect from the failing configuration (format mode, context size); the server's error text is printed in full.

  python3 scripts/ollama_diagnose.py --run runs/<run_id>/<world> --stage A2 --model qwen3:4b-instruct
Then, on the Mac, also read the server log right after a failure:   tail -n 60 ~/.ollama/logs/server.log
"""
import argparse
import urllib.error
from pathlib import Path

from ollama_run import ollama_chat


def attempt(url, model, prompt, options, think, fmt, timeout):
    try:
        _, result, content = ollama_chat(url, model, prompt, options, think, timeout, format_mode=fmt)
        return f"OK  done_reason={result.get('done_reason')} prompt_tokens={result.get('prompt_eval_count')} generated={result.get('eval_count')} content_chars={len(content)}"
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode('utf8', 'replace')[:1500]
        except Exception:
            body = ''
        return f'HTTP {exc.code}: {body or exc.reason}'
    except Exception as exc:
        return f'{type(exc).__name__}: {exc}'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--run', required=True); ap.add_argument('--stage', default='A2'); ap.add_argument('--model', default='qwen3:4b-instruct')
    ap.add_argument('--url', default='http://127.0.0.1:11434'); ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--temperature', type=float, default=0.6); ap.add_argument('--top-p', type=float, default=0.95); ap.add_argument('--top-k', type=int, default=20)
    ap.add_argument('--seed', type=int, default=4101); ap.add_argument('--num-predict', type=int, default=8192)
    a = ap.parse_args()
    prompt = (Path(a.run) / f'{a.stage}_PROMPT.txt').read_text(encoding='utf8')
    print(f'{a.stage} prompt: {len(prompt)} characters, {len(prompt.split())} words\n')
    base = {'temperature': a.temperature, 'top_p': a.top_p, 'top_k': a.top_k, 'seed': a.seed, 'num_predict': a.num_predict}
    variants = [('as run: format=json, num_ctx=32768', 'json', 32768), ('format=none, num_ctx=32768', 'none', 32768),
                ('format=json, num_ctx=16384', 'json', 16384), ('format=json, num_ctx=8192', 'json', 8192),
                ('format=none, num_ctx=8192', 'none', 8192)]
    for label, fmt, ctx in variants:
        print(f'[{label}] ->', attempt(a.url, a.model, prompt, dict(base, num_ctx=ctx), False, fmt, a.timeout), flush=True)
    print('\nRead the pattern: failing only with format=json -> JSON-grammar problem in this Ollama build; failing at large num_ctx only -> memory;\n'
          'failing everywhere -> look at the server log; passing everywhere -> the failure depends on something else (e.g. a crashed runner that has since restarted).')


if __name__ == '__main__':
    main()
