"""Build and check the per-case result the platform expects.

Three separate ideas, deliberately kept apart:

    parse "did the document parse?"              -> result['parse']['status']
    case  "is the case's contract satisfied?"    -> result['case_status']

A case is only `complete` when every required field carries a validated answer
with evidence. Parsing the document is not answering the question, so a scaffold
result is `incomplete` from the start and never claims otherwise.

Usage
  # 1. parse output -> scaffold result.json (never claims the case is done)
  python build_result.py --case case.json --blocks OUT/blocks.json --out /app/output/result.json

  # 2. the agent writes answers (with page + verbatim quote), then they are merged
  python build_result.py --case case.json --result /app/output/result.json \
      --apply-answers answers.json

  # 3. re-check whatever is on disk after any hand edit
  python build_result.py --case case.json --result /app/output/result.json --validate

  # facts the agent may need (page count, table count, searchable terms)
  python build_result.py --case case.json --blocks OUT/blocks.json --facts
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import po_contract as PC          # noqa: E402
import po_evidence as PE          # noqa: E402

SCHEMA = 'pdfoutline/result/v2'


def now():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def load(path, default=None):
    try:
        with open(path, encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return default


def blocks_like(result_or_doc):
    """Accept either blocks.json or a result.json and return the parsed view."""
    if 'parsed' in result_or_doc:
        p = result_or_doc['parsed']
        src = result_or_doc.get('source') or {}
        return {
            'blocks': p.get('blocks') or [],
            'tables': p.get('tables') or [],
            'formulas': p.get('formulas') or [],
            'figures': p.get('figures') or [],
            'uncertainties': result_or_doc.get('uncertainties') or [],
            'status': result_or_doc.get('parse', {}).get('status'),
            'source': {'pages': src.get('pages'), 'backend': src.get('backend'),
                       'sha256': src.get('sha256'), 'path': src.get('pdf')},
            'layout': result_or_doc.get('parse', {}).get('layout'),
        }
    return result_or_doc


# ------------------------------------------------------------------ structural
STRUCTURAL_PRODUCERS = {
    'text': lambda b, md: md,
    'markdown': lambda b, md: md,
    'plain_text': lambda b, md: md,
    'document_text': lambda b, md: md,
    'full_text': lambda b, md: md,
    'blocks': lambda b, md: b.get('blocks') or [],
    'sections': lambda b, md: [x for x in (b.get('blocks') or []) if x.get('type') == 'heading'],
    'paragraphs': lambda b, md: [x for x in (b.get('blocks') or []) if x.get('type') == 'body'],
    'reading_order': lambda b, md: [x.get('id') for x in (b.get('blocks') or [])],
    'layout': lambda b, md: b.get('layout'),
    'tables': lambda b, md: b.get('tables') or [],
    'formulas': lambda b, md: b.get('formulas') or [],
    'figures': lambda b, md: b.get('figures') or [],
    'figure_captions': lambda b, md: [x for x in (b.get('blocks') or []) if x.get('type') == 'caption'
                                             and (x.get('caption_label') or '') in ('figure', 'fig')],
    'references': lambda b, md: [x for x in (b.get('blocks') or []) if x.get('type') == 'reference'],
    'references_json': lambda b, md: [x for x in (b.get('blocks') or []) if x.get('type') == 'reference'],
}


LIST_FIELDS = {'blocks', 'sections', 'paragraphs', 'reading_order', 'tables', 'formulas',
               'figures', 'figure_captions', 'references', 'references_json'}


def structural_value(name, blocks_doc, markdown):
    """Return the parsed artefact, keeping "ran and found nothing" distinct from "n/a".

    An empty list is a fact about the document ("it has no table"); None means the
    name is not something this script produces.
    """
    fn = STRUCTURAL_PRODUCERS.get(name)
    if not fn:
        return None
    val = fn(blocks_doc, markdown)
    if val is None:
        return [] if name in LIST_FIELDS else None
    if val == '' :
        return None if name not in LIST_FIELDS else []
    return val


# --------------------------------------------------------------------- status
def field_is_filled(f, val, facts):
    """Is this contract field satisfied?

    An empty list is a real answer for a *structural* field (a document can
    genuinely contain no table), but only when the document was read cleanly —
    otherwise "no tables" would be a claim about a document we could not read.
    """
    if val is None or val == '' or val == {}:
        return False
    if isinstance(val, list) and not val:
        return f['role'] == 'structural' and facts.get('parse_status') == 'ok'
    return True


def compute_status(contract, answers, facts, answer_issues, missing_issues, missing_entries):
    if facts.get('parse_status') == 'failed':
        return 'failed'
    required = [f for f in contract['fields'] if f['required']]
    pending = [f['name'] for f in required
               if not field_is_filled(f, answers.get(f['name']), facts)]
    if not pending and not answer_issues and not missing_issues:
        return 'complete'
    covered = {f['name'] for f in required if f['name'] in answers}
    if pending and missing_entries and not missing_issues:
        # an explicit, validated "the document does not contain this" answer
        if all(issues.get('field') not in pending for issues in answer_issues) and missing_entries:
            return 'blocked'
    if not pending and missing_entries and not missing_issues and not answer_issues:
        return 'complete'
    _ = covered
    return 'incomplete'


# ------------------------------------------------------------------ validation
def validate(contract, answers, missing_entries, blocks_doc, facts):
    answer_issues = []
    evidence_field = []
    for f in contract['fields']:
        name = f['name']
        val = answers.get(name)
        if f['role'] == 'structural':
            # a parsed artefact is not an answer: it is checked for presence, never
            # for evidence, and it can never make a case "complete" on its own
            if field_is_filled(f, val, facts):
                continue
            if isinstance(val, list) and not val:
                answer_issues.append({'field': name, 'rule': 'structural_empty_unconfirmed',
                                      'detail': f'{name!r} is empty, but the document was not read '
                                                f'cleanly enough to confirm the absence'})
            elif f['required']:
                answer_issues.append({'field': name, 'rule': 'structural_missing',
                                      'detail': f'parsed field {name!r} is empty'})
            continue
        if name in answers and val not in (None, '', [], {}):
            if f['type'] == 'missing_information' or name in ('missing', 'missing_information'):
                answer_issues.extend(PE.validate_missing(name, val, blocks_doc, facts))
            elif name == 'evidence':
                entries = val if isinstance(val, list) else [val]
                for ent in entries:
                    answer_issues.extend(PE.validate_answer(name, {'value': 'x', 'evidence': [ent]},
                                                           blocks_doc))
            else:
                ans, shape_issues = PE.resolve_answer(val, blocks_doc)
                sentence = ans.get('value') if isinstance(ans, dict) else ans
                issues = shape_issues + PE.validate_answer(name, ans, blocks_doc, f['type'])
                answer_issues.extend(issues)
                if not issues and isinstance(ans, dict):
                    for ev in ans.get('evidence') or []:
                        evidence_field.append({'field': name, 'page': ev.get('page'),
                                               'block_id': ev.get('block_id'),
                                               'quote': ev.get('quote'),
                                               'bbox': ev.get('bbox'),
                                               'locator': ev.get('locator')})
                    answers[name] = ans
                    _ = sentence
    for w in contract.get('warnings') or []:
        answer_issues.append({'field': w.get('field'),
                              'rule': 'contract_unrecognized_field',
                              'detail': w.get('detail')})
    missing_issues = []
    if missing_entries:
        missing_issues = PE.validate_missing('missing_information', missing_entries, blocks_doc, facts)
    return answer_issues, missing_issues, evidence_field


# ------------------------------------------------------------------- scaffold
def build_scaffold(case, contract, blocks_doc, markdown):
    facts = PE.document_facts(blocks_doc)
    answers = {}
    field_state = []
    for f in contract['fields']:
        if f['role'] == 'structural':
            val = structural_value(f['name'], blocks_doc, markdown)
            answers[f['name']] = val
            filled = field_is_filled(f, val, facts)
            state = dict(PC.describe_field(f), filled=filled,
                         source='parsed' if filled else 'not_parsed')
            if isinstance(val, list) and not val:
                state['note'] = ('empty list accepted: the document parsed cleanly and '
                                 'contains no such object' if filled else
                                 'empty list, but the document was not read cleanly')
            field_state.append(state)
        else:
            answers[f['name']] = None
            field_state.append(dict(PC.describe_field(f), filled=False, source=None,
                                    note='needs an agent answer with evidence'))
    result = {
        'schema': SCHEMA,
        'case_id': case.get('case_id') or case.get('id') or 'unknown-case',
        'task': case.get('task') or case.get('question') or case.get('prompt'),
        'generated_at': now(),
        'parse': {
            'status': facts.get('parse_status') or 'failed',
            'pages': facts.get('pages'),
            'backend': facts.get('backend'),
            'blocks': facts.get('blocks'),
            'tables': facts.get('tables'),
            'formulas': facts.get('formulas'),
            'figures': facts.get('figures'),
            'layout': blocks_doc.get('layout'),
        },
        'case_status': None,
        'status': None,
        'source': {
            'pdf': (blocks_doc.get('source') or {}).get('path'),
            'sha256': (blocks_doc.get('source') or {}).get('sha256'),
            'pages': facts.get('pages'),
        },
        'answers': answers,
        'missing_information': [],
        'contract': {'declared': contract['declared'],
                     'fields': field_state,
                     'warnings': contract.get('warnings') or [],
                     'coverage_complete': contract.get('coverage_complete', True),
                     'pending': [f['name'] for f in contract['fields']
                                 if f['required'] and not field_is_filled(
                                     f, answers.get(f['name']), facts)]},
        'parsed': {
            'markdown': markdown,
            'blocks': blocks_doc.get('blocks') or [],
            'tables': blocks_doc.get('tables') or [],
            'formulas': blocks_doc.get('formulas') or [],
            'figures': blocks_doc.get('figures') or [],
        },
        'uncertainties': blocks_doc.get('uncertainties') or [],
        'answer_issues': [],
        'missing_issues': [],
        'validation': {'checked_at': now(), 'passed': False, 'rules': [
            'required field present and non-empty',
            'evidence page inside 1..pages',
            'quote occurs verbatim on the cited page',
            'every number in the answer appears in an evidence quote',
            'table-cell answer names table, row and column that exist and agree',
            'missing-information claims record what was checked',
        ]},
        'change_log': [
            {'step': 'parse', 'action': 'layout-aware parse',
             'detail': f"{facts.get('blocks')} blocks, {facts.get('tables')} table(s), "
                       f"pages {facts.get('pages')}"},
            {'step': 'contract', 'action': 'read output_contract from the case',
             'detail': f"{len([f for f in contract['fields'] if f['role'] == 'structural'])} structural, "
                       f"{len([f for f in contract['fields'] if f['role'] == 'judgement'])} judgement fields"},
            {'step': 'scaffold', 'action': 'structural fields filled, judgement fields left pending',
             'detail': 'case_status is incomplete until answers are validated'},
        ],
    }
    apply_validation(result, contract, blocks_doc, facts)
    return result


def apply_validation(result, contract, blocks_doc, facts):
    answers = result.get('answers') or {}
    missing_entries = result.get('missing_information') or []
    answer_issues, missing_issues, evidence_field = validate(contract, answers, missing_entries,
                                                             blocks_doc, facts)
    # the contract's "evidence" field is derived from validated answers, never from
    # the block index: dumping every block is not evidence for a specific answer
    for f in contract['fields']:
        if f['name'] == 'evidence' and evidence_field and not answer_issues:
            answers['evidence'] = evidence_field
    result['answers'] = answers
    result['answer_issues'] = answer_issues
    result['missing_issues'] = missing_issues
    status = compute_status(contract, answers, facts, answer_issues, missing_issues, missing_entries)
    result['case_status'] = status
    result['status'] = status
    result['parse']['status'] = facts.get('parse_status') or result['parse'].get('status')
    result['validation'] = {
        'checked_at': now(),
        'passed': status == 'complete',
        'issues': answer_issues + missing_issues,
        'rules': (result.get('validation') or {}).get('rules', []),
    }
    field_state = []
    for f in contract['fields']:
        val = answers.get(f['name'])
        filled = field_is_filled(f, val, facts)
        state = dict(PC.describe_field(f), filled=filled,
                     source=('parsed' if f['role'] == 'structural' else
                             ('agent' if filled else None)))
        if isinstance(val, list) and not val:
            state['note'] = ('empty list accepted: the document parsed cleanly and '
                             'contains no such object' if filled else
                             'empty list, but the document was not read cleanly')
        field_state.append(state)
    result['contract']['fields'] = field_state
    result['contract']['warnings'] = contract.get('warnings') or []
    result['contract']['coverage_complete'] = contract.get('coverage_complete', True)
    result['contract']['pending'] = [f['name'] for f in contract['fields']
                                     if f['required'] and not field_is_filled(
                                         f, answers.get(f['name']), facts)]
    result.setdefault('change_log', []).append({
        'step': 'validate', 'action': 're-checked every required field',
        'detail': f'case_status={status}; {len(answer_issues) + len(missing_issues)} issue(s)'})
    return result


# ----------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description='Result builder / validator for pdf-reading-order')
    ap.add_argument('--case', required=True)
    ap.add_argument('--blocks')
    ap.add_argument('--markdown')
    ap.add_argument('--out')
    ap.add_argument('--result', help='existing result.json (for --apply-answers / --validate)')
    ap.add_argument('--apply-answers', help='JSON file with {"answers": {...}, "missing_information": [...]}')
    ap.add_argument('--validate', action='store_true')
    ap.add_argument('--facts', action='store_true')
    args = ap.parse_args(argv)

    case = load(args.case, {}) or {}
    contract = PC.parse_contract(case)

    if args.facts:
        if not args.blocks:
            print(json.dumps({'error': '--facts needs --blocks'}))
            return 2
        doc = load(args.blocks, {})
        print(json.dumps(PE.document_facts(doc), ensure_ascii=False, indent=1))
        return 0

    # path A: scaffold from a fresh parse
    if args.blocks and not args.result:
        doc = load(args.blocks)
        if not doc:
            print(json.dumps({'status': 'failed', 'error': f'cannot read {args.blocks}'}))
            return 2
        markdown = ''
        if args.markdown and os.path.exists(args.markdown):
            markdown = open(args.markdown, encoding='utf-8').read()
        out = args.out or os.path.join('/app/output', 'result.json')
        result = build_scaffold(case, contract, doc, markdown)
        if args.apply_answers:
            apply_answers_file(result, contract, args.apply_answers)
        write(out, result)
        print(json.dumps({'case_id': result['case_id'], 'case_status': result['case_status'],
                          'parse_status': result['parse']['status'],
                          'pending': result['contract']['pending'], 'out': out},
                         ensure_ascii=False))
        return 0 if result['case_status'] in ('complete', 'blocked', 'incomplete') else 2

    # path B: work on an existing result.json
    if not args.result:
        print(json.dumps({'error': 'give --blocks (scaffold) or --result (apply/validate)'}))
        return 2
    result = load(args.result)
    if not result:
        print(json.dumps({'error': f'cannot read {args.result}'}))
        return 2
    blocks_doc = blocks_like(result)
    facts = PE.document_facts(blocks_doc)
    if args.apply_answers:
        apply_answers_file(result, contract, args.apply_answers, blocks_doc)
    apply_validation(result, contract, blocks_doc, facts)
    write(args.result, result)
    summary = {'case_id': result['case_id'], 'case_status': result['case_status'],
               'parse_status': result['parse']['status'],
               'pending': result['contract']['pending'],
               'issues': [i.get('rule') for i in result['answer_issues'] + result['missing_issues']],
               'out': args.result}
    print(json.dumps(summary, ensure_ascii=False))
    if args.validate:
        return 0 if result['case_status'] in ('complete', 'blocked') else 2
    return 0


def apply_answers_file(result, contract, path, blocks_doc=None):
    payload = load(path, {}) or {}
    if blocks_doc is None:
        blocks_doc = blocks_like(result)
    answers = payload.get('answers') or {}
    for name, val in answers.items():
        result['answers'][name] = val
    if payload.get('missing_information') is not None:
        result['missing_information'] = payload['missing_information']
    result.setdefault('change_log', []).append({
        'step': 'agent_answers', 'action': 'answers merged from the agent',
        'detail': ', '.join(sorted(answers)) or 'none'})
    _ = contract
    return result


def write(path, result):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or '.', exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


if __name__ == '__main__':
    sys.exit(main())
