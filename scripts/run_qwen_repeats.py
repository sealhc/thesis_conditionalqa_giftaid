#!/usr/bin/env python3
"""Run independent Qwen repetitions. Keep running after any one failure.

Technical failures are preserved as retry-required attempts, never silently replaced.
The combined summary is produced at the end, even if one run is incomplete.
"""
import argparse
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--worlds', nargs='+', default=['R0','R1_L','R1_H','R2_L','R2_H','R3_L','R3_H'], choices=['R0','R1_L','R1_H','R2_L','R2_H','R3_L','R3_H'])
    p.add_argument('--model', default='qwen3:4b-instruct')
    p.add_argument('--url', default='http://127.0.0.1:11434')
    p.add_argument('--repeats', type=int, default=3)
    p.add_argument('--seed-start', type=int, default=4101)
    p.add_argument('--prefix', default=None, help='default: qwen_nothink_v7, or qwen_think_v7 with --think')
    p.add_argument('--think', action='store_true', help='Enable thinking (separate control arm); default OFF')
    p.add_argument('--format-mode', choices=['json','none','schema'], default='schema')
    p.add_argument('--temperature', type=float, default=0.6)
    p.add_argument('--top-p', type=float, default=.95)
    p.add_argument('--top-k', type=int, default=20)
    p.add_argument('--num-ctx', type=int, default=32768)
    p.add_argument('--num-predict', type=int, default=8192)
    p.add_argument('--timeout', type=int, default=900)
    p.add_argument('--max-attempts', type=int, default=3, help='per-stage attempts after a model format failure (1 = never retry)')
    args = p.parse_args()
    if args.prefix is None:
        args.prefix = 'qwen_think_v7' if args.think else 'qwen_nothink_v7'
    if args.repeats < 1:
        p.error('--repeats must be >= 1')
    # Fail fast: every planned (configuration, seed) pair must already be smoke-approved BEFORE any run starts.
    sys.path.insert(0, str(HERE))
    from ollama_run import local_model_info
    from ollama_smoke import assert_approved_smoke
    info = local_model_info(args.url, args.model)
    for rep in range(1, args.repeats + 1):
        options = {'temperature': args.temperature, 'top_p': args.top_p, 'top_k': args.top_k,
                   'seed': args.seed_start + rep - 1, 'num_ctx': args.num_ctx, 'num_predict': args.num_predict}
        assert_approved_smoke(args.model, info, bool(args.think), args.format_mode, options)
    failures = []
    for rep in range(1, args.repeats + 1):
        for world in args.worlds:
            run_id = f'{args.prefix}_rep{rep:02d}'
            cmd = [sys.executable, str(HERE / 'ollama_run.py'), '--world', world,
                   '--model', args.model, '--url', args.url, '--run-id', run_id,
                   '--seed', str(args.seed_start + rep - 1),
                   '--temperature', str(args.temperature), '--top-p', str(args.top_p),
                   '--top-k', str(args.top_k), '--num-ctx', str(args.num_ctx),
                   '--num-predict', str(args.num_predict), '--timeout', str(args.timeout),
                   '--format-mode', args.format_mode, '--max-attempts', str(args.max_attempts)]
            if args.think:
                cmd.append('--think')
            print(f'\n===== {world} repetition {rep}: {run_id} =====', flush=True)
            status = subprocess.run(cmd, check=False).returncode
            if status:
                failures.append({'run_id': run_id, 'world': world, 'process_exit_code': status})
    summary = subprocess.run([sys.executable, str(HERE / 'pilot.py'), 'summary'], check=False)
    print(f'Finished batch, {len(failures)} incomplete or failed runs. Raw records retained.', flush=True)
    if failures:
        print('Investigate and rerun any confirmed technical failures with NEW run IDs:', failures, flush=True)
    if failures or summary.returncode:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
