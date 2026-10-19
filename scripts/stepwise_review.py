#!/usr/bin/env python3
"""Stage-wise Gold evaluation: human semantic review interface + strictly validated scoring.

WHY THIS EXISTS
  Only the final label is compared automatically by pilot.py. Whether an A1 atom *means* a reference
  condition, whether an A2 status is *right for the right reason*, and whether a model's missing_facts
  *mean* the reference missing facts are semantic judgements. This module does NOT make them.
  It (1) generates a review worksheet from a finished run, (2) refuses to score until a named reviewer
  has completed every required judgement and has not altered any frozen field (Gold text or model
  output), and (3) computes metrics ONLY from those human judgements. Mechanical comparisons
  (exact status equality, policy-ID overlap) are reported separately and labelled as proxies.

  An LLM judge (judge_review.py) may fill the SAME worksheet into a separate file with validate_review(strict_judge=True);
  its numbers stay labelled UNCALIBRATED until judge-vs-human agreement passes. Human and judge labels are never mixed.

COMMANDS
  template --run RUN     write STEPWISE_REVIEW_TEMPLATE.json (+ STEPWISE_BLIND_KEY.json) into the run folder
  score    --run RUN     validate STEPWISE_REVIEW.json (copy of the template, filled in) and write STEPWISE_SCORE.json
  summary  [--runs-root] aggregate reviewed runs; pending runs are counted, never imputed
"""
import argparse
import copy
import json
import sys
from collections import defaultdict
from pathlib import Path

import pilot

SCHEMA = 'stepwise-review-v2'
COVERAGE = ('FULL', 'PARTIAL', 'MISSING', 'INCORRECT')
ATOM_ASSESSMENTS = ('MATCHES_REFERENCE', 'SUPPORTED_ADDITIONAL', 'REDUNDANT', 'UNSUPPORTED', 'AMBIGUOUS')
LOGIC_VALUES = ('EQUIVALENT', 'NOT_EQUIVALENT', 'UNDETERMINED')
SCOPE_VALUES = ('MATCH', 'MISMATCH', 'UNCLEAR')
CONFIDENCE = ('HIGH', 'MEDIUM', 'LOW')
BRANCH_MATCH = ('FULL', 'PARTIAL', 'NONE')   # conditional answer vs reference; only judged when the model answered NEED_MORE_INFORMATION
EDITABLE = {'matched_A1_atom_ids', 'coverage', 'atom_assessment', 'logic_assessment', 'scope_assessment',
            'review_logic_issues', 'review_scope_issues', 'rationale', 'confidence', 'uncertain_cases',
            'status_correct', 'quote_supports_status', 'matches_reference_fact_ids',
            'unnecessary', 'decisive_conditions_correct', 'asserts_unsupported_fact', 'scope_correct',
            'branches_match_reference', 'asserts_unsupported_condition',
            'notes', 'reviewer', 'review_timestamp', 'judge_meta'}
REVIEW = 'STEPWISE_REVIEW.json'
TEMPLATE = 'STEPWISE_REVIEW_TEMPLATE.json'
KEY = 'STEPWISE_BLIND_KEY.json'
SCORE = 'STEPWISE_SCORE.json'
REVIEW_JUDGE = 'STEPWISE_REVIEW_JUDGE.json'    # LLM-judge labels; never mixed into the human files
SCORE_JUDGE = 'STEPWISE_SCORE_JUDGE.json'
AGREEMENT = 'STEPWISE_AGREEMENT.json'
REPORTS_DIR = pilot.BASE / 'reports'


def _frozen(obj):
    """Remove everything the reviewer may edit; what remains is Gold text and model output."""
    if isinstance(obj, dict):
        return {k: _frozen(v) for k, v in obj.items() if k not in EDITABLE}
    if isinstance(obj, list):
        return [_frozen(x) for x in obj]
    return obj


def _stage(run, stage):
    return pilot.read_run_json(run, stage) if pilot._stage_state(run, stage)['has_parsed'] else None


