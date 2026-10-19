#!/usr/bin/env python3
"""LLM-judge labelling of the stage-wise worksheet, usable ONLY after calibration against human labels.

WHY: stage-wise evaluation (A1 coverage, A2 semantic accuracy, missing-information matching, reasoning
grounding) needs semantic judgements for every run; a human cannot label every run. The judge fills the SAME
worksheet a human would (same fields, same validator, same frozen-field tamper check) but writes it to a
separate file, STEPWISE_REVIEW_JUDGE.json, so human and judge labels are never mixed.

SAFEGUARDS
  * Narrow multiple-choice-style questions, temperature 0, no knowledge of model, pipeline or run ID (the prompts
    contain none of them), every prompt and raw response logged in the run folder (judge_logs/).
  * Output must pass the same validator as a human review PLUS the strict-judge rules (rationales, confidence, cross-field
    consistency, input provenance hashes); otherwise it is stored as *_REJECTED and unusable. A judge equal to the tested model is refused.
  * `agreement` compares judge and human on a stratified calibration sample and applies a PRE-REGISTERED rule
    (default: every label family needs >= --min-pairs paired labels and Cohen's kappa >= --min-kappa).
    Until a passing STEPWISE_AGREEMENT.json exists, `stepwise_review.py summary` marks judge numbers
    "UNCALIBRATED: do not report as a result".
  * The judge model should not be one of the models under test. If it is from the same family, say so in the paper.

COMMANDS
  sample    --runs-root R --n N --seed S      stratified calibration sample for human review
  judge     --run RUN --judge-model M         label one run (API; or --manual-dir DIR for offline copy-paste judging)
  judge-all --runs-root R --judge-model M     label every synthetic run that has no judge file yet
  agreement --runs-root R                     judge-vs-human agreement on runs that have both
"""
import argparse
import copy
import json
import os
import random
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

import pilot
import stepwise_review as sr

PROMPT_DIR = pilot.BASE / 'judge_prompts'
API_URL = 'https://api.anthropic.com/v1/messages'


class JudgeError(RuntimeError):
    pass


def anthropic_client(model, api_key, max_tokens=8192, temperature=0.0, timeout=300):
    """Minimal Messages API client (no SDK dependency). Returns call(system, prompt) -> (text, meta)."""
    def call(system, prompt):
        body = {'model': model, 'max_tokens': max_tokens, 'temperature': temperature, 'system': system,
                'messages': [{'role': 'user', 'content': prompt}]}
        request = urllib.request.Request(API_URL, data=json.dumps(body).encode('utf8'), method='POST',
                                         headers={'content-type': 'application/json', 'x-api-key': api_key,
                                                  'anthropic-version': '2023-06-01'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf8'))
        text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
        return text, {'model': data.get('model'), 'id': data.get('id'), 'usage': data.get('usage'), 'stop_reason': data.get('stop_reason')}
    return call


def openai_client(model, api_key, max_tokens=8192, temperature=0.0, timeout=300):
    """Chat Completions judge client (use when the model under test is from the Anthropic family). Parameter support for a given
    model is NOT assumed: an HTTP 400 surfaces as an error, so run `judge` on one run first."""
    def call(system, prompt):
        body = {'model': model, 'max_completion_tokens': max_tokens, 'temperature': temperature, 'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': prompt}]}
        request = urllib.request.Request('https://api.openai.com/v1/chat/completions', data=json.dumps(body).encode('utf8'), method='POST',
                                         headers={'content-type': 'application/json', 'authorization': f'Bearer {api_key}'})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf8'))
        choice = (data.get('choices') or [{}])[0]
        return (choice.get('message') or {}).get('content') or '', {'model': data.get('model'), 'id': data.get('id'), 'usage': data.get('usage'), 'stop_reason': choice.get('finish_reason')}
    return call


def _prompt(name):
    return (PROMPT_DIR / name).read_text(encoding='utf8')


def _policy_text():
    return '\n'.join(f"[{p['id']}] {p['html']}" for p in pilot.read('data/policy_dev38_13.json')['passages'])


def _j(x):
    return json.dumps(x, ensure_ascii=False, indent=1)


