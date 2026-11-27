"""Reproduce the defect in v4 before touching it.

Claim under test: v4 writes status="ok" to result.json even when a field the case
declares as required (`answer`) is still empty.

Run:  python reproduce_bug.py [SKILL_DIR]
Exit code 0 = the defect was reproduced (as expected).
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, 'cases')
SKILL = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(HERE, '..'))
OUT = os.path.join(HERE, 'runs', 'reproduce')


def ensure_fixtures(skill):
    """A clean install ships no binary fixtures: generate them on first use."""
    fixtures = os.path.join(skill, 'tests', 'fixtures')
    if not os.path.exists(os.path.join(fixtures, 'two_column.pdf')):
        subprocess.run([sys.executable, os.path.join(skill, 'tests', 'make_fixtures.py')],
                       check=True, capture_output=True, text=True)


def main():
    ensure_fixtures(SKILL)
    case_path = os.path.join(CASES, 'case1_page1.json')
    case = json.load(open(case_path, encoding='utf-8'))
    pdf = os.path.join(SKILL, case['pdf'])
    os.makedirs(OUT, exist_ok=True)
    parse_out = os.path.join(OUT, 'parse')
    subprocess.run([sys.executable, os.path.join(SKILL, 'scripts', 'pdf_outline.py'),
                    '--pdf', pdf, '--out', parse_out], check=True,
                   capture_output=True, text=True)
    result_path = os.path.join(OUT, 'result.json')
    subprocess.run([sys.executable, os.path.join(SKILL, 'scripts', 'build_result.py'),
                    '--case', case_path, '--blocks', os.path.join(parse_out, 'blocks.json'),
                    '--markdown', os.path.join(parse_out, 'document.md'),
                    '--out', result_path], check=True, capture_output=True, text=True)
    result = json.load(open(result_path, encoding='utf-8'))

    answers = result.get('answers') or {}
    unfilled = (result.get('output_contract') or {}).get('unfilled') or []
    status = result.get('status')

    print(json.dumps({'status': status, 'answer': answers.get('answer'),
                      'unfilled': unfilled}, ensure_ascii=False))

    is_v5 = 'case_status' in result
    if is_v5:
        # v5 must NOT reproduce it: parse done, case still incomplete
        fixed = (result.get('case_status') in ('incomplete', 'blocked')
                 and answers.get('answer') is None)
        print('v5 check: ' + ('PASS - case_status is not "ok" while the answer is empty'
                              if fixed else 'FAIL - v5 still claims the case is done'))
        return 0 if fixed else 1

    reproduced = status == 'ok' and answers.get('answer') is None and 'answer' in unfilled
    if reproduced:
        print('DEFECT REPRODUCED: status=ok while a required answer is still empty')
        return 0
    print('not reproduced')
    return 1


if __name__ == '__main__':
    sys.exit(main())