def _gold(world):
    sw = pilot.read('gold/STEPWISE_GOLD_REVIEW.json')[world]
    g = pilot.read('gold/gold_per_run.json')['scenario_statuses'][world]
    ref = pilot.read('gold/reference_conditions_and_logic.json')
    rel = g['outcome_relevance']
    decisive = [a for a, v in rel.items() if v == 'decisive'] or [a for a, v in rel.items() if v == 'required_for_yes']
    facts = [{'reference_fact_id': m['atom'], 'fact': m['fact'], 'effect': m['effect']}
             for m in g['G4_missing_information']['must_identify']]
    policy_ids = sorted({s for c in sw['A1_reference_conditions'] for s in c['policy_source_ids']})
    return sw, g, ref, decisive, facts, policy_ids


def _blind_order(run, meta):
    flip = int(pilot.hash_(f"{meta['run_id']}:{meta['world']}")[:2], 16) % 2
    return [('A', 'A3'), ('B', 'B1')] if flip == 0 else [('B', 'B1'), ('A', 'A3')]


def _provenance(run):
    """Content hashes of everything the worksheet is built from. They sit in a FROZEN field, so a worksheet (human or judge)
    made from a different A1/A2/A3/B1 output or a different Gold file than the one on disk now is rejected as stale or tampered."""
    run = Path(run)
    stages = {st: pilot.hash_((run / f'{st}_parsed.json').read_text(encoding='utf8'))
              for st in ('A1', 'A2', 'A3', 'B1') if (run / f'{st}_parsed.json').exists()}
    gold = {n: pilot.hash_((pilot.BASE / 'gold' / n).read_text(encoding='utf8'))
            for n in ('STEPWISE_GOLD_REVIEW.json', 'gold_per_run.json', 'reference_conditions_and_logic.json')}
    return {'run_stage_sha256': stages, 'gold_sha256': gold}


def build_template(run):
    run = Path(run)
    meta = pilot.metadata(run)
    world = meta['world']
    if world == 'R0':
        raise ValueError('R0 has no researcher stage-wise Gold; use the R0 native semantic review in pilot.py')
    sw, g, ref, decisive, facts, policy_ids = _gold(world)
    a1, a2 = _stage(run, 'A1'), _stage(run, 'A2')
    entity = {a['id']: a['entity'] for a in pilot.read('gold/reference_conditions_and_logic.json')['atoms']}
    t = {'schema_version': SCHEMA, 'world': world, 'run_id': meta['run_id'],
         'provenance': _provenance(run),
         'instructions': ('Fill every null field you are shown. Judge MEANING, not wording or IDs. Do not edit any other field: '
                          'Gold text and model outputs are frozen and a changed copy is rejected. Final answers are labelled F1/F2 '
                          'in a hidden order; do not open STEPWISE_BLIND_KEY.json until you have finished. '
                          'A1 coverage per reference condition: FULL = model atom(s) together state the same condition, same entity, same polarity; '
                          'PARTIAL = only part of it, or too broad/narrow, or one half of a conjunction; INCORRECT = an atom aims at it but says something '
                          'different (wrong entity, polarity or requirement); MISSING = nothing addresses it. List the atom IDs for FULL/PARTIAL/INCORRECT, none for MISSING. '
                          'Every model atom gets MATCHES_REFERENCE (it is in a FULL/PARTIAL list), SUPPORTED_ADDITIONAL, REDUNDANT, UNSUPPORTED or AMBIGUOUS. status_correct: does the model status for the matched atom(s) '
                          'equal the reference status AND rest on what the scenario actually says. '
                          'unnecessary: is a listed missing fact irrelevant to the reference outcome.'),
         'reviewer': '', 'review_timestamp': '', 'confidence': None, 'uncertain_cases': [],
         'reference': {'reference_logic': ref['reference_logic'], 'reference_decision': g['reference_decision'],
                       'decisive_atoms': decisive, 'reference_missing_facts': facts,
                       'not_needed_missing_information': g['G4_missing_information']['not_needed']},
         'A1': None, 'A2': None, 'final_outputs': []}
    if a1 is not None:
        t['A1'] = {
            'reference_conditions': [{'gold_condition_id': c['id'], 'proposition': c['proposition'],
                                      'entity': entity[c['id']], 'policy_source_ids': c['policy_source_ids'],
                                      'matched_A1_atom_ids': [], 'coverage': None, 'rationale': '', 'notes': ''}
                                     for c in sw['A1_reference_conditions']],
            'model_atoms': [{'atom_id': x['id'], 'proposition': x['proposition'], 'entity_scope': x['entity_scope'],
                             'source_ids': x['source_ids'], 'atom_assessment': None, 'rationale': '', 'notes': ''} for x in a1['atoms']],
            'logic': {'model_expression': a1['eligibility_expression'], 'reference_expression': ref['reference_logic'],
                      'logic_assessment': None, 'review_logic_issues': [], 'scope_assessment': None, 'review_scope_issues': [], 'notes': ''}}
    if a1 is not None and a2 is not None:
        rows = {r['id']: r for r in a2['statuses']}
        t['A2'] = {'model_rows': [{'atom_id': k, 'status': v['status'], 'evidence_quote': v['evidence_quote'],
                                   'justification': v['justification']} for k, v in rows.items()],
                   'items': [{'gold_condition_id': r['id'], 'reference_status': r['expected_status'],
                              'reference_basis': r['evidence_basis'], 'reference_quotes': r['verbatim_evidence'],
                              'status_correct': None, 'quote_supports_status': None, 'rationale': '', 'notes': ''}
                             for r in sw['A2_reference_statuses']]}
    key = {}
    for n, (path, stage) in enumerate(_blind_order(run, meta), start=1):
        out = _stage(run, stage)
        if out is None:
            continue
        label = f'F{n}'
        key[label] = {'pipeline': path, 'stage': stage}
        t['final_outputs'].append({
            'label': label, 'decision': out['decision'], 'justification': out['justification'],
            'supporting_policy_ids': out['supporting_policy_ids'],
            'model_missing_facts': [{'index': i, 'text': s, 'matches_reference_fact_ids': [], 'unnecessary': None}
                                    for i, s in enumerate(out['missing_facts'])],
            'reasoning': {'decisive_conditions_correct': None, 'asserts_unsupported_fact': None, 'scope_correct': None, 'rationale': '', 'notes': ''},
            # Conditional-answer quality is judged ONLY when the model itself answered NEED_MORE_INFORMATION (design decision v9.7):
            # a decided answer's conditions are its decisive conditions, already covered by reasoning.decisive_conditions_correct.
            'conditional_answer': {'applicable': out['decision'] == 'NEED_MORE_INFORMATION',
                                   'branches_shown': out.get('conditional_branches', []),
                                   'branches_match_reference': None, 'asserts_unsupported_condition': None, 'rationale': '', 'notes': ''}})
    pilot.write(run / TEMPLATE, pilot.canon(t) + '\n')
    pilot.write(run / KEY, pilot.canon(key) + '\n')
    return t


