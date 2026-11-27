"""Build the v4-vs-v5 comparison table and refresh the examples.

Reads tests/runs/cases/*/{v4,v5}/result.json and writes tests/v4_vs_v5_report.md.
Also regenerates examples/ from the same pipeline so the documented example can
never contradict itself again.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.abspath(os.path.join(HERE, '..'))
RUNS = os.path.join(HERE, 'runs', 'cases')
EX = os.path.join(SKILL, 'examples')
CASES = os.path.join(HERE, 'cases')
PY = sys.executable


def load(p, default=None):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def brief(r, case):
    if not r:
        return {'status': None}
    ans = (r.get('answers') or {}).get('answer')
    if isinstance(ans, dict):
        ev = ans.get('evidence') or []
        return {'status': r.get('status'), 'value': ans.get('value'),
                'evidence_pages': [e.get('page') for e in ev],
                'locator': ans.get('locator_resolved') or ans.get('locator'),
                'missing': [m.get('kind') for m in (r.get('missing_information') or [])]}
    return {'status': r.get('status'), 'value': ans,
            'missing': [m.get('kind') for m in (r.get('missing_information') or [])],
            'unfilled': (r.get('output_contract') or {}).get('unfilled')}


def main():
    lines = ['# v4 vs v5 — local comparison (same inputs)', '']
    lines.append('| case | question | expected | v4 result | v5 result | v5 evidence |')
    lines.append('|---|---|---|---|---|---|')
    for name in ('case1_page1', 'case2_table_cell', 'case3_no_table_not_reported'):
        case = load(os.path.join(CASES, name + '.json'), {})
        r4 = load(os.path.join(RUNS, name, 'v4', 'result.json'))
        r5 = load(os.path.join(RUNS, name, 'v5', 'result.json'))
        b4, b5 = brief(r4, case), brief(r5, case)
        exp = case.get('expected') or {}
        exp_txt = exp.get('answer_contains') or ('no number; explain why' if 'no_table' in name else '')
        ev = ''
        if b5.get('evidence_pages') or b5.get('missing'):
            pages = b5.get('evidence_pages') or []
            ev = (f"p.{pages[0]}" if len(pages) == 1 else f"p.{pages}") if pages \
                else f"missing_information={b5.get('missing')}"
            loc = b5.get('locator')
            if isinstance(loc, dict) and loc.get('row'):
                ev += f" · row={loc.get('row')!r} col={loc.get('column')!r} value={loc.get('value')!r}"
        lines.append(f"| {name} | {case.get('task')} | {exp_txt} | "
                     f"status={b4.get('status')}, answer={b4.get('value')!r}, "
                     f"unfilled={b4.get('unfilled')} | status={b5.get('status')}, "
                     f"answer={b5.get('value')!r} | {ev} |")
    lines.append('')

    # examples: same pipeline, regenerated so the docs cannot contradict themselves
    case_path = os.path.join(EX, 'case.example.json')
    case = load(case_path, {})
    blocks = os.path.join(RUNS, 'case2_table_cell', 'parse', 'blocks.json')
    tmp = os.path.join(HERE, 'runs', 'examples')
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    os.makedirs(tmp, exist_ok=True)
    scaffold = os.path.join(tmp, 'example_result.incomplete.json')
    subprocess.run([PY, os.path.join(SKILL, 'scripts', 'build_result.py'), '--case', case_path,
                    '--blocks', blocks, '--markdown',
                    os.path.join(RUNS, 'case2_table_cell', 'parse', 'document.md'),
                    '--out', scaffold], check=True, capture_output=True, text=True)
    answers = os.path.join(tmp, 'answers.json')
    subprocess.run([PY, os.path.join(HERE, 'simulate_agent.py'), '--case', case_path,
                    '--blocks', blocks, '--out', answers], check=True, capture_output=True, text=True)
    final = os.path.join(tmp, 'example_result.json')
    shutil.copy(scaffold, final)
    subprocess.run([PY, os.path.join(SKILL, 'scripts', 'build_result.py'), '--case', case_path,
                    '--result', final, '--apply-answers', answers, '--validate'],
                   check=True, capture_output=True, text=True)
    for src, dst in ((scaffold, 'example_result.incomplete.json'), (final, 'example_result.json'),
                     (answers, 'answers.example.json')):
        shutil.copy(src, os.path.join(EX, dst))

    final_doc = load(final, {})
    lines.append('## Example in `examples/`')
    lines.append('')
    lines.append(f"- `example_result.incomplete.json`: status `{load(scaffold, {}).get('status')}` "
                 f"(parse done, answer pending) — the state before the agent answers")
    lines.append(f"- `example_result.json`: status `{final_doc.get('status')}`, "
                 f"answer `{(final_doc.get('answers') or {}).get('answer', {}).get('value')!r}`, "
                 f"evidence `{[e.get('page') for e in ((final_doc.get('answers') or {}).get('answer') or {}).get('evidence', [])]}`")
    lines.append(f"- `answers.example.json`: the agent answers that turn one into the other")
    with open(os.path.join(HERE, 'v4_vs_v5_report.md'), 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')
    print('\n'.join(lines))


if __name__ == '__main__':
    main()
