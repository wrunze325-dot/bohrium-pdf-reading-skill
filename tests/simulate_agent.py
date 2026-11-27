"""Test double for the agent: turn a case's expected answer into an answers file.

This is NOT part of the skill. It exists so the local tests can exercise the
"agent answers -> validate" step deterministically, the way a real agent would
fill in /app/output/result.json after reading the document.

Run: python simulate_agent.py --case cases/case1_page1.json --blocks runs/x/parse/blocks.json --out answers.json
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'scripts'))
import po_evidence as PE  # noqa: E402


def build(case, blocks_doc, mode='good'):
    exp = case.get('expected') or {}
    cid = case.get('case_id')
    if cid and 'no-table' in cid:
        return {
            'answers': {},
            'missing_information': [
                {'kind': 'no_such_object', 'object': 'Table 2',
                 'checked': {'tables': len(blocks_doc.get('tables') or []),
                             'pages_checked': 1,
                             'how': 'table extraction returned no table anywhere in the document'}},
                {'kind': 'not_reported', 'quantity': 'accuracy of method B',
                 'checked': {'queries': [
                     dict(PE.search_document(blocks_doc, 'method B')),
                     dict(PE.search_document(blocks_doc, 'Table 2')),
                     dict(PE.search_document(blocks_doc, 'accuracy')),
                 ]}},
            ],
        }
    answer = {
        'value': str(exp.get('answer_contains')),
        'type': 'table_cell' if exp.get('table_column') else 'text',
        'unit': exp.get('unit') or ('fraction' if exp.get('table_column') else 'lines'),
        'evidence': [{'page': exp.get('page'), 'quote': exp.get('quote')}],
        'locator': {'table': 'table-1'},
        'filled_by': 'agent',
    }
    if exp.get('table_column'):
        answer['locator'].update({'row': exp.get('table_row'), 'column': exp.get('table_column')})
    if mode == 'wrong_page':
        answer['evidence'][0]['page'] = (exp.get('page') or 1) + 1
    if mode == 'wrong_number':
        answer['value'] = '7'
    if mode == 'no_row_column':
        answer['locator'] = {'table': 'table-1'}
    return {'answers': {'answer': answer, 'evidence': answer['evidence']},
            'missing_information': []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--case', required=True)
    ap.add_argument('--blocks', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', default='good',
                    choices=['good', 'wrong_page', 'wrong_number', 'no_row_column'])
    args = ap.parse_args()
    case = json.load(open(args.case, encoding='utf-8'))
    blocks_doc = json.load(open(args.blocks, encoding='utf-8'))
    payload = build(case, blocks_doc, args.mode)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(json.dumps({'out': args.out, 'mode': args.mode}, ensure_ascii=False))


if __name__ == '__main__':
    main()