def _is_bool(x):
    return isinstance(x, bool)


def _text(x):
    return isinstance(x, str) and bool(x.strip())


def _issues_ok(x):
    return isinstance(x, list) and bool(x) and all(_text(i) for i in x)


def validate_review(template, review, strict_judge=False):
    """Return (None, None) if complete, internally consistent and untampered, else (status, message).
    strict_judge=True (used for LLM-judge worksheets) additionally demands a rationale for every judgement,
    a valid confidence and well-formed uncertain_cases."""
    if _frozen(review) != _frozen(template):
        return 'INVALID_REVIEW', 'A frozen field (Gold text, reference, model output or provenance hash) differs from the generated template'
    if not str(review.get('reviewer', '')).strip():
        return 'INCOMPLETE_REVIEW', 'reviewer name is empty'
    if strict_judge:
        if review.get('confidence') not in CONFIDENCE:
            return 'INVALID_REVIEW', f"confidence must be one of {CONFIDENCE}"
        uc = review.get('uncertain_cases')
        if not isinstance(uc, list) or not all(_text(u) for u in uc):
            return 'INVALID_REVIEW', 'uncertain_cases must be a list of non-empty strings'
    elif review.get('confidence') not in (None,) + CONFIDENCE:
        return 'INVALID_REVIEW', 'confidence, if set, must be HIGH, MEDIUM or LOW'
    if template['A1'] is not None:
        atoms = {x['atom_id'] for x in review['A1']['model_atoms']}
        in_full_partial, in_incorrect = set(), set()
        for c in review['A1']['reference_conditions']:
            cov, ids, gid = c['coverage'], c['matched_A1_atom_ids'], c['gold_condition_id']
            if cov is None:
                return 'INCOMPLETE_REVIEW', f"A1 {gid}: coverage not set"
            if cov not in COVERAGE:
                return 'INVALID_REVIEW', f"A1 {gid}: coverage must be one of {COVERAGE}"
            if not isinstance(ids, list) or any(i not in atoms for i in ids) or len(set(ids)) != len(ids):
                return 'INVALID_REVIEW', f"A1 {gid}: unknown or repeated atom ID in matched_A1_atom_ids"
            if cov == 'MISSING' and ids:
                return 'INVALID_REVIEW', f"A1 {gid}: MISSING must have no matched atoms"
            if cov != 'MISSING' and not ids:
                return 'INVALID_REVIEW', f"A1 {gid}: {cov} needs at least one matched atom"
            if strict_judge and not _text(c['rationale']):
                return 'INVALID_REVIEW', f"A1 {gid}: rationale is empty"
            (in_incorrect if cov == 'INCORRECT' else in_full_partial if cov in ('FULL', 'PARTIAL') else set()).update(ids)
        for x in review['A1']['model_atoms']:
            v, aid = x['atom_assessment'], x['atom_id']
            if v is None:
                return 'INCOMPLETE_REVIEW', f"A1 atom {aid}: atom_assessment not set"
            if v not in ATOM_ASSESSMENTS:
                return 'INVALID_REVIEW', f"A1 atom {aid}: atom_assessment must be one of {ATOM_ASSESSMENTS}"
            if aid in in_full_partial and v != 'MATCHES_REFERENCE':
                return 'INVALID_REVIEW', f"A1 atom {aid}: it is matched to a reference condition (FULL/PARTIAL) so it must be MATCHES_REFERENCE, not {v}"
            if aid not in in_full_partial and v == 'MATCHES_REFERENCE':
                return 'INVALID_REVIEW', f"A1 atom {aid}: MATCHES_REFERENCE but it is not in the matched list of any FULL/PARTIAL condition"
            if aid in in_incorrect and aid not in in_full_partial and v not in ('UNSUPPORTED', 'AMBIGUOUS'):
                return 'INVALID_REVIEW', f"A1 atom {aid}: it is the INCORRECT attempt at a reference condition, so it can only be UNSUPPORTED or AMBIGUOUS"
            if strict_judge and v != 'MATCHES_REFERENCE' and not _text(x['rationale']):
                return 'INVALID_REVIEW', f"A1 atom {aid}: rationale is empty"
        lg = review['A1']['logic']
        if lg['logic_assessment'] is None or lg['scope_assessment'] is None:
            return 'INCOMPLETE_REVIEW', 'A1 logic/scope assessments not set'
        if lg['logic_assessment'] not in LOGIC_VALUES or lg['scope_assessment'] not in SCOPE_VALUES:
            return 'INVALID_REVIEW', f'A1 logic_assessment must be one of {LOGIC_VALUES}, scope_assessment one of {SCOPE_VALUES}'
        for field, bad in (('review_logic_issues', 'NOT_EQUIVALENT'), ('review_scope_issues', 'MISMATCH')):
            val = lg['logic_assessment'] if 'logic' in field else lg['scope_assessment']
            if val == bad and not _issues_ok(lg[field]):
                return 'INVALID_REVIEW', f'A1 {val}: {field} must list the concrete problem(s)'
            if not isinstance(lg[field], list):
                return 'INVALID_REVIEW', f'A1 {field} must be a list'
    if template['A2'] is not None:
        matched = {c['gold_condition_id']: c for c in review['A1']['reference_conditions']}
        quotes = {r['atom_id']: r['evidence_quote'] for r in review['A2']['model_rows']}
        for it in review['A2']['items']:
            c = matched[it['gold_condition_id']]
            if c['coverage'] == 'FULL':
                if not _is_bool(it['status_correct']):
                    return 'INCOMPLETE_REVIEW', f"A2 {it['gold_condition_id']}: status_correct not set"
                has_quote = any(quotes.get(i, '').strip() for i in c['matched_A1_atom_ids'])
                if has_quote and not _is_bool(it['quote_supports_status']):
                    return 'INCOMPLETE_REVIEW', f"A2 {it['gold_condition_id']}: quote_supports_status not set"
                if not has_quote and it['quote_supports_status'] is not None:
                    return 'INVALID_REVIEW', f"A2 {it['gold_condition_id']}: no model quote, quote_supports_status must stay null"
                if strict_judge and not _text(it['rationale']):
                    return 'INVALID_REVIEW', f"A2 {it['gold_condition_id']}: rationale is empty"
            elif it['status_correct'] is not None or it['quote_supports_status'] is not None:
                return 'INVALID_REVIEW', f"A2 {it['gold_condition_id']}: A1 coverage is not FULL, so A2 is not scorable and must stay null"
    ref_ids = {f['reference_fact_id'] for f in template['reference']['reference_missing_facts']}
    for out in review['final_outputs']:
        for mf in out['model_missing_facts']:
            ids = mf['matches_reference_fact_ids']
            if not isinstance(ids, list) or any(i not in ref_ids for i in ids):
                return 'INVALID_REVIEW', f"{out['label']} missing fact {mf['index']}: unknown reference fact ID"
            if ids and mf['unnecessary'] not in (False, None):
                return 'INVALID_REVIEW', f"{out['label']} missing fact {mf['index']}: a matched fact cannot be 'unnecessary'"
            if not ids and not _is_bool(mf['unnecessary']):
                return 'INCOMPLETE_REVIEW', f"{out['label']} missing fact {mf['index']}: unnecessary not set"
        r = out['reasoning']
        if not all(_is_bool(r[k]) for k in ('decisive_conditions_correct', 'asserts_unsupported_fact', 'scope_correct')):
            return 'INCOMPLETE_REVIEW', f"{out['label']}: reasoning judgements not all set"
        if strict_judge and not _text(r['rationale']):
            return 'INVALID_REVIEW', f"{out['label']}: reasoning rationale is empty"
        ca = out['conditional_answer']
        if not ca['applicable']:
            if ca['branches_match_reference'] is not None or ca['asserts_unsupported_condition'] is not None:
                return 'INVALID_REVIEW', f"{out['label']}: conditional_answer is not applicable (decision is not NEED_MORE_INFORMATION) and must stay null"
        else:
            if ca['branches_match_reference'] not in BRANCH_MATCH or not _is_bool(ca['asserts_unsupported_condition']):
                return 'INCOMPLETE_REVIEW' if ca['branches_match_reference'] is None or ca['asserts_unsupported_condition'] is None else 'INVALID_REVIEW', \
                    f"{out['label']}: conditional_answer judgements not (validly) set; branches_match_reference must be one of {BRANCH_MATCH}"
            if strict_judge and not _text(ca['rationale']):
                return 'INVALID_REVIEW', f"{out['label']}: conditional_answer rationale is empty"
    return None, None


