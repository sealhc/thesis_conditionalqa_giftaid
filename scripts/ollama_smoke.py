#!/usr/bin/env python3
"""Qwen/Ollama compatibility preflight. Makes REAL API calls ONLY on user's Mac.

Purpose: test actual format=json / think setting and terminal metadata before research.
Does NOT compare to Gold and does NOT certify policy/legal or semantic correctness.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import urllib.error
import urllib.request

import pilot
import smoke_chain
import stage_schemas
from ollama_run import ollama_chat, local_model_info, api_metrics, detect_technical_failure

REPORT_DIR = pilot.BASE / 'smoke_reports'
APPROVAL_DIR = REPORT_DIR / 'approved'   # one file per approved configuration (config id = hash of the config)
DEFAULT_SEEDS = [4101, 4102, 4103]       # must equal run_qwen_repeats: seed_start + repetition - 1


def approval_config(model, digest, think, format_mode, options):
    """Everything that must be identical between the smoke test and the real runs, EXCEPT the seed.

    Seeds are deliberately planned to differ across repetitions, so they are approved as a LIST
    (see assert_approved_smoke), not as one fixed value."""
    return {'model': model, 'digest': digest, 'think': bool(think), 'format_mode': format_mode,
            'num_ctx': options['num_ctx'], 'num_predict': options['num_predict'],
            'temperature': options['temperature'], 'top_p': options['top_p'], 'top_k': options['top_k'],
            'schemas': stage_schemas.all_hashes() if format_mode == 'schema' else None}


def config_id(config):
    return pilot.hash_(pilot.canon(config))[:16]


def ollama_version(url):
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/api/version', timeout=10) as response:
            data=json.loads(response.read().decode('utf8'))
            return data.get('version', 'UNKNOWN')
    except Exception as exc:
        return f'UNAVAILABLE ({type(exc).__name__})'


def smoke_command(model, think, format_mode, options):
    return (f"python3 scripts/ollama_smoke.py --model {model}{' --think' if think else ''} --format-mode {format_mode} "
            f"--num-ctx {options['num_ctx']} --num-predict {options['num_predict']} --temperature {options['temperature']} "
            f"--top-p {options['top_p']} --top-k {options['top_k']} --seeds {options['seed']}")


def assert_approved_smoke(model, model_info, think, format_mode, options):
    """Fail closed unless this exact sampling configuration AND this seed were smoke-tested and passed.

    Checked: model, digest, think, format mode, num_ctx, num_predict, temperature, top_p, top_k
    (all must equal the approved configuration) and the run's seed (must be in the approved seed list)."""
    digest = model_info.get('digest')
    if not digest or digest in ('UNAVAILABLE', 'NOT_FOUND_IN_INSTALLED_TAGS'):
        raise RuntimeError('Cannot verify local model digest; check ollama list / server health.')
    config = approval_config(model, digest, think, format_mode, options)
    path = APPROVAL_DIR / f'{config_id(config)}.json'
    if not path.exists():
        raise RuntimeError('No smoke approval for this exact configuration (model digest, think, format, num_ctx, '
                           'num_predict, temperature, top_p, top_k). Run, on the machine that will run the experiment: '
                           + smoke_command(model, think, format_mode, options) + f'  (configuration: {config!r})')
    approved = json.loads(path.read_text(encoding='utf8'))
    if not approved.get('passed') or approved.get('config') != config:
        raise RuntimeError(f'Approval file {path.name} is invalid or does not match the requested configuration.')
    if options['seed'] not in approved.get('approved_seeds', []):
        raise RuntimeError(f"Seed {options['seed']} was not part of the smoke test. Approved seeds: "
                           f"{approved.get('approved_seeds')}. Re-run smoke with --seeds including this seed.")