def build_map_prompt(template):
    ref = pilot.read('gold/reference_conditions_and_logic.json')
    cond = [{'gold_id': a['id'], 'proposition': a['proposition'], 'entity': a['entity'], 'kind': a['kind']} for a in ref['atoms']]
    atoms = [{'atom_id': a['atom_id'], 'proposition': a['proposition'], 'entity_scope': a['entity_scope'], 'source_ids': a['source_ids']}
             for a in template['A1']['model_atoms']]
    return (_prompt('map.txt') + f"\n=== POLICY_PASSAGES ===\n{_policy_text()}\n\n=== REFERENCE_CONDITIONS ===\n{_j(cond)}"
            f"\n\n=== REFERENCE_EXPRESSION ===\n{_j(template['A1']['logic']['reference_expression'])}"
            f"\n\n=== MODEL_ATOMS ===\n{_j(atoms)}\n\n=== MODEL_EXPRESSION ===\n{_j(template['A1']['logic']['model_expression'])}\n")


def build_state_prompt(template, review):
    scenario = pilot.read(f"model_inputs/{template['world']}.json")['customer_scenario']
    rows = {r['atom_id']: r for r in template['A2']['model_rows']}
    props = {c['gold_condition_id']: c['proposition'] for c in template['A1']['reference_conditions']}
    ids = {c['gold_condition_id']: c['matched_A1_atom_ids'] for c in review['A1']['reference_conditions']}
    items = []
    for it in template['A2']['items']:
        if ids.get(it['gold_condition_id']):
            items.append({'gold_condition_id': it['gold_condition_id'], 'reference_condition': props[it['gold_condition_id']],
                          'reference_status': it['reference_status'], 'reference_evidence_basis': it['reference_basis'],
                          'reference_evidence_quotes': it['reference_quotes'],
                          'model_rows': [rows[a] for a in ids[it['gold_condition_id']] if a in rows]})
    return items, (_prompt('state.txt') + f"\n=== CUSTOMER_SCENARIO ===\n{scenario}\n\n=== ITEMS ===\n{_j(items)}\n")


def build_final_prompt(template, out):
    ref = template['reference']
    props = {a['id']: a['proposition'] for a in pilot.read('gold/reference_conditions_and_logic.json')['atoms']}
    scenario = pilot.read(f"model_inputs/{template['world']}.json")['customer_scenario']
    shown = {'reference_decision': ref['reference_decision'],
             'reference_decisive_conditions': [{'id': a, 'proposition': props[a]} for a in ref['decisive_atoms']],
             'reference_missing_facts': ref['reference_missing_facts'],
             'not_needed_missing_information': ref['not_needed_missing_information']}
    answer = {'decision': out['decision'], 'justification': out['justification'],
              'conditional_answer_applicable': out['conditional_answer']['applicable'],
              'conditional_branches': out['conditional_answer']['branches_shown'],
              'listed_missing_facts': [{'index': m['index'], 'text': m['text']} for m in out['model_missing_facts']]}
    return _prompt('final.txt') + f"\n=== CUSTOMER_SCENARIO ===\n{scenario}\n\n=== REFERENCE ===\n{_j(shown)}\n\n=== MODEL_FINAL_ANSWER ===\n{_j(answer)}\n"


def _ask(client, prompt, logs, name):
    """One call plus ONE retry on unparsable output; every attempt is logged. No repair of content."""
    system = _prompt('system.txt')
    for attempt in (1, 2):
        text, meta = client(system, prompt)
        logs.mkdir(exist_ok=True)
        pilot.write(logs / f'{name}_attempt{attempt}_prompt.txt', prompt)
        pilot.write(logs / f'{name}_attempt{attempt}_response.txt', text)
        pilot.write(logs / f'{name}_attempt{attempt}_meta.json', pilot.canon(meta) + '\n')
        try:
            obj, _, _ = pilot.parse_json_envelope(text)
            if isinstance(obj, dict):
                return obj, meta
        except (ValueError, TypeError):
            pass
    raise JudgeError(f'{name}: judge returned no parsable JSON object after one retry')


def _bool(x, where):
    if not isinstance(x, bool):
        raise JudgeError(f'{where}: expected true/false, got {x!r}')
    return x


def _sentence(x, where):
    if not isinstance(x, str) or not x.strip():
        raise JudgeError(f'{where}: a non-empty text is required, got {x!r}')
    return x.strip()


