#!/usr/bin/env python3
"""Structure-only JSON Schemas for the four stages, for Ollama structured-output decoding.

PRINCIPLE: each schema says exactly what the stage prompt's own OUTPUT SCHEMA says and NOTHING else. No case-specific values
(no atom IDs, no atom counts, no policy IDs), because "does A2 keep, drop or rename the atoms it was given" and "does A3 cite real
policy IDs" are things the experiment measures; constraining them would remove the failure being measured.
Enumerations that appear in the prompt itself (status values, decision labels, expression operators) are part of the format.
"""
import json

import pilot

EXPR_DEPTH = 4   # maximum nesting of NOT/ALL/ANY operators in eligibility_expression (the researcher reference logic has depth 2)
_STR = {'type': 'string'}
_NONEMPTY = _STR     # "non-empty" is NOT enforced by the schema (minLength is not portable to every provider); the pipeline's own validator rejects empty values
_STRS = {'type': 'array', 'items': _STR}


def _expr_defs(depth):
    """Recursion is not accepted by every provider (Anthropic rejects recursive schemas), so the expression grammar is unrolled to a fixed depth:
    expr0 = {"ref": ...}; exprK = a ref, or NOT(expr K-1), or ALL/ANY(list of expr K-1)."""
    defs = {'expr0': {'type': 'object', 'properties': {'ref': _STR}, 'required': ['ref'], 'additionalProperties': False}}
    for k in range(1, depth + 1):
        sub = {'$ref': f'#/$defs/expr{k - 1}'}
        defs[f'expr{k}'] = {'anyOf': [
            {'type': 'object', 'properties': {'ref': _STR}, 'required': ['ref'], 'additionalProperties': False},
            {'type': 'object', 'properties': {'op': {'type': 'string', 'enum': ['NOT']}, 'arg': sub}, 'required': ['op', 'arg'], 'additionalProperties': False},
            {'type': 'object', 'properties': {'op': {'type': 'string', 'enum': ['ALL', 'ANY']}, 'args': {'type': 'array', 'minItems': 1, 'items': sub}},
             'required': ['op', 'args'], 'additionalProperties': False}]}
    return defs


_DEFS = _expr_defs(EXPR_DEPTH)
_EXPR_REF = {'$ref': f'#/$defs/expr{EXPR_DEPTH}'}
_BRANCH = {'type': 'object', 'properties': {'possible_answer': {'type': 'string', 'enum': ['YES', 'NO']}, 'conditions_in_words': _STR},
           'required': ['possible_answer', 'conditions_in_words'], 'additionalProperties': False}
_DECISION = {'type': 'string', 'enum': ['YES', 'NO', 'NEED_MORE_INFORMATION']}


def _obj(props, required=None):
    return {'type': 'object', 'properties': props, 'required': list(props) if required is None else required, 'additionalProperties': False}


SCHEMAS = {
    'A1': {'$defs': _DEFS, **_obj({
        'decision_target': _STR, 'decision_unit': _STR,
        'atoms': {'type': 'array', 'minItems': 1, 'items': _obj({'id': _NONEMPTY, 'proposition': _NONEMPTY, 'entity_scope': _NONEMPTY, 'source_ids': _STRS})},
        'eligibility_expression': _EXPR_REF, 'scope_issues': _STRS, 'limitations': _STRS})},
    'A2': _obj({
        'statuses': {'type': 'array', 'items': _obj({'id': _NONEMPTY, 'status': {'type': 'string', 'enum': ['TRUE', 'FALSE', 'UNKNOWN', 'CONFLICTING']},
                                                      'evidence_quote': _STR, 'justification': _STR})},
        'scope_issues': _STRS}),
    'A3': _obj({
        'decision': _DECISION, 'supporting_policy_ids': _STRS, 'decisive_condition_ids': _STRS, 'unresolved_condition_ids': _STRS,
        'missing_facts': _STRS, 'justification': _STR, 'scope_issues': _STRS,
        'ir_discrepancies': {'type': 'array', 'items': _obj({'type': {'type': 'string', 'enum': ['OMISSION', 'UNSUPPORTED', 'RELATION', 'SCOPE', 'STATUS', 'OTHER']},
                                                             'description': _STR, 'policy_ids': _STRS, 'condition_ids': _STRS})},
        'conditional_branches': {'type': 'array', 'items': _BRANCH}}),
    'B1': _obj({
        'decision': _DECISION, 'supporting_policy_ids': _STRS, 'missing_facts': _STRS, 'justification': _STR, 'scope_issues': _STRS,
        'conditional_branches': {'type': 'array', 'items': _BRANCH}}),
}


def stage_schema(stage):
    return SCHEMAS[stage]


def schema_sha256(stage):
    return pilot.hash_(json.dumps(SCHEMAS[stage], sort_keys=True, ensure_ascii=False))


def all_hashes():
    return {s: schema_sha256(s) for s in SCHEMAS}


PORTABLE_KEYWORDS = {'type', 'properties', 'required', 'additionalProperties', 'items', 'enum', 'anyOf', '$ref', '$defs', 'minItems'}


def portable_problems(schema=None):
    """Check the schemas against the INTERSECTION of what Ollama, Anthropic and OpenAI (strict) accept, as documented 2026-10:
    no recursion, no minLength/maxLength/min/max, minItems only 0 or 1, every object additionalProperties=false with ALL properties required, root is an object."""
    problems = []
    for stage, root in ([(None, schema)] if schema is not None else list(SCHEMAS.items())):
        defs = root.get('$defs', {})
        if root.get('type') != 'object':
            problems.append(f'{stage}: root must be an object')

        def walk(node, path, stack):
            if isinstance(node, list):
                for i, x in enumerate(node): walk(x, f'{path}[{i}]', stack)
                return
            if not isinstance(node, dict):
                return
            for k in node:
                if path.endswith('properties') or path.endswith('$defs'):
                    continue            # property / definition names are free text
                if k not in PORTABLE_KEYWORDS:
                    problems.append(f'{stage}:{path}: keyword {k!r} is not portable')
            if node.get('type') == 'object':
                props = node.get('properties', {})
                if node.get('additionalProperties') is not False:
                    problems.append(f'{stage}:{path}: object must set additionalProperties=false')
                if set(node.get('required', [])) != set(props):
                    problems.append(f'{stage}:{path}: every property must be required')
            if 'minItems' in node and node['minItems'] not in (0, 1):
                problems.append(f'{stage}:{path}: minItems must be 0 or 1')
            if '$ref' in node:
                name = node['$ref'].split('/')[-1]
                if name in stack:
                    problems.append(f'{stage}:{path}: recursive reference to {name}')
                elif name in defs:
                    walk(defs[name], f'$defs.{name}', stack + (name,))
                else:
                    problems.append(f'{stage}:{path}: unresolved reference {node["$ref"]}')
            for k, v in node.items():
                if k in ('properties', '$defs') and isinstance(v, dict):
                    for pk, pv in v.items(): walk(pv, f'{path}.{k}.{pk}' if k == 'properties' else f'{path}.{k}.{pk}', stack) if k == 'properties' else None
                elif k not in ('$ref',):
                    walk(v, f'{path}.{k}', stack)
        walk({k: v for k, v in root.items() if k != '$defs'}, '$', ())
    return sorted(set(problems))