def probe(args, think, format_mode, seed):
    """One live FOUR-STAGE chain (A1, A2, A3, B1; R0 input, no Gold) for one seed. Passes only if every stage was technically complete and structurally valid."""
    options={'num_ctx':args.num_ctx, 'num_predict':args.num_predict,
             'temperature':args.temperature,'top_p':args.top_p,'top_k':args.top_k,
             'seed':seed}
    info=local_model_info(args.url,args.model)
    result={'time_utc':datetime.now(timezone.utc).isoformat(),
            'model':args.model,'digest':info.get('digest'),'model_info':info,
            'ollama_version':ollama_version(args.url),
            'think':think,'format_mode':format_mode,
            'schema_sha256': stage_schemas.all_hashes() if format_mode == 'schema' else None,
            'num_ctx':args.num_ctx,'num_predict':args.num_predict,
            'options':options,
            'probe_type':'chain_R0_A1_A2_A3_B1_without_gold',
            'passed':False,'evidence_is_not_gold_evaluation':True}
    suffix=('think' if think else 'no_think')+'_'+format_mode+f'_seed{seed}'
    tag=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    prefix=REPORT_DIR/f'{tag}_{suffix}'
    REPORT_DIR.mkdir(parents=True,exist_ok=True)

    def call(stage, prompt):
        pilot.write(prefix.with_name(f'{prefix.name}_{stage}_prompt.txt'),prompt)
        payload,response,raw=ollama_chat(args.url,args.model,prompt,options,think,args.timeout,format_mode=format_mode,
                                          schema=stage_schemas.stage_schema(stage) if format_mode=='schema' else None)
        pilot.write(prefix.with_name(f'{prefix.name}_{stage}_request.json'),pilot.canon(payload)+'\n')
        pilot.write(prefix.with_name(f'{prefix.name}_{stage}_response.json'),pilot.canon(response)+'\n')
        pilot.write(prefix.with_name(f'{prefix.name}_{stage}_raw_content.txt'),raw)
        technical,description=detect_technical_failure(response,options)
        metrics=api_metrics(response,options)
        return raw,{'technical_failure':technical,'technical_failure_detail':description,'done_reason':metrics['done_reason'],
                    'eval_count':metrics['eval_count'],'thinking_field_present':metrics['thinking_field_present']}
    try:
        passed,rows=smoke_chain.run_chain(call)
        result['stages']=rows; result['passed']=passed
    except urllib.error.HTTPError as exc:
        result['api_error']=f'HTTP {exc.code}: {exc.read().decode("utf8","replace")[:1000]}'
    except (urllib.error.URLError,TimeoutError,ConnectionError,ValueError,TypeError,KeyError) as exc:
        result['api_error']=repr(exc)
    result['report_file']=f'{prefix.name}_report.json'
    pilot.write(prefix.with_name(prefix.name+'_report.json'),pilot.canon(result)+'\n')
    per=' '.join(f"{r['stage']}={'ok' if r['ok'] else 'FAIL'}" for r in result.get('stages',[]))
    print(f'Smoke {suffix}: {"PASS" if result["passed"] else "FAIL"}; {per or result.get("api_error")}')
    print(f'  Detailed record: {prefix.name}_report.json')
    return result


def write_approval(args, think, format_mode, results):
    digest = results[0]['digest']
    options = {'num_ctx': args.num_ctx, 'num_predict': args.num_predict, 'temperature': args.temperature,
               'top_p': args.top_p, 'top_k': args.top_k}
    config = approval_config(args.model, digest, think, format_mode, options)
    APPROVAL_DIR.mkdir(parents=True, exist_ok=True)
    record = {'passed': True, 'time_utc': datetime.now(timezone.utc).isoformat(), 'config': config,
              'config_id': config_id(config), 'approved_seeds': [r['options']['seed'] for r in results],
              'seed_reports': [r['report_file'] for r in results], 'ollama_version': results[0]['ollama_version'],
              'probe_type': 'chain_R0_A1_A2_A3_B1_without_gold, one live chain per approved seed',
              'note': 'Compatibility preflight only. Not a Gold evaluation.'}
    path = APPROVAL_DIR / f"{record['config_id']}.json"
    pilot.write(path, pilot.canon(record) + '\n')
    return path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',default='qwen3:4b-instruct')
    parser.add_argument('--url',default='http://127.0.0.1:11434')
    parser.add_argument('--think',action='store_true',help='Smoke-test with thinking ON (default OFF)')
    parser.add_argument('--format-mode',choices=['json','none','schema'],default='schema',help='schema = structure-only JSON Schema per stage (stage_schemas.py); the same content is sent to every arm')
    parser.add_argument('--matrix',action='store_true',
                        help='DIAGNOSTIC ONLY: probe all four think/format combinations with the first seed. '
                             'Never writes an approval; run the formal single-configuration smoke test afterwards.')
    parser.add_argument('--num-ctx',type=int,default=32768)
    parser.add_argument('--num-predict',type=int,default=8192)
    parser.add_argument('--temperature',type=float,default=0.6)
    parser.add_argument('--top-p',type=float,default=0.95)
    parser.add_argument('--top-k',type=int,default=20)
    parser.add_argument('--seeds',type=int,nargs='+',default=DEFAULT_SEEDS,
                        help='Every planned repetition seed; each is probed once and then approved.')
    parser.add_argument('--timeout',type=int,default=900)
    args=parser.parse_args()
    if len(set(args.seeds)) != len(args.seeds):
        parser.error('--seeds must not repeat a seed')
    if args.matrix:
        combos=[(True,'json'),(False,'json'),(True,'none'),(False,'none')]
        reports=[probe(args,thinking,fmt,args.seeds[0]) for thinking,fmt in combos]
        print('MATRIX IS DIAGNOSTIC ONLY: no approval was written. Choose ONE common configuration and run the '
              'formal smoke test without --matrix.')
        raise SystemExit(0 if all(r['passed'] for r in reports) else 2)
    think=bool(args.think)
    reports=[probe(args,think,args.format_mode,seed) for seed in args.seeds]
    if all(r['passed'] for r in reports) and reports[0]['digest'] not in (None,'UNAVAILABLE','NOT_FOUND_IN_INSTALLED_TAGS'):
        path=write_approval(args,think,args.format_mode,reports)
        print(f'APPROVED configuration {path.stem} for seeds {args.seeds} ({path}).')
    else:
        print('NOT APPROVED: at least one seed failed, or the model digest could not be read. Existing approvals are untouched.')
        raise SystemExit(2)
    print('Compatibility test only: no A/B experiments or scoring were run.')


if __name__=='__main__':
    main()