def _enum(x, allowed, where):
    if x not in allowed:
        raise JudgeError(f'{where}: {x!r} is not one of {allowed}')
    return x


def _list(obj, key, where):
    v = obj.get(key) if isinstance(obj, dict) else None
    if not isinstance(v, list) or not all(isinstance(i, dict) for i in v):
        raise JudgeError(f'{where}: "{key}" must be a list of objects')
    return v


def _unique(rows, key, expected, where):
    """Each expected ID exactly once; nothing else."""
    got = {}
    for r in rows:
        k = r.get(key)
        if k in got:
            raise JudgeError(f'{where}: {k!r} appears more than once')
        got[k] = r
    unknown = set(got) - set(expected)
    if unknown:
        raise JudgeError(f'{where}: unknown ID(s) {sorted(map(str, unknown))}')
    missing = [k for k in expected if k not in got]
    if missing:
        raise JudgeError(f'{where}: no answer for {missing}')
    return got


def _call_meta(obj, where):
    conf = _enum(obj.get('confidence'), sr.CONFIDENCE, f'{where} confidence')
    uc = obj.get('uncertain_cases')
    if not isinstance(uc, list) or not all(isinstance(u, str) and u.strip() for u in uc):
        raise JudgeError(f'{where}: uncertain_cases must be a list of non-empty strings')
    return conf, [u.strip() for u in uc]