def _ratio(n, d):
    return {'numerator': n, 'denominator': d, 'rate': (n / d) if d else None}


def compute_score(run, template, review, source='human'):
    run = Path(run)
    meta = pilot.metadata(run)
    world = meta['world']
    _, g, _, decisive, facts, policy_ids = _gold(world)
    key = json.loads((run / KEY).read_text(encoding='utf8'))
    s = {'schema_version': SCHEMA, 'status': 'MANUALLY_REVIEWED' if source == 'human' else 'LLM_JUDGE_LABELLED', 'source': source, 'reviewer': review['reviewer'], 'world': world,
         'run_id': meta['run_id'], 'model': meta['model'], 'cohort_settings': pilot.settings_for_cohort(meta.get('inference_settings')),
         'basis': ('Every semantic number below comes from human judgements in STEPWISE_REVIEW.json. "mechanical_proxies" do not.' if source == 'human'
                   else 'Every semantic number below is an LLM-judge label (STEPWISE_REVIEW_JUDGE.json), valid only if STEPWISE_AGREEMENT.json passed. "mechanical_proxies" are not judge labels.'),
         'confidence': review.get('confidence'), 'uncertain_cases': review.get('uncertain_cases', [])}
    s['needs_human_check'] = bool(source == 'judge' and (
        review.get('confidence') != 'HIGH' or review.get('uncertain_cases')
        or (review['A1'] is not None and (review['A1']['logic']['logic_assessment'] == 'UNDETERMINED' or review['A1']['logic']['scope_assessment'] == 'UNCLEAR'
                                          or any(a['atom_assessment'] == 'AMBIGUOUS' for a in review['A1']['model_atoms'])))))
    mapping = {}
    if review['A1'] is not None:
        conds = review['A1']['reference_conditions']
        mapping = {c['gold_condition_id']: c['matched_A1_atom_ids'] for c in conds if c['coverage'] == 'FULL'}
        atoms = review['A1']['model_atoms']
        lg = review['A1']['logic']
        count = lambda vals, key: {v: sum(1 for x in vals if x == v) for v in key}
        supported = ('MATCHES_REFERENCE', 'SUPPORTED_ADDITIONAL', 'REDUNDANT')
        s['A1'] = {'reference_condition_recall': _ratio(len(mapping), len(conds)),     # STRICT: FULL only
                   'reference_condition_recall_lenient_full_or_partial': _ratio(sum(1 for c in conds if c['coverage'] in ('FULL', 'PARTIAL')), len(conds)),
                   'coverage_counts': count([c['coverage'] for c in conds], COVERAGE),
                   'model_atom_precision_policy_supported': _ratio(sum(1 for a in atoms if a['atom_assessment'] in supported), len(atoms)),   # AMBIGUOUS counts as not supported
                   'atom_assessment_counts': count([a['atom_assessment'] for a in atoms], ATOM_ASSESSMENTS),
                   'logic_assessment': lg['logic_assessment'], 'scope_assessment': lg['scope_assessment'],
                   'logic_equivalent': {'EQUIVALENT': True, 'NOT_EQUIVALENT': False}.get(lg['logic_assessment']),
                   'entity_scope_match': {'MATCH': True, 'MISMATCH': False}.get(lg['scope_assessment']),
                   'logic_issues': lg['review_logic_issues'], 'scope_issues': lg['review_scope_issues'],
                   'unmatched_reference_conditions': [c['gold_condition_id'] for c in conds if c['coverage'] != 'FULL']}
    if review['A2'] is not None:
        rows = {r['atom_id']: r for r in review['A2']['model_rows']}
        items = review['A2']['items']
        scorable = [i for i in items if i['gold_condition_id'] in mapping]
        mech_ok, disagree = 0, []
        for i in scorable:
            ok = all(rows.get(a, {}).get('status') == i['reference_status'] for a in mapping[i['gold_condition_id']])
            mech_ok += ok
            if ok != i['status_correct']:
                disagree.append({'gold_condition_id': i['gold_condition_id'], 'mechanical_exact_status_equal': ok,
                                 'human_status_correct': i['status_correct'],
                                 'note': 'Reviewer and exact-equality disagree; human judgement is the one used. Inspect.'})
        quoted = [i for i in scorable if i['quote_supports_status'] is not None]
        s['A2'] = {'status_semantic_accuracy_on_matched_conditions': _ratio(sum(i['status_correct'] for i in scorable), len(scorable)),
                   'evidence_quote_support': _ratio(sum(i['quote_supports_status'] for i in quoted), len(quoted)),
                   'not_scorable_because_A1_missed_condition': len(items) - len(scorable),
                   'mechanical_proxy_exact_status_equality': _ratio(mech_ok, len(scorable)),
                   'reviewer_vs_mechanical_disagreements': disagree}
    s['final_outputs'] = {}
    for out in review['final_outputs']:
        pipe = key[out['label']]['pipeline']
        mfs = out['model_missing_facts']
        matched_ref = {r for mf in mfs for r in mf['matches_reference_fact_ids']}
        ref_ids = [f['reference_fact_id'] for f in facts]
        r = out['reasoning']
        grounded = r['decisive_conditions_correct'] and not r['asserts_unsupported_fact'] and r['scope_correct']
        correct = out['decision'] == g['reference_decision']
        ca = out['conditional_answer']
        entry = {'label': out['label'], 'decision': out['decision'], 'decision_correct': correct,
                 'conditional_answer': ({'applicable': True, 'branches_match_reference': ca['branches_match_reference'],
                                         'asserts_unsupported_condition': ca['asserts_unsupported_condition']} if ca['applicable'] else
                                        {'applicable': False, 'note': 'Not scored: the model did not answer NEED_MORE_INFORMATION. Its conditions are its decisive conditions (reasoning_grounded). Report the share of runs where this was scored.'}),
                 'reasoning_grounded': bool(grounded),
                 'correct_for_right_reasons': bool(correct and grounded),
                 'correct_for_right_reasons_basis': f'decision_correct is a mechanical label comparison; reasoning_grounded is {source}-assessed, so this field is weaker evidence than decision_correct and must not be pooled with it as one accuracy',
                 'missing_information': {'reference_fact_recall': _ratio(len(matched_ref), len(ref_ids)) if ref_ids else None,
                                         'listed_facts': len(mfs),
                                         'listed_facts_unnecessary': sum(1 for mf in mfs if mf['unnecessary'] is True),
                                         'precision_not_unnecessary': _ratio(sum(1 for mf in mfs if mf['unnecessary'] is not True), len(mfs)) if mfs else None},
                 'mechanical_proxies': {'cited_policy_ids_overlap_reference': sorted(set(out['supporting_policy_ids']) & set(policy_ids)),
                                        'reference_policy_ids': policy_ids,
                                        'note': 'Citation overlap is not evidence of correct reasoning.'}}
        if pipe == 'A':
            a3 = pilot.read_run_json(run, 'A3')
            mapped_decisive = {gid for gid, atoms in mapping.items() if set(atoms) & set(a3.get('decisive_condition_ids', []))}
            entry['mechanical_proxies']['A3_decisive_ids_via_human_A1_mapping'] = sorted(mapped_decisive)
            entry['mechanical_proxies']['reference_decisive_atoms'] = decisive
        s['final_outputs'][pipe] = entry
    return s


