"""Turn a parsed document into the per-case result the platform expects.

    python build_result.py --case /input/case.json --blocks OUT/blocks.json \
        --out /app/output/result.json [--markdown OUT/document.md]

The evaluator hands out three fixed cases, each with its own `output_contract`.
Nothing here hard-codes example fields: the contract is read from the case file,
every declared field is listed with a fill state, and any field whose name maps to
something this skill actually produces is filled. Fields that need a judgement call
(an answer to a question about the paper, a translation) are left for the agent to
complete, and are marked as such instead of being filled with a guess.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

CASE_ID_KEYS = ('case_id', 'caseId', 'id', 'task_id', 'taskId', 'name', 'slug')

FIELD_MAP = (
    # (regex on field name, producer key)
    (r'(markdown|plain[_ ]?text|full[_ ]?text|document[_ ]?text|^text$|content$)', 'markdown'),
    (r'(blocks?|paragraphs?|sections?|layout|reading[_ ]?order)', 'blocks'),
    (r'(tables?|cells?)', 'tables'),
    (r'(formulas?|equations?|math)', 'formulas'),
    (r'(figures?|captions?|images?)', 'figures'),
    (r'(references?|bibliograph)', 'references'),
    (r'(pages?|page[_ ]?count)', 'pages'),
    (r'(language|lang)', 'language'),
    (r'(uncertaint|warnings?|limitations?)', 'uncertainties'),
)


def load_json(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def find_case_id(case):
    for k in CASE_ID_KEYS:
        v = case.get(k)
        if isinstance(v, (str, int)) and str(v).strip():
            return str(v)
    return 'unknown-case'


def contract_fields(case):
    """Pull the list of required output fields out of whatever shape the case uses."""
    contract = case.get('output_contract') or case.get('outputContract') or case.get('contract') or {}
    if isinstance(contract, list):
        return [str(x) for x in contract], contract
    names = []
    if isinstance(contract, dict):
        for key in ('fields', 'required', 'required_fields', 'outputs', 'schema', 'properties'):
            val = contract.get(key)
            if isinstance(val, list):
                names = [str(x) if not isinstance(x, dict) else str(x.get('name') or x.get('id'))
                         for x in val]
                break
            if isinstance(val, dict):
                names = list(val.keys())
                break
    if not names:
        # fall back to top-level case keys that look like an output spec
        for key in ('output_fields', 'expected_output', 'answer_fields'):
            val = case.get(key)
            if isinstance(val, list):
                names = [str(x) for x in val]
                break
            if isinstance(val, dict):
                names = list(val.keys())
                break
    return names, contract


def scene_key(field):
    f = str(field).lower()
    for pat, key in FIELD_MAP:
        if re.search(pat, f):
            return key
    return None


def build(case, blocks_doc, markdown, pdf_path):
    case_id = find_case_id(case)
    fields, contract = contract_fields(case)
    tables = blocks_doc.get('tables') or []
    formulas = blocks_doc.get('formulas') or []
    figures = blocks_doc.get('figures') or []
    blocks = blocks_doc.get('blocks') or []
    refs = [b for b in blocks if b.get('type') == 'reference']

    producers = {
        'markdown': markdown or '',
        'blocks': blocks,
        'tables': tables,
        'formulas': formulas,
        'figures': figures,
        'references': refs,
        'pages': blocks_doc.get('source', {}).get('pages'),
        'language': case.get('language') or case.get('lang') or 'auto',
        'uncertainties': blocks_doc.get('uncertainties') or [],
    }

    answers = {}
    field_state = []
    for name in fields:
        key = scene_key(name)
        filled = key is not None and producers.get(key) not in (None, '', [])
        if filled:
            answers[name] = producers[key]
        else:
            answers[name] = None
        field_state.append({
            'field': name, 'auto_filled': bool(filled),
            'source': key if filled else None,
            'note': None if filled else 'needs the agent: this field is a judgement call derived '
                                        'from the parsed content, not a parsing artefact',
        })

    evidence = [{'block_id': b.get('id'), 'page': b.get('page'), 'type': b.get('type'),
                 'locator': b.get('locator')}
                for b in blocks if b.get('locator')]

    status = blocks_doc.get('status') or 'partial'
    result = {
        'schema': 'pdfoutline/result/v1',
        'case_id': case_id,
        'status': status,
        'generated_at': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
        'task': case.get('task') or case.get('question') or case.get('prompt'),
        'source': {
            'pdf': pdf_path or blocks_doc.get('source', {}).get('path'),
            'sha256': blocks_doc.get('source', {}).get('sha256'),
            'pages': blocks_doc.get('source', {}).get('pages'),
            'backend': blocks_doc.get('source', {}).get('backend'),
            'layout': blocks_doc.get('layout'),
        },
        'parsed': {
            'markdown': markdown or '',
            'blocks': blocks,
            'tables': tables,
            'formulas': formulas,
            'figures': figures,
        },
        'evidence': evidence,
        'uncertainties': blocks_doc.get('uncertainties') or [],
        'output_contract': {
            'declared': contract,
            'fields': field_state,
            'unfilled': [f['field'] for f in field_state if not f['auto_filled']],
        },
        'answers': answers,
        'change_log': [
            {'step': 'parse', 'action': 'layout-aware text extraction + reading order',
             'detail': f"{len(blocks)} blocks, {blocks_doc.get('layout', {}).get('columns', 1)} column(s)"},
            {'step': 'structure', 'action': 'tables / formulas / figures / captions',
             'detail': f'{len(tables)} table(s), {len(formulas)} formula(s), {len(figures)} figure(s)'},
            {'step': 'evidence', 'action': 'page + bbox recorded for every block',
             'detail': f'{len(evidence)} locators'},
            {'step': 'contract', 'action': 'map parsed content onto case output_contract',
             'detail': f'{sum(1 for f in field_state if f["auto_filled"])}/{len(field_state)} fields auto-filled'},
        ],
    }
    return result


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True, help='/input/case.json')
    ap.add_argument('--blocks', required=True, help='blocks.json from pdf_outline.py')
    ap.add_argument('--out', required=True, help='result.json to write')
    ap.add_argument('--markdown', default=None)
    ap.add_argument('--pdf', default=None)
    ap.add_argument('--status', default=None, help='override status (ok|partial|failed)')
    args = ap.parse_args(argv)

    case = load_json(args.case, {}) or {}
    blocks_doc = load_json(args.blocks, {})
    if not blocks_doc:
        print(json.dumps({'status': 'failed', 'error': f'cannot read {args.blocks}'}))
        return 2
    markdown = ''
    if args.markdown and os.path.exists(args.markdown):
        markdown = open(args.markdown, encoding='utf-8').read()
    elif args.blocks:
        guess = os.path.join(os.path.dirname(args.blocks), 'document.md')
        if os.path.exists(guess):
            markdown = open(guess, encoding='utf-8').read()

    result = build(case, blocks_doc, markdown, args.pdf)
    if args.status:
        result['status'] = args.status
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    print(json.dumps({'status': result['status'], 'case_id': result['case_id'],
                      'blocks': len(result['parsed']['blocks']),
                      'unfilled_fields': result['output_contract']['unfilled'],
                      'out': args.out}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