def fill_review(template, client, judge_model, logs, settings=None):
    review = copy.deepcopy(template)
    review['reviewer'] = f'LLM:{judge_model}'
    review['review_timestamp'] = pilot.now()
    metas, confs, unsure = [], [], []
    if review['A1'] is not None:
        obj, meta = _ask(client, build_map_prompt(template), logs, 'map'); metas.append(meta)
        gold_ids = [c['gold_condition_id'] for c in review['A1']['reference_conditions']]
        atom_ids = [a['atom_id'] for a in review['A1']['model_atoms']]
        checks = _unique(_list(obj, 'reference_checks', 'map'), 'gold_id', gold_ids, 'map reference_checks')
        for c in review['A1']['reference_conditions']:
            m = checks[c['gold_condition_id']]
            c['coverage'] = _enum(m.get('coverage'), sr.COVERAGE, f"map {c['gold_condition_id']} coverage")
            ids = m.get('model_atom_ids')
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise JudgeError(f"map {c['gold_condition_id']}: model_atom_ids must be a list of strings")
            c['matched_A1_atom_ids'] = ids
            c['rationale'] = _sentence(m.get('rationale'), f"map {c['gold_condition_id']} rationale")
        covered = {i for c in review['A1']['reference_conditions'] if c['coverage'] in ('FULL', 'PARTIAL') for i in c['matched_A1_atom_ids']}
        extras = _unique(_list(obj, 'extra_atoms', 'map'), 'model_atom_id', [i for i in atom_ids if i not in covered], 'map extra_atoms')
        for a in review['A1']['model_atoms']:
            if a['atom_id'] in covered:
                a['atom_assessment'] = 'MATCHES_REFERENCE'
            else:
                e = extras[a['atom_id']]
                a['atom_assessment'] = _enum(e.get('assessment'), sr.ATOM_ASSESSMENTS[1:], f"map atom {a['atom_id']} assessment")
                a['rationale'] = _sentence(e.get('rationale'), f"map atom {a['atom_id']} rationale")
        lg = review['A1']['logic']
        for key, field, allowed, issues in (('logic', 'logic_assessment', sr.LOGIC_VALUES, 'review_logic_issues'), ('scope', 'scope_assessment', sr.SCOPE_VALUES, 'review_scope_issues')):
            o = obj.get(key)
            if not isinstance(o, dict):
                raise JudgeError(f'map: "{key}" object missing')
            lg[field] = _enum(o.get('assessment'), allowed, f'map {key} assessment')
            iss = o.get('issues')
            if not isinstance(iss, list) or not all(isinstance(i, str) for i in iss):
                raise JudgeError(f'map {key}: issues must be a list of strings')
            lg[issues] = [i.strip() for i in iss if i.strip()]
        conf, uc = _call_meta(obj, 'map'); confs.append(conf); unsure += [f'A1: {u}' for u in uc]
    if review['A2'] is not None:
        items, prompt = build_state_prompt(template, review)
        quotes = {r['atom_id']: r['evidence_quote'] for r in template['A2']['model_rows']}
        if items:
            obj, meta = _ask(client, prompt, logs, 'state'); metas.append(meta)
            got = _unique(_list(obj, 'items', 'state'), 'gold_condition_id', [i['gold_condition_id'] for i in items], 'state items')
            conf, uc = _call_meta(obj, 'state'); confs.append(conf); unsure += [f'A2: {u}' for u in uc]
        else:
            got = {}
        for it in review['A2']['items']:
            cond = next(c for c in review['A1']['reference_conditions'] if c['gold_condition_id'] == it['gold_condition_id'])
            if cond['coverage'] != 'FULL':
                continue
            m = got[it['gold_condition_id']]
            it['status_correct'] = _bool(m.get('status_correct'), f"status_correct {it['gold_condition_id']}")
            has_quote = any(quotes.get(i, '').strip() for i in cond['matched_A1_atom_ids'])
            it['quote_supports_status'] = _bool(m.get('quote_supports_status'), f"quote_supports_status {it['gold_condition_id']}") if has_quote else None
            it['rationale'] = _sentence(m.get('reason'), f"state {it['gold_condition_id']} reason")
    for out in review['final_outputs']:
        obj, meta = _ask(client, build_final_prompt(template, out), logs, f"final_{out['label']}"); metas.append(meta)
        got = _unique(_list(obj, 'missing_facts', out['label']), 'index', [m['index'] for m in out['model_missing_facts']], f"{out['label']} missing_facts")
        for mf in out['model_missing_facts']:
            m = got[mf['index']]
            ids = m.get('matches_reference_fact_ids')
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise JudgeError(f"{out['label']} missing fact {mf['index']}: matches_reference_fact_ids must be a list of strings")
            mf['matches_reference_fact_ids'] = ids
            mf['unnecessary'] = None if ids else _bool(m.get('unnecessary'), f"unnecessary {mf['index']}")
        r = obj.get('reasoning')
        if not isinstance(r, dict):
            raise JudgeError(f"{out['label']}: reasoning object missing")
        for k in ('decisive_conditions_correct', 'asserts_unsupported_fact', 'scope_correct'):
            out['reasoning'][k] = _bool(r.get(k), k)
        out['reasoning']['rationale'] = _sentence(r.get('reason'), f"{out['label']} reasoning reason")
        if out['conditional_answer']['applicable']:
            c = obj.get('conditional_answer')
            if not isinstance(c, dict):
                raise JudgeError(f"{out['label']}: conditional_answer object missing (decision was NEED_MORE_INFORMATION)")
            out['conditional_answer']['branches_match_reference'] = _enum(c.get('branches_match_reference'), sr.BRANCH_MATCH, f"{out['label']} branches_match_reference")
            out['conditional_answer']['asserts_unsupported_condition'] = _bool(c.get('asserts_unsupported_condition'), f"{out['label']} asserts_unsupported_condition")
            out['conditional_answer']['rationale'] = _sentence(c.get('reason'), f"{out['label']} conditional_answer reason")
        conf, uc = _call_meta(obj, out['label']); confs.append(conf); unsure += [f"{out['label']}: {u}" for u in uc]
    review['confidence'] = max(confs, key=sr.CONFIDENCE.index) if confs else 'HIGH'   # the WORST (lowest) confidence of any call
    review['uncertain_cases'] = unsure
    review['judge_meta'] = {'judge_model_requested': judge_model,
                            'judge_models_returned': sorted({m.get('model') for m in metas if m.get('model')}),
                            'responses': metas, 'settings': settings or {},
                            'prompt_hashes': {p.name: pilot.hash_(p.read_text(encoding='utf8')) for p in sorted(PROMPT_DIR.glob('*.txt'))},
                            'inputs_provenance': template['provenance'],
                            'confidence_by_call': confs,
                            'note': 'LLM-judge labels. Not ground truth, not human-confirmed. Usable only if STEPWISE_AGREEMENT.json passed.'}
    return review