def review_status(run):
    """Cheap status for pilot.audit_run: never raises, never claims a score that was not reviewed."""
    run = Path(run)
    try:
        meta = pilot.metadata(run)
        if meta['world'] == 'R0':
            return {'status': 'NOT_APPLICABLE', 'note': 'R0 has no researcher stage-wise Gold'}
        if (run / SCORE).exists():
            return {'status': 'MANUALLY_REVIEWED', 'file': SCORE}
        if (run / SCORE_JUDGE).exists():
            return {'status': 'LLM_JUDGE_LABELLED_NOT_HUMAN_CONFIRMED', 'file': SCORE_JUDGE,
                    'note': 'Usable only if the judge passed calibration against human labels (STEPWISE_AGREEMENT.json).'}
        if not (run / REVIEW).exists():
            return {'status': 'PENDING_MANUAL_REVIEW',
                    'note': 'A1 coverage, A2 semantic accuracy, missing-information matching and reasoning grounding are NOT scored until reviewed. '
                            'Run: python3 scripts/stepwise_review.py template --run <RUN>'}
        return {'status': 'REVIEW_FILE_PRESENT_NOT_SCORED', 'note': 'Run stepwise_review.py score --run <RUN>'}
    except Exception as exc:  # status reporting must not break an audit
        return {'status': 'UNKNOWN', 'note': repr(exc)}


