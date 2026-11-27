"""Local acceptance tests for the reading skill (v4 vs v5), three cases.

Every case below runs the *same* inputs through v4's script and v5's script and
compares the produced result.json:

  case1  answer only on page 1 of a 2-page PDF   (citation page must be 1, not 2)
  case2  a value that lives in a table cell      (row/column/value must agree)
  case3  no tables, and the asked quantity is not reported
         (must separate no_such_object / not_reported / parse_failure)

Also runs the falsification checks: a wrong page, an invented number and a
table-cell answer without row/column must all be rejected.

Run:  python test_cases.py [--v4 DIR] [--v5 DIR]
Exit code 0 = all checks passed.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_V5 = os.path.abspath(os.path.join(HERE, '..'))
CASES = os.path.join(HERE, 'cases')
RUNS = os.path.join(HERE, 'runs', 'cases')

RESULTS = []


def record(name, ok, detail):
    RESULTS.append({'check': name, 'ok': bool(ok), 'detail': detail})
    print(('PASS  ' if ok else 'FAIL  ') + name + ('' if ok else f'  <- {detail}'))


def run(cmd):
    cp = subprocess.run(cmd, capture_output=True, text=True)
    if cp.returncode not in (0, 2):
        raise RuntimeError(f'command failed ({cp.returncode}): {" ".join(map(str, cmd))}\n'
                           f'{cp.stdout[-800:]}\n{cp.stderr[-800:]}')
    return cp


def ensure_fixtures(skill):
    fx = os.path.join(skill, 'tests', 'fixtures')
    if not os.path.exists(os.path.join(fx, 'two_column.pdf')):
        run([sys.executable, os.path.join(skill, 'tests', 'make_fixtures.py')])


def parse(skill, pdf, out):
    if os.path.isdir(out):
        shutil.rmtree(out)
    run([sys.executable, os.path.join(skill, 'scripts', 'pdf_outline.py'), '--pdf', pdf, '--out', out])
    return os.path.join(out, 'blocks.json')


def v4_result(skill, case_path, case, blocks, outdir):
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, 'result.json')
    run([sys.executable, os.path.join(skill, 'scripts', 'build_result.py'),
         '--case', case_path, '--blocks', blocks,
         '--markdown', os.path.join(os.path.dirname(blocks), 'document.md'), '--out', out])
    return json.load(open(out, encoding='utf-8'))


def v5_scaffold(case_path, case, blocks, outdir):
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, 'result.json')
    run([sys.executable, os.path.join(SKILL_V5, 'scripts', 'build_result.py'),
         '--case', case_path, '--blocks', blocks,
         '--markdown', os.path.join(os.path.dirname(blocks), 'document.md'), '--out', out])
    return out, json.load(open(out, encoding='utf-8'))


def v5_apply_and_validate(case_path, result_path, blocks, outdir, mode='good'):
    answers = os.path.join(outdir, f'answers_{mode}.json')
    run([sys.executable, os.path.join(HERE, 'simulate_agent.py'),
         '--case', case_path, '--blocks', blocks, '--out', answers, '--mode', mode])
    cp = run([sys.executable, os.path.join(SKILL_V5, 'scripts', 'build_result.py'),
              '--case', case_path, '--result', result_path,
              '--apply-answers', answers, '--validate'])
    return json.load(open(result_path, encoding='utf-8')), cp


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--v4', default=os.path.abspath(os.path.join(HERE, '..', '..', 'pkg-v4')))
    args = ap.parse_args()
    v4 = args.v4
    if not os.path.isdir(v4):
        print(f'v4 directory not found: {v4} (comparison will be skipped)')
        v4 = None
    os.makedirs(RUNS, exist_ok=True)
    ensure_fixtures(SKILL_V5)
    if v4:
        ensure_fixtures(v4)

    case_files = ['case1_page1.json', 'case2_table_cell.json', 'case3_no_table_not_reported.json',
                  'case4_tables_required_empty.json', 'case5_properties_required.json',
                  'case6_unrecognized_field.json']
    summary = {}
    for cf in case_files:
        case_path = os.path.join(CASES, cf)
        case = json.load(open(case_path, encoding='utf-8'))
        pdf = os.path.join(SKILL_V5, case['pdf'])
        tag = os.path.splitext(cf)[0]
        blocks = parse(SKILL_V5, pdf, os.path.join(RUNS, tag, 'parse'))

        # --- v4 (comparison only) ---
        r4 = None
        if v4:
            b4 = parse(v4, os.path.join(v4, case['pdf']), os.path.join(RUNS, tag, 'parse_v4'))
            r4 = v4_result(v4, case_path, case, b4, os.path.join(RUNS, tag, 'v4'))

        # --- v5: scaffold must not claim the case is done ---
        result_path, r5 = v5_scaffold(case_path, case, blocks, os.path.join(RUNS, tag, 'v5'))
        if tag.startswith('case4'):
            # a required structural field that is genuinely empty is already an answer:
            # the scaffold may be complete, but only with an explicit empty list
            record(f'{tag}: scaffold completes only through an explicit empty list',
                   r5['case_status'] == 'complete' and r5['answers'].get('tables') == [],
                   f"case_status={r5['case_status']} tables={r5['answers'].get('tables')!r}")
            notes = [f.get('note') for f in r5['contract']['fields'] if f['field'] == 'tables']
            record(f'{tag}: the empty list carries an explicit note',
                   bool(notes and notes[0]), f'note={notes[0] if notes else None}')
        else:
            record(f'{tag}: v5 scaffold is not "ok"',
                   r5['case_status'] == 'incomplete' and r5['parse']['status'] == 'ok',
                   f"case_status={r5['case_status']} parse={r5['parse']['status']}")
            record(f'{tag}: v5 scaffold lists the pending field',
                   'answer' in (r5['contract']['pending'] or []),
                   f"pending={r5['contract']['pending']}")

        if tag.startswith('case4'):
            # contract requires `tables`; the document has none -> [] must be accepted
            record(f'{tag}: empty table list accepted for a required field',
                   r5['case_status'] == 'complete' and r5['answers'].get('tables') == [],
                   f"status={r5['case_status']} tables={r5['answers'].get('tables')!r} "
                   f"issues={[i['rule'] for i in r5['answer_issues']]}")
            summary[tag] = {'v4': r4, 'v5': r5}
            continue

        if tag.startswith('case5'):
            # properties + required: keep every declared field, its type and its requiredness
            fields = {f['field']: f for f in r5['contract']['fields']}
            want = case['expected']['fields']
            ok = set(fields) == set(want) and all(
                fields[n]['required'] == w['required'] and fields[n]['type'] == w['type']
                for n, w in want.items())
            record(f'{tag}: properties+required keeps every field with its type and requiredness',
                   ok, f"fields={ {k: (v['type'], v['required']) for k, v in fields.items()} }")
            r5b, _ = v5_apply_and_validate(case_path, result_path, blocks, os.path.join(RUNS, tag, 'v5'))
            record(f'{tag}: optional fields stay optional, required answer completes the case',
                   r5b['case_status'] == 'complete',
                   f"status={r5b['case_status']} pending={r5b['contract']['pending']}")
            summary[tag] = {'v4': r4, 'v5': r5b}
            continue

        if tag.startswith('case6'):
            # an unreadable field declaration must be reported and must block "complete"
            warnings = r5['contract'].get('warnings') or []
            rules = [i['rule'] for i in r5['answer_issues']]
            record(f'{tag}: unrecognised declaration is reported, not dropped',
                   len(warnings) >= 1 and 'contract_unrecognized_field' in rules,
                   f'warnings={warnings} issues={rules}')
            r5b, _ = v5_apply_and_validate(case_path, result_path, blocks, os.path.join(RUNS, tag, 'v5'))
            record(f'{tag}: a valid answer still cannot make the case complete',
                   r5b['case_status'] != 'complete' and 'contract_unrecognized_field' in
                   [i['rule'] for i in r5b['answer_issues']],
                   f"status={r5b['case_status']} issues={[i['rule'] for i in r5b['answer_issues']]}")
            summary[tag] = {'v4': r4, 'v5': r5b}
            continue

        if tag.startswith('case3'):
            # blocked, with both reasons distinguishable
            r5b, _ = v5_apply_and_validate(case_path, result_path, blocks, os.path.join(RUNS, tag, 'v5'))
            kinds = [e.get('kind') for e in (r5b.get('missing_information') or [])]
            record(f'{tag}: v5 reports blocked, not complete',
                   r5b['case_status'] == 'blocked', f"case_status={r5b['case_status']}")
            record(f'{tag}: distinguishes no_such_object and not_reported',
                   'no_such_object' in kinds and 'not_reported' in kinds, f'kinds={kinds}')
            record(f'{tag}: does not invent a number',
                   (r5b['answers'].get('answer') in (None, '')) and not r5b['answer_issues'],
                   f"answer={r5b['answers'].get('answer')!r} issues={r5b['answer_issues']}")
            summary[tag] = {'v4': r4, 'v5': r5b}
            continue

        # --- v5: good answers -> complete, with page-correct evidence ---
        r5g, _ = v5_apply_and_validate(case_path, result_path, blocks, os.path.join(RUNS, tag, 'v5'))
        exp = case['expected']
        ans = (r5g['answers'] or {}).get('answer') or {}
        ev = ans.get('evidence') or []
        record(f'{tag}: v5 completes after validated answers',
               r5g['case_status'] == 'complete', f"case_status={r5g['case_status']} issues={r5g['answer_issues']}")
        record(f'{tag}: answer value',
               str(exp['answer_contains']) in str(ans.get('value')),
               f"value={ans.get('value')!r} expected contains {exp['answer_contains']!r}")
        record(f'{tag}: evidence points at the right page',
               bool(ev) and ev[0].get('page') == exp['page'],
               f"evidence pages={[e.get('page') for e in ev]} expected {exp['page']}")
        if exp.get('table_column'):
            loc = ans.get('locator_resolved') or {}
            record(f'{tag}: table row/column/value agree',
                   loc.get('row') == exp['table_row'] and loc.get('column') == exp['table_column']
                   and str(loc.get('value')) == str(exp['answer_contains']),
                   f'locator_resolved={loc}')

        # --- falsification: wrong page ---
        r5w, _ = v5_apply_and_validate(case_path, result_path, blocks,
                                       os.path.join(RUNS, tag, 'v5'), mode='wrong_page')
        rules = [i['rule'] for i in r5w['answer_issues']]
        record(f'{tag}: wrong citation page is rejected',
               r5w['case_status'] != 'complete' and rules, f'issues={rules}')

        # --- falsification: invented number ---
        r5n, _ = v5_apply_and_validate(case_path, result_path, blocks,
                                       os.path.join(RUNS, tag, 'v5'), mode='wrong_number')
        rules = [i['rule'] for i in r5n['answer_issues']]
        record(f'{tag}: number not in evidence is rejected',
               r5n['case_status'] != 'complete' and 'number_not_in_evidence' in rules,
               f'issues={rules}')

        if exp.get('table_column'):
            r5c, _ = v5_apply_and_validate(case_path, result_path, blocks,
                                           os.path.join(RUNS, tag, 'v5'), mode='no_row_column')
            rules = [i['rule'] for i in r5c['answer_issues']]
            record(f'{tag}: table answer without row/column is rejected',
                   r5c['case_status'] != 'complete' and 'table_cell_row_column' in rules,
                   f'issues={rules}')

        # after a rejected attempt, a corrected answer must bring the case back to
        # complete — the status follows the evidence, not the history
        r5final, _ = v5_apply_and_validate(case_path, result_path, blocks,
                                           os.path.join(RUNS, tag, 'v5'), mode='good')
        record(f'{tag}: re-answering updates the status back to complete',
               r5final['case_status'] == 'complete', f"case_status={r5final['case_status']}")

        summary[tag] = {'v4': r4, 'v5': r5final}

    # legacy capabilities must still work
    legacy = subprocess.run([sys.executable, os.path.join(HERE, 'selftest.py')],
                            capture_output=True, text=True)
    ok_legacy = legacy.returncode == 0 and 'order_inv=0' in legacy.stdout
    record('legacy parsing self-test still passes', ok_legacy, legacy.stdout.strip()[-200:])

    report = {'checks': RESULTS, 'cases': summary,
              'passed': sum(1 for r in RESULTS if r['ok']), 'total': len(RESULTS)}
    with open(os.path.join(RUNS, 'report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"\n{report['passed']}/{report['total']} checks passed")
    return 0 if report['passed'] == report['total'] else 1


if __name__ == '__main__':
    sys.exit(main())