def judge_run(run, client, judge_model, force=False, settings=None):
    run = Path(run)
    dest = run / sr.REVIEW_JUDGE
    if dest.exists() and not force:
        return 'SKIPPED_EXISTS'
    tested = pilot.metadata(run).get('model', '')
    if str(judge_model).strip().lower() == str(tested).strip().lower():
        return f'REFUSED: the judge model ({judge_model}) is the model under test; use an independent judge'
    template = sr.build_template(run)
    try:
        review = fill_review(template, client, judge_model, run / 'judge_logs', settings)
        returned = set(review['judge_meta']['judge_models_returned'])
        if str(tested).strip().lower() in {str(m).strip().lower() for m in returned}:
            raise JudgeError(f'the API reports it answered with the tested model ({tested})')
        status, message = sr.validate_review(template, review, strict_judge=True)
        if status:
            raise JudgeError(f'{status}: {message}')
    except JudgeError as exc:
        pilot.write(run / 'STEPWISE_REVIEW_JUDGE_REJECTED.json', pilot.canon({'reason': str(exc), 'time_utc': pilot.now()}) + '\n')
        return f'REJECTED: {exc}'
    pilot.write(dest, pilot.canon(review) + '\n')
    return 'OK'


def manual_client(directory):
    """Offline judging without an API: every prompt is written to DIR/<hash>.prompt.txt; the judge's pure-JSON reply is read from
    DIR/<hash>.response.json. Re-run the same command after filling the files; the identical request is found by its hash.
    The same strict validator applies, so a pasted reply is held to exactly the same standard as an API reply."""
    directory = Path(directory)
    def call(system, prompt):
        key = pilot.hash_(system + '\n\n' + prompt)[:16]
        directory.mkdir(parents=True, exist_ok=True)
        pilot.write(directory / f'{key}.prompt.txt', 'SYSTEM:\n' + system + '\n\nUSER:\n' + prompt)
        reply = directory / f'{key}.response.json'
        if not reply.exists():
            raise ManualPending(f'Send {directory / (key + ".prompt.txt")} to the judge model; save its JSON reply as {reply}; run the same command again')
        return reply.read_text(encoding='utf8'), {'model': 'manual-offline', 'id': key}
    return call


class ManualPending(RuntimeError):
    pass


