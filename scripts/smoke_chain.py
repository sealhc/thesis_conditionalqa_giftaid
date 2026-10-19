#!/usr/bin/env python3
"""Four-stage compatibility probe shared by the Ollama and API smoke tests.

A smoke test must exercise everything a real run will send, not only A1: the A2 failure of 2026-10 passed an A1-only smoke test.
The chain is A1 -> A2 -> A3 (each fed the previous valid output, as in a real run) plus the independent B1, on the R0 input WITHOUT Gold.
A stage is OK when the call was technically complete AND the output parsed AND passed the pipeline's structural validator (first attempt, no retry).
This certifies that the CONFIGURATION lets the chain run. It does not evaluate correctness.
"""
import pilot


def stage_prompt(stage, m, a1=None, a2=None):
    if stage == 'A1':
        return (pilot.BASE / 'prompts/A1_extract.txt').read_text(encoding='utf8') + pilot.format_shared(m)
    if stage == 'B1':
        return (pilot.BASE / 'prompts/B1_direct.txt').read_text(encoding='utf8') + pilot.format_shared(m)
    return pilot.format_a2(m, a1) if stage == 'A2' else pilot.format_a3(m, a1, a2)


def run_chain(call, world='R0'):
    """call(stage, prompt) -> (text, meta) with meta = {'technical_failure': str|None, ...provider details...}; it may raise for transport errors
    (the caller decides what that means). Returns (passed, stage_rows)."""
    m = pilot.read(f'model_inputs/{world}.json')
    rows, objs = [], {}

    def one(stage, prompt):
        text, meta = call(stage, prompt)
        row = {'stage': stage, 'prompt_sha256': pilot.hash_(prompt), **meta}
        if meta.get('technical_failure'):
            row.update(ok=False, error=f"technical failure: {meta['technical_failure']}"); rows.append(row); return None
        try:
            obj, _, _ = pilot.parse_json_envelope(text)
            pilot.schema_validate(stage, obj)
            row['ok'] = True
        except (ValueError, TypeError) as exc:
            obj = None; row.update(ok=False, error=str(exc)[:200])
        rows.append(row)
        return obj

    objs['A1'] = one('A1', stage_prompt('A1', m))
    if objs['A1'] is not None:
        objs['A2'] = one('A2', stage_prompt('A2', m, objs['A1']))
        if objs['A2'] is not None:
            objs['A3'] = one('A3', stage_prompt('A3', m, objs['A1'], objs['A2']))
    objs['B1'] = one('B1', stage_prompt('B1', m))
    done = {r['stage'] for r in rows}
    for s in ('A1', 'A2', 'A3', 'B1'):
        if s not in done:
            rows.append({'stage': s, 'ok': False, 'error': 'not run: an earlier stage of the chain did not produce a valid object'})
    rows.sort(key=lambda r: ('A1', 'A2', 'A3', 'B1').index(r['stage']))
    return all(r['ok'] for r in rows), rows