def template_cmd(a):
    t = build_template(a.run)
    print(f"Wrote {Path(a.run) / TEMPLATE} ({len(t['final_outputs'])} blinded final output(s)). "
          f"Copy it to {REVIEW}, fill it in, then run score.")


def score_cmd(a):
    run = Path(a.run)
    source = getattr(a, 'source', 'human')
    review_file, score_file = (REVIEW, SCORE) if source == 'human' else (REVIEW_JUDGE, SCORE_JUDGE)
    template = build_template(run)  # regenerate from the run itself: the reference for the tamper check
    if not (run / review_file).exists():
        raise RuntimeError(f'{review_file} not found')
    review = json.loads((run / review_file).read_text(encoding='utf8'))
    status, message = validate_review(template, review, strict_judge=(source == 'judge'))
    if status:
        print(f'{status}: {message}')
        raise SystemExit(2)
    pilot.write(run / score_file, pilot.canon(compute_score(run, template, review, source)) + '\n')
    print(f'Scored {run.name} ({source}): wrote {score_file}')


def _pool(run_dirs, score_file):
    sums = defaultdict(lambda: [0, 0]); n = 0
    def add(name, ratio):
        if ratio and ratio['denominator']:
            sums[name][0] += ratio['numerator']; sums[name][1] += ratio['denominator']
    for run in run_dirs:
        if not (run / score_file).exists():
            continue
        sc = json.loads((run / score_file).read_text(encoding='utf8')); n += 1
        if 'A1' in sc:
            add('A1_reference_condition_recall', sc['A1']['reference_condition_recall'])
            add('A1_reference_condition_recall_lenient', sc['A1']['reference_condition_recall_lenient_full_or_partial'])
            add('A1_model_atom_precision', sc['A1']['model_atom_precision_policy_supported'])
        if 'A2' in sc:
            add('A2_status_semantic_accuracy', sc['A2']['status_semantic_accuracy_on_matched_conditions'])
        for pipe, fo in sc['final_outputs'].items():
            for name, val in ((f'{pipe}_correct_for_right_reasons', fo['correct_for_right_reasons']), (f'{pipe}_decision_correct', fo['decision_correct'])):
                sums[name][0] += int(val); sums[name][1] += 1
            mi = fo['missing_information']
            add(f'{pipe}_missing_fact_recall', mi['reference_fact_recall'])
            add(f'{pipe}_missing_fact_precision', mi['precision_not_unnecessary'])
    return n, {k: _ratio(v[0], v[1]) for k, v in sorted(sums.items())}