# ---------------------------------------------------------------- agreement ----------------------------------------------------------------
def cohen_kappa(pairs):
    """Cohen's kappa for any hashable labels (booleans or categories)."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    cats = {x for p in pairs for x in p}
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe)


def label_pairs(human, judge):
    fam = defaultdict(list)
    if human['A1'] is not None and judge['A1'] is not None:
        hc = {c['gold_condition_id']: c for c in human['A1']['reference_conditions']}
        jc = {c['gold_condition_id']: c for c in judge['A1']['reference_conditions']}
        for k in hc:
            fam['A1_coverage'].append((hc[k]['coverage'], jc[k]['coverage']))
            if hc[k]['coverage'] == jc[k]['coverage'] != 'MISSING':
                fam['A1_exact_atom_mapping_given_same_coverage'].append((True, set(hc[k]['matched_A1_atom_ids']) == set(jc[k]['matched_A1_atom_ids'])))
        ja = {a['atom_id']: a for a in judge['A1']['model_atoms']}
        for a in human['A1']['model_atoms']:
            fam['A1_atom_assessment'].append((a['atom_assessment'], ja[a['atom_id']]['atom_assessment']))
        fam['A1_logic_and_scope'].append((human['A1']['logic']['logic_assessment'], judge['A1']['logic']['logic_assessment']))
        fam['A1_logic_and_scope'].append((human['A1']['logic']['scope_assessment'], judge['A1']['logic']['scope_assessment']))
        if human['A2'] is not None and judge['A2'] is not None:
            ji = {i['gold_condition_id']: i for i in judge['A2']['items']}
            for i in human['A2']['items']:
                j = ji[i['gold_condition_id']]
                if i['status_correct'] is not None and j['status_correct'] is not None:
                    fam['A2_status_correct'].append((i['status_correct'], j['status_correct']))
                if i['quote_supports_status'] is not None and j['quote_supports_status'] is not None:
                    fam['A2_quote_supports_status'].append((i['quote_supports_status'], j['quote_supports_status']))
    jo = {o['label']: o for o in judge['final_outputs']}
    for ho in human['final_outputs']:
        o = jo[ho['label']]
        ref_ids = [f['reference_fact_id'] for f in human['reference']['reference_missing_facts']]
        for hm, jm in zip(ho['model_missing_facts'], o['model_missing_facts']):
            for rid in ref_ids:
                fam['final_missing_fact_matches_reference'].append((rid in hm['matches_reference_fact_ids'], rid in jm['matches_reference_fact_ids']))
            if hm['unnecessary'] is not None and jm['unnecessary'] is not None:
                fam['final_missing_fact_unnecessary'].append((hm['unnecessary'], jm['unnecessary']))
        for k in ('decisive_conditions_correct', 'asserts_unsupported_fact', 'scope_correct'):
            fam[f'final_reasoning_{k}'].append((ho['reasoning'][k], o['reasoning'][k]))
        if ho['conditional_answer']['applicable']:
            for k in ('branches_match_reference', 'asserts_unsupported_condition'):
                fam[f'final_conditional_{k}'].append((ho['conditional_answer'][k], o['conditional_answer'][k]))
    return fam


def agreement_report(pairs_by_family, min_kappa, min_pairs, runs, min_minority=3):
    families, ok = {}, True
    for name, pairs in sorted(pairs_by_family.items()):
        k = cohen_kappa(pairs)
        pct = sum(a == b for a, b in pairs) / len(pairs)
        humans = [a for a, _ in pairs]
        minority = len(humans) - max(humans.count(x) for x in set(humans))   # human labels that are not the most common label
        if len(pairs) < min_pairs:
            verdict = 'INSUFFICIENT_PAIRS'
        elif minority < min_minority:
            verdict = 'INSUFFICIENT_VARIATION'   # e.g. nearly everything "correct": high agreement would not show the judge can spot errors
        elif k is None:   # kappa undefined (no variation in either rater): never a pass, whatever --min-minority is
            verdict = 'INSUFFICIENT_VARIATION'
        else:
            verdict = 'PASS' if k >= min_kappa else 'FAIL'
        ok &= verdict == 'PASS'
        families[name] = {'pairs': len(pairs), 'percent_agreement': pct, 'cohen_kappa': k, 'human_minority_labels': minority, 'verdict': verdict}
    return {'rule': f'every label family needs >= {min_pairs} paired labels and kappa >= {min_kappa} '
                    f'and >= {min_minority} human labels outside the most common one (otherwise INSUFFICIENT_VARIATION: raw agreement on an almost-constant label is reported but never a pass; an undefined kappa is never a pass either); decided BEFORE looking at results',
            'min_kappa': min_kappa, 'min_pairs': min_pairs, 'min_minority': min_minority, 'calibration_runs': runs, 'families': families,
            'judge_acceptable': bool(ok and families),
            'note': 'Agreement on a small sample is itself uncertain; also report the disagreements (human label wins).'}


def agreement_cmd(a):
    root = Path(a.runs_root) if a.runs_root else pilot.BASE / 'runs'
    pairs, runs, judge_models = defaultdict(list), [], set()
    for meta_file in sorted(root.glob('*/*/run_metadata.json')):
        run = meta_file.parent
        if not ((run / sr.REVIEW).exists() and (run / sr.REVIEW_JUDGE).exists()):
            continue
        template = sr.build_template(run)
        human = json.loads((run / sr.REVIEW).read_text(encoding='utf8'))
        judge = json.loads((run / sr.REVIEW_JUDGE).read_text(encoding='utf8'))
        for who, rev in (('human', human), ('judge', judge)):
            status, message = sr.validate_review(template, rev, strict_judge=(who == 'judge'))
            if status:
                raise SystemExit(f'{run}: {who} review is not valid ({status}: {message}); fix it before computing agreement')
        for name, p in label_pairs(human, judge).items():
            pairs[name].extend(p)
        runs.append(f'{run.parent.name}/{run.name}'); judge_models.add(judge.get('judge_meta', {}).get('judge_model_requested'))
    if not runs:
        raise SystemExit('No run has both a human review and a judge review')
    report = agreement_report(pairs, a.min_kappa, a.min_pairs, runs, a.min_minority)
    report['judge_models'] = sorted(m for m in judge_models if m)
    dest = sr.REPORTS_DIR / sr.AGREEMENT
    pilot.write(dest, pilot.canon(report) + '\n')
    print(f'Wrote {dest}: judge_acceptable={report["judge_acceptable"]}')
    for name, f in report['families'].items():
        print(f"  {name}: n={f['pairs']} agreement={f['percent_agreement']:.2f} kappa={f['cohen_kappa']} -> {f['verdict']}")


# ---------------------------------------------------------------- sampling & CLI ----------------------------------------------------------------
def sample_runs(root, n, seed):
    groups = defaultdict(list)
    for meta_file in sorted(Path(root).glob('*/*/run_metadata.json')):
        run = meta_file.parent
        meta = pilot.metadata(run)
        if meta['world'] != 'R0':
            groups[(meta['model'], meta['world'])].append(run)
    rng = random.Random(seed)
    for g in groups.values():
        rng.shuffle(g)
    order = sorted(groups)
    rng.shuffle(order)
    picked = []
    while len(picked) < n and any(groups.values()):
        for key in order:
            if groups[key] and len(picked) < n:
                picked.append(groups[key].pop())
    return picked


def make_client(args):
    if getattr(args, 'manual_dir', None):
        return manual_client(args.manual_dir)
    env = args.api_key_env or ('OPENAI_API_KEY' if args.judge_provider == 'openai' else 'ANTHROPIC_API_KEY')
    key = os.environ.get(env)
    if not key:
        raise SystemExit(f'Set {env} (API key) to call the judge model')
    return (openai_client if args.judge_provider == 'openai' else anthropic_client)(args.judge_model, key)


def _settings(args):
    return {'mode': 'manual-offline' if getattr(args, 'manual_dir', None) else 'api', 'provider': getattr(args, 'judge_provider', None), 'temperature': 0.0, 'max_tokens': 8192}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('sample'); p.add_argument('--runs-root', default=None); p.add_argument('--n', type=int, required=True); p.add_argument('--seed', type=int, default=20261018)
    p = sub.add_parser('judge'); p.add_argument('--run', required=True)
    p = sub.add_parser('judge-all'); p.add_argument('--runs-root', default=None)
    for q in (sub.choices['judge'], sub.choices['judge-all']):
        q.add_argument('--judge-model', required=True, help='label of the judge model (API model ID, or the name of the model you pasted into for --manual-dir)')
        q.add_argument('--judge-provider', choices=['anthropic', 'openai'], default='anthropic'); q.add_argument('--api-key-env', default=None); q.add_argument('--force', action='store_true')
        q.add_argument('--manual-dir', default=None, help='offline mode: write prompts to this folder, read pasted JSON replies from it (no API call)')
    p = sub.add_parser('agreement'); p.add_argument('--runs-root', default=None)
    p.add_argument('--min-kappa', type=float, default=0.7); p.add_argument('--min-pairs', type=int, default=20); p.add_argument('--min-minority', type=int, default=3)
    # NB: 0 only relaxes the minority-count check; an undefined kappa still cannot pass
    a = ap.parse_args()
    if a.cmd == 'sample':
        for run in sample_runs(a.runs_root or pilot.BASE / 'runs', a.n, a.seed):
            print(run)
    elif a.cmd == 'judge':
        try:
            print(judge_run(a.run, make_client(a), a.judge_model, a.force, _settings(a)))
        except ManualPending as exc:
            print(f'WAITING FOR JUDGE REPLY: {exc}')
    elif a.cmd == 'judge-all':
        client = make_client(a)
        root = Path(a.runs_root) if a.runs_root else pilot.BASE / 'runs'
        results = defaultdict(int)
        for meta_file in sorted(root.glob('*/*/run_metadata.json')):
            run = meta_file.parent
            if pilot.metadata(run)['world'] == 'R0':
                continue
            try:
                res = judge_run(run, client, a.judge_model, a.force, _settings(a))
            except ManualPending as exc:
                print(f'WAITING FOR JUDGE REPLY: {exc}'); break
            results[res.split(':')[0]] += 1
            print(f'{run.parent.name}/{run.name}: {res}')
        print(dict(results))
    else:
        agreement_cmd(a)


if __name__ == '__main__':
    main()
