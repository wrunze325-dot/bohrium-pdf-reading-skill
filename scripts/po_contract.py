"""Read a case's output_contract without guessing what a field means.

v4 mapped field names by regular expression ("page", "table", "text" ...), which
filled values the case never asked for and left real questions empty. v5 keeps the
contract as the only source of field meaning:

  role = "structural"  the field is a parsed artefact the script can produce verbatim
                       (full text, blocks, tables, formulas, ...).
  role = "judgement"   everything else: an answer from the agent, with evidence.

Two contract shapes are supported:

  * JSON-schema style: {"properties": {...}, "required": [...]} — field type and
    description come from `properties`, requiredness from `required`. A field that
    `properties` declares but `required` does not name is optional.
  * list style: {"fields": [ {"name": ..., "type": ..., "required": ...}, "answer" ]}

Any declaration that cannot be read is reported in `warnings` instead of being
dropped: a dropped field would silently make the case look complete.
"""
from __future__ import annotations

import json
import re

STRUCTURAL_NAMES = {
    'text', 'markdown', 'plain_text', 'document_text', 'full_text',
    'blocks', 'sections', 'paragraphs', 'reading_order',
    'tables', 'formulas', 'figures', 'figure_captions',
    'references', 'references_json', 'layout',
}
JUDGEMENT_NAMES = {
    'answer', 'answers', 'evidence', 'citation', 'citations', 'page', 'pages',
    'page_number', 'table_number', 'value', 'result', 'conclusion', 'summary_answer',
    'missing', 'missing_information', 'uncertainty', 'confidence',
}

MISSING_KINDS = ('no_such_object', 'not_reported', 'parse_failure')
LIST_KEYS = ('fields', 'required_fields', 'outputs', 'schema')


def _warn(warnings, kind, detail, field=None):
    warnings.append({'kind': kind, 'field': field, 'detail': detail})


def _usable_name(value):
    return isinstance(value, str) and bool(value.strip())


def _role(name, ftype, desc):
    key = name.strip().lower()
    if key in JUDGEMENT_NAMES:
        return 'judgement'
    if ftype in ('evidence', 'answer', 'missing_information', 'citation'):
        return 'judgement'
    if key in STRUCTURAL_NAMES and not re.search(r'\b(per|for each|of the|answer|cite)\b', desc or '', re.I):
        return 'structural'
    return 'judgement'


def _default_type(name):
    if name == 'evidence':
        return 'evidence'
    if 'missing' in name:
        return 'missing_information'
    return 'text'


def _spec_fields(spec):
    """Return (type, description) from a property spec, or (None, None) if unreadable."""
    if isinstance(spec, str):
        return spec.strip().lower(), ''
    if isinstance(spec, dict):
        ftype = spec.get('type') or spec.get('format') or ''
        desc = spec.get('description') or spec.get('desc') or spec.get('meaning') or ''
        if isinstance(ftype, (list, dict)):
            ftype = ''
        return str(ftype).strip().lower(), str(desc)
    return None, None


def parse_contract(case):
    contract = (case.get('output_contract') or case.get('outputContract')
                or case.get('contract') or {})
    warnings = []
    fields = []
    seen = set()

    def add(name, required, ftype, desc, where):
        name = name.strip()
        if name in seen:
            _warn(warnings, 'duplicate_field', f'{name!r} declared more than once', name)
            return
        seen.add(name)
        ftype = ftype or _default_type(name)
        fields.append({'name': name, 'required': bool(required), 'type': ftype,
                       'description': desc or '', 'role': _role(name, ftype, desc)})

    if isinstance(contract, dict) and isinstance(contract.get('properties'), dict):
        props = contract['properties']
        req = contract.get('required')
        req_names = set()
        if isinstance(req, list):
            for r in req:
                if _usable_name(r):
                    req_names.add(r.strip())
                else:
                    _warn(warnings, 'unrecognised_declaration',
                          f'"required" entry {r!r} is not a field name')
        elif isinstance(req, dict):
            req_names = {k for k, v in req.items() if v and _usable_name(k)}
        elif req is not None:
            _warn(warnings, 'unrecognised_declaration',
                  f'"required" has unsupported shape {type(req).__name__}')
        for name, spec in props.items():
            ftype, desc = _spec_fields(spec)
            if not _usable_name(name):
                _warn(warnings, 'unrecognised_declaration',
                      f'property name {name!r} cannot be used as a field name')
                continue
            if ftype is None:
                _warn(warnings, 'unrecognised_declaration',
                      f'property {name!r} has a spec that is neither object nor string')
                ftype, desc = '', ''
            add(name, name.strip() in req_names, ftype, desc, 'properties')
        for missing in sorted(req_names - {n.strip() for n in props}):
            _warn(warnings, 'required_without_property',
                  f'{missing!r} is required but not declared in properties', missing)
    else:
        raw_items = []
        if isinstance(contract, list):
            raw_items = list(contract)
        elif isinstance(contract, dict):
            for key in LIST_KEYS:
                val = contract.get(key)
                if isinstance(val, list):
                    raw_items = val
                    break
                if isinstance(val, dict):
                    raw_items = [dict(v, name=k) if isinstance(v, dict) else {'name': k, 'type': str(v)}
                                 for k, v in val.items()]
                    break
        if not raw_items:
            for key in ('output_fields', 'expected_output', 'answer_fields'):
                val = case.get(key)
                if isinstance(val, list):
                    raw_items = val
                    break
                if isinstance(val, dict):
                    raw_items = [{'name': k, 'type': str(v)} for k, v in val.items()]
                    break
        if contract not in ({}, [], None) and not raw_items:
            _warn(warnings, 'unrecognised_contract',
                  f'output_contract has an unsupported shape: {json.dumps(contract, ensure_ascii=False)[:120]}')
        for item in raw_items:
            if isinstance(item, str):
                if _usable_name(item):
                    add(item, True, '', '', 'list')
                else:
                    _warn(warnings, 'unrecognised_declaration', f'empty field name {item!r}')
            elif isinstance(item, dict):
                name = item.get('name') or item.get('field') or item.get('id') or item.get('key')
                if not _usable_name(name):
                    _warn(warnings, 'unrecognised_declaration',
                          f'field declaration without a usable name: '
                          f'{json.dumps(item, ensure_ascii=False)[:100]}')
                    continue
                ftype, desc = _spec_fields(item)
                required = item.get('required')
                add(name, True if required is None else bool(required), ftype or '',
                    desc or '', 'list')
            else:
                _warn(warnings, 'unrecognised_declaration',
                      f'field declaration of type {type(item).__name__}: {item!r}')

    return {
        'declared': contract,
        'fields': fields,
        'warnings': warnings,
        'coverage_complete': not warnings,
    }


def pending(contract):
    return [f['name'] for f in contract['fields'] if f['required']]


def describe_field(f):
    return {
        'field': f['name'],
        'required': f['required'],
        'role': f['role'],
        'type': f['type'],
        'description': f['description'],
    }
