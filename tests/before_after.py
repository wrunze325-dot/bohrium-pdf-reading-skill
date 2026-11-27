"""Capture the behaviour of the two regression cases BEFORE the fix.

Saves before_case4.json and before_case5.json (plus the contract v5 currently
parses out of case5/case6) so the fix can be compared against real output.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.abspath(os.path.join(HERE, '..'))
CASES = os.path.join(HERE, 'cases')
OUT = os.path.join(HERE, 'runs', 'before')
PY = sys.executable


def run(case_name, tag):
    case_path = os.path.join(CASES, case_name)
    case = json.load(open(case_path, encoding='utf-8'))
    pdf = os.path.join(SKILL, case['pdf'])
    work = os.path.join(OUT, tag)
    if os.path.isdir(work):
        shutil.rmtree(work)
    os.makedirs(work, exist_ok=True)
    if not os.path.exists(os.path.join(SKILL, 'tests', 'fixtures', 'two_column.pdf')):
        subprocess.run([PY, os.path.join(SKILL, 'tests', 'make_fixtures.py')], check=True,
                       capture_output=True)
    blocks = os.path.join(work, 'blocks.json')
    subprocess.run([PY, os.path.join(SKILL, 'scripts', 'pdf_outline.py'), '--pdf', pdf,
                    '--out', work], check=True, capture_output=True)
    result = os.path.join(work, 'result.json')
    cp = subprocess.run([PY, os.path.join(SKILL, 'scripts', 'build_result.py'), '--case', case_path,
                         '--blocks', blocks, '--markdown', os.path.join(work, 'document.md'),
                         '--out', result], capture_output=True, text=True)
    if cp.returncode != 0 or not os.path.exists(result):
        tail = (cp.stderr or cp.stdout).strip().splitlines()
        return {'case': case_name, 'crashed': True, 'returncode': cp.returncode,
                'error': tail[-1] if tail else ''}
    d = json.load(open(result, encoding='utf-8'))
    return {'case': case_name, 'case_status': d.get('case_status', d.get('status')),
            'parse': d.get('parse', {}).get('status'),
            'answers': {k: ('<empty list>' if v == [] else ('<null>' if v is None else
                        (f'<{len(v)} items>' if isinstance(v, list) else v)))
                        for k, v in (d.get('answers') or {}).items()},
            'contract_fields': [{k: f.get(k) for k in ('field', 'required', 'role', 'type')}
                                for f in (d.get('contract') or {}).get('fields', [])],
            'pending': (d.get('contract') or {}).get('pending'),
            'warnings': (d.get('contract') or {}).get('warnings'),
            'issues': [i.get('rule') for i in (d.get('answer_issues') or [])]}


def main():
    os.makedirs(OUT, exist_ok=True)
    out = {}
    for case_name, tag in (('case4_tables_required_empty.json', 'case4'),
                           ('case5_properties_required.json', 'case5'),
                           ('case6_unrecognized_field.json', 'case6')):
        out[tag] = run(case_name, tag)
    with open(os.path.join(OUT, 'before.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