def summary_cmd(a):
    root = Path(a.runs_root) if a.runs_root else pilot.BASE / 'runs'
    agreement_path = REPORTS_DIR / AGREEMENT
    agreement = json.loads(agreement_path.read_text(encoding='utf8')) if agreement_path.exists() else None
    judge_ok = bool(agreement and agreement.get('judge_acceptable'))
    groups = defaultdict(list)
    for meta_file in sorted(root.glob('*/*/run_metadata.json')):
        run = meta_file.parent
        meta = pilot.metadata(run)
        if meta['world'] == 'R0':
            continue
        cohort = pilot.hash_(pilot.canon({'model': meta['model'], 'settings': pilot.settings_for_cohort(meta.get('inference_settings'))}))[:12]
        groups[(cohort, meta['model'], meta['world'])].append(run)
    rows = []
    for (cohort, model, world), runs in sorted(groups.items()):
        n_h, m_h = _pool(runs, SCORE); n_j, m_j = _pool(runs, SCORE_JUDGE)
        rows.append({'cohort': cohort, 'model': model, 'world': world, 'runs_total': len(runs),
                     'human_confirmed': {'runs': n_h, 'metrics': m_h},
                     'llm_judge_labelled': {'runs': n_j, 'metrics': m_j,
                                            'flagged_needs_human_check': sum(1 for r in runs if (r / SCORE_JUDGE).exists() and json.loads((r / SCORE_JUDGE).read_text(encoding='utf8')).get('needs_human_check')),
                                            'status': 'CALIBRATED (see calibration)' if judge_ok else 'UNCALIBRATED: do not report as a result'},
                     'pending_no_label_at_all': sum(1 for r in runs if not (r / SCORE).exists() and not (r / SCORE_JUDGE).exists())})
    out = {'note': 'Human-confirmed and LLM-judge-labelled numbers are kept apart. Judge numbers may be reported only with the calibration block; runs with no label are counted, never imputed. Rates pool numerators and denominators.',
           'calibration': agreement if agreement else 'NO AGREEMENT REPORT: the judge has not been calibrated against human labels',
           'groups': rows}
    dest = Path(a.output) if a.output else REPORTS_DIR / 'STEPWISE_SUMMARY.json'
    pilot.write(dest, pilot.canon(out) + '\n')
    print(f'Wrote {dest}: ' + '; '.join(f"{g['model']}/{g['world']}: human {g['human_confirmed']['runs']}, judge {g['llm_judge_labelled']['runs']}, unlabelled {g['pending_no_label_at_all']}" for g in rows))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name, fn in (('template', template_cmd), ('score', score_cmd)):
        p = sub.add_parser(name); p.add_argument('--run', required=True)
        if name == 'score': p.add_argument('--source', choices=['human', 'judge'], default='human')
        p.set_defaults(func=fn)
    p = sub.add_parser('summary'); p.add_argument('--runs-root', default=None); p.add_argument('--output', default=None); p.set_defaults(func=summary_cmd)
    args = ap.parse_args()
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        print(f'ERROR: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
