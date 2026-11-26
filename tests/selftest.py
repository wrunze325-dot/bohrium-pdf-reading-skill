"""Fair self-test: baseline text extraction vs this skill, same input and task.

Both conditions read the same fixture PDFs and answer the same questions
("what is the reading order", "where does each element sit", "what does the table
and the formula contain"). The report is written to selftest_report.json/.md.

Run:  python selftest.py [--pdf fixtures/two_column.pdf] [--out OUTDIR]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), 'scripts')
FIXTURES = os.path.join(HERE, 'fixtures')

TRUTH_PATH = os.path.join(FIXTURES, 'truth.json')


# ------------------------------------------------------------------ baselines
def baseline_pypdf(pdf_path):
    from pypdf import PdfReader
    t0 = time.time()
    reader = PdfReader(pdf_path)
    lines = []
    for pno, page in enumerate(reader.pages):
        text = page.extract_text() or ''
        for ln in text.splitlines():
            if ln.strip():
                lines.append({'page': pno + 1, 'text': ln.strip()})
    return {'name': 'baseline:pypdf.extract_text', 'lines': lines,
            'seconds': round(time.time() - t0, 3), 'blocks': None, 'warnings': []}


def baseline_pymupdf(pdf_path):
    import pymupdf
    t0 = time.time()
    doc = pymupdf.open(pdf_path)
    lines = []
    for pno in range(doc.page_count):
        text = doc[pno].get_text('text')
        for ln in text.splitlines():
            if ln.strip():
                lines.append({'page': pno + 1, 'text': ln.strip()})
    return {'name': 'baseline:pymupdf.get_text(text)', 'lines': lines,
            'seconds': round(time.time() - t0, 3), 'blocks': None, 'warnings': []}


def run_skill(pdf_path, outdir, ocr='auto'):
    t0 = time.time()
    cmd = [sys.executable, os.path.join(SCRIPTS, 'pdf_outline.py'),
           '--pdf', pdf_path, '--out', outdir, '--ocr', ocr, '--json']
    cp = subprocess.run(cmd, capture_output=True, text=True)
    seconds = round(time.time() - t0, 3)
    path = os.path.join(outdir, 'blocks.json')
    if not os.path.exists(path):
        return {'name': 'skill:pdf_outline', 'lines': [], 'seconds': seconds,
                'blocks': None, 'warnings': ['skill failed: ' + cp.stderr[-500:]],
                'returncode': cp.returncode}
    data = json.load(open(path, encoding='utf-8'))
    lines = []
    for b in data['blocks']:
        if b.get('type') == 'figure_label':
            continue
        lines.append({'page': b['page'], 'text': b.get('text') or '',
                      'type': b.get('type'), 'bbox': b.get('bbox'),
                      'locator': b.get('locator')})
    return {'name': 'skill:pdf_outline', 'lines': lines, 'seconds': seconds,
            'blocks': data, 'warnings': [u.get('reason') for u in data.get('uncertainties', [])],
            'status': data.get('status'), 'returncode': cp.returncode}


# -------------------------------------------------------------------- metrics
def _find(text, needle):
    return text.find(needle)


TABLE_TOKENS = ('T1R1C0', 'T1R1C1', 'T1RC0', 'T1RC1')
PERIPHERY_TOKENS = ('HDR1', 'HDR2', 'PG1', 'PG2')


def order_metrics(lines, elements, truth_order=None):
    """Inversions, cross-column alternations and splice-prone joins.

    Table row tokens are scored by table_metrics instead: a table that spans pages
    is reported once, at the page it starts on, so those rows are not part of the
    linear order comparison.
    """
    joined = '\n'.join(l['text'] for l in lines)
    pos = {}
    for el in elements:
        i = _find(joined, el['text'])
        if i >= 0:
            pos[el['token']] = i
    if truth_order:
        by_token = {el['token']: el for el in elements}
        ordered = [by_token[t] for t in truth_order if t in by_token]
    else:
        ordered = list(elements)
    seq = [el['token'] for el in ordered if el['token'] in pos
           and el['token'] not in TABLE_TOKENS]
    inv = 0
    for a in range(len(seq)):
        for b in range(a + 1, len(seq)):
            if pos[seq[a]] > pos[seq[b]]:
                inv += 1
    total_pairs = max(len(seq) * (len(seq) - 1) // 2, 1)

    # which column does each output line belong to
    col_of_line = []
    for l in lines:
        cols = set()
        for el in elements:
            if el['text'] in l['text'] and el.get('column') in ('left', 'right'):
                cols.add(el['column'])
        col_of_line.append(cols)
    alternations = 0
    splice_prone = 0
    for i in range(len(lines) - 1):
        a, b = col_of_line[i], col_of_line[i + 1]
        if a and b and a != b and (a | b) == {'left', 'right'}:
            alternations += 1
            tail = lines[i]['text'].rstrip()
            head = lines[i + 1]['text'].lstrip()
            if tail and head and tail[-1] not in '.!?:;"' and head[:1].islower():
                splice_prone += 1
    missing = [el['token'] for el in ordered if el['token'] not in pos]
    return {
        'tokens_found': len(pos), 'tokens_total': len(elements), 'missing': missing,
        'order_inversions': inv, 'order_pairs': total_pairs,
        'order_inversion_rate': round(inv / total_pairs, 4),
        'column_alternations': alternations,
        'splice_prone_joins': splice_prone,
    }


def evidence_metrics(blocks_doc, elements, pdf_path):
    """Does each element point back to the right page AND a bbox that contains it?"""
    import pymupdf
    doc = pymupdf.open(pdf_path)
    blocks = [b for b in (blocks_doc or {}).get('blocks', []) if b.get('type') != 'figure_label']
    hits = {'page_ok': 0, 'bbox_ok': 0, 'text_ok': 0, 'total': 0, 'detail': []}
    for el in elements:
        if el['kind'] in ('header', 'footer'):
            continue
        hits['total'] += 1
        page = doc[el['page'] - 1]
        try:
            rects = page.search_for(el['text'])
        except Exception:
            rects = []
        true_rect = [rects[0].x0, rects[0].y0, rects[0].x1, rects[0].y1] if rects else None
        cand = None
        if el.get('cells'):
            for b in blocks:
                if b.get('type') != 'table':
                    continue
                flat = [str(c) for c in (b.get('header') or [])]
                flat += [str(c) for row in (b.get('rows') or []) for c in row]
                if all(str(c) in flat for c in el['cells']):
                    cand = b
                    break
        else:
            for b in blocks:
                if el['text'] in (b.get('text') or ''):
                    cand = b
                    break
        if cand is None:
            hits['detail'].append({'token': el['token'], 'found': False})
            continue
        hits['text_ok'] += 1
        page_ok = (cand['page'] == el['page'] or
                   el['page'] in (cand.get('spans_pages') or []))
        hits['page_ok'] += 1 if page_ok else 0
        bbox_ok = False
        if el.get('cells'):
            bbox_ok = bool(cand.get('header_bbox') or cand.get('row_cells'))
        elif true_rect and cand.get('bbox'):
            bb = cand['bbox']
            ix = max(0, min(bb[2], true_rect[2]) - max(bb[0], true_rect[0]))
            iy = max(0, min(bb[3], true_rect[3]) - max(bb[1], true_rect[1]))
            area = max((true_rect[2] - true_rect[0]) * (true_rect[3] - true_rect[1]), 1e-6)
            bbox_ok = (ix * iy) / area >= 0.6
        hits['bbox_ok'] += 1 if bbox_ok else 0
        hits['detail'].append({'token': el['token'], 'found': True, 'page_ok': page_ok,
                               'bbox_ok': bbox_ok, 'page': cand['page'],
                               'bbox': cand.get('bbox'),
                               'true_bbox': [round(v, 1) for v in true_rect] if true_rect else None})
    return hits


def table_metrics(blocks_doc, truth):
    tbl = truth['tables']['table_1']
    tables = (blocks_doc or {}).get('tables') or []
    if not tables:
        return {'found': False, 'rows_total': len(tbl['rows']),
                'note': 'plain text cannot recover cell structure'}
    t = tables[0]
    header_ok = t.get('header') == tbl['header']
    rows_ok = [r == expected for r, expected in zip(t.get('rows') or [], tbl['rows'])]
    pages_ok = (t.get('spans_pages') or []) == tbl['spans_pages']
    return {
        'found': True,
        'number': t.get('number'),
        'header_ok': header_ok,
        'header': t.get('header'),
        'rows': t.get('rows'),
        'rows_correct': sum(1 for v in rows_ok if v),
        'rows_total': len(tbl['rows']),
        'spans_pages': t.get('spans_pages'),
        'pages_ok': pages_ok,
        'row_pages': t.get('row_pages'),
        'cell_provenance': bool(t.get('cell_bbox')),
    }


def formula_metrics(blocks_doc, truth):
    exp = truth['formulas']['FORM1']
    forms = (blocks_doc or {}).get('formulas') or []
    if not forms:
        return {'found': False, 'note': 'plain text keeps symbols but no script structure'}
    f = forms[0]
    rich = f.get('rich_text') or ''
    sups = [s['text'] for s in (f.get('scripts') or []) if s.get('type') == 'sup']
    subs = [s['text'] for s in (f.get('scripts') or []) if s.get('type') == 'sub']
    return {
        'found': True,
        'text': f.get('text'),
        'rich_text': rich,
        'number_ok': (f.get('formula_number') or '') == exp['number'].strip('()'),
        'number': f.get('formula_number'),
        'superscripts_ok': exp['superscripts'][0] in ' '.join(sups),
        'superscripts': sups,
        'subscripts': subs,
        'greek_ok': all(g in (f.get('text') or '') for g in exp['greek']),
        'greek': f.get('greek'),
        'page': f.get('page'),
        'bbox': f.get('bbox'),
    }


def scanned_check(pdf_path, outdir):
    res = run_skill(pdf_path, outdir, ocr='auto')
    doc = res.get('blocks') or {}
    unc = [u.get('kind') for u in doc.get('uncertainties', [])]
    return {
        'status': doc.get('status'),
        'uncertainty_kinds': unc,
        'honest_no_text_layer': any(k in unc for k in ('no_text_layer', 'ocr_unavailable',
                                                       'ocr_failed', 'unreadable_page')),
        'blocks': len(doc.get('blocks') or []),
        'seconds': res['seconds'],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--pdf', default=os.path.join(FIXTURES, 'two_column.pdf'))
    ap.add_argument('--out', default=os.path.join(HERE, 'runs', 'latest'))
    ap.add_argument('--skip-baseline-pymupdf', action='store_true')
    args = ap.parse_args()
    try:
        os.makedirs(args.out, exist_ok=True)
    except OSError:
        import tempfile
        args.out = tempfile.mkdtemp(prefix='pdfoutline-selftest-')
        print('tests/ is not writable, writing run output to', args.out)
    truth = json.load(open(TRUTH_PATH, encoding='utf-8'))
    elements = truth['elements']
    body_tokens = truth['reading_order']
    order_tokens = [t for t in body_tokens
                    if t not in TABLE_TOKENS and t not in PERIPHERY_TOKENS]

    conds = [baseline_pypdf(args.pdf), run_skill(args.pdf, os.path.join(args.out, 'skill'))]
    if not args.skip_baseline_pymupdf:
        conds.insert(1, baseline_pymupdf(args.pdf))

    report = {'fixture': os.path.basename(args.pdf), 'conditions': []}
    for cond in conds:
        m = {
            'name': cond['name'],
            'seconds': cond['seconds'],
            'returncode': cond.get('returncode'),
            'order': order_metrics(cond['lines'], elements, order_tokens),
            'warnings': cond.get('warnings') or [],
            'status': cond.get('status'),
        }
        if cond.get('blocks'):
            m['evidence'] = evidence_metrics(cond['blocks'], elements, args.pdf)
            m['table'] = table_metrics(cond['blocks'], truth)
            m['formula'] = formula_metrics(cond['blocks'], truth)
            m['layout'] = cond['blocks'].get('layout')
        else:
            m['evidence'] = {'page_ok': 0, 'bbox_ok': 0, 'text_ok': 0,
                             'total': sum(1 for e in elements if e['kind'] not in ('header', 'footer')),
                             'detail': [], 'note': 'plain text has no block-level provenance'}
            m['table'] = {'found': False, 'note': 'plain text cannot recover cell structure'}
            m['formula'] = {'found': False, 'note': 'plain text keeps symbols but no script structure'}
        report['conditions'].append(m)

    single_pdf = os.path.join(FIXTURES, 'single_column.pdf')
    if os.path.exists(single_pdf):
        sr = run_skill(single_pdf, os.path.join(args.out, 'single'))
        sdoc = sr.get('blocks') or {}
        body_text = '\n'.join(b.get('text', '') for b in sdoc.get('blocks', []))
        lines = truth.get('single_column', {}).get('expected_lines') or []
        report['single_column'] = {
            'columns': (sdoc.get('layout') or {}).get('columns'),
            'expected_columns': 1,
            'body_blocks': sum(1 for b in sdoc.get('blocks', []) if b.get('type') == 'body'),
            'lines_in_order': all(lines[i] in body_text for i in range(len(lines))),
            'status': sdoc.get('status'),
            'seconds': sr['seconds'],
        }

    scanned_pdf = os.path.join(FIXTURES, 'scanned.pdf')
    if os.path.exists(scanned_pdf):
        report['scanned'] = scanned_check(scanned_pdf, os.path.join(args.out, 'scanned'))
        try:
            from pypdf import PdfReader
            r = PdfReader(scanned_pdf)
            txt = (r.pages[0].extract_text() or '').strip()
            report['scanned']['baseline_text_chars'] = len(txt)
            report['scanned']['baseline_silently_empty'] = len(txt) < 20
        except Exception as exc:
            report['scanned']['baseline_error'] = str(exc)

    with open(os.path.join(args.out, 'selftest_report.json'), 'w', encoding='utf-8') as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    try:
        write_markdown(report, os.path.join(HERE, 'selftest_report.md'))
    except OSError:
        write_markdown(report, os.path.join(args.out, 'selftest_report.md'))
        print('tests/ is not writable, report written to', args.out)
    print_summary(report)
    return report


def write_markdown(report, path):
    L = []
    L.append('# Self-test: baseline vs skill\n')
    L.append(f'Fixture: `{report["fixture"]}`. Same PDF, same questions, same machine.\n')
    L.append('| metric | ' + ' | '.join(c['name'] for c in report['conditions']) + ' |')
    L.append('|---' * (len(report['conditions']) + 1) + '|')
    def _sv(v):
        return 'n/a' if v is None else str(v)

    def row(label, fn):
        L.append(f'| {label} | ' + ' | '.join(fn(c) for c in report['conditions']) + ' |')
    row('wall seconds', lambda c: f'{c["seconds"]}')
    row('tokens found', lambda c: f'{c["order"]["tokens_found"]}/{c["order"]["tokens_total"]}')
    row('order inversions', lambda c: f'{c["order"]["order_inversions"]} (rate {c["order"]["order_inversion_rate"]})')
    row('cross-column alternations', lambda c: f'{c["order"]["column_alternations"]}')
    row('splice-prone joins', lambda c: f'{c["order"]["splice_prone_joins"]}')
    row('elements with a page', lambda c: f'{c["evidence"]["page_ok"]}/{c["evidence"]["total"]}')
    row('elements with a containing bbox', lambda c: f'{c["evidence"]["bbox_ok"]}/{c["evidence"]["total"]}')
    row('table structure recovered', lambda c: 'yes' if c['table'].get('found') else 'no')
    row('table header correct', lambda c: _sv(c['table'].get('header_ok')))
    row('table rows correct', lambda c: f'{c["table"].get("rows_correct", 0)}/{c["table"].get("rows_total", 0)}')
    row('cross-page table merge', lambda c: _sv(c['table'].get('pages_ok')))
    row('formula number kept', lambda c: _sv(c['formula'].get('number_ok')))
    row('formula superscripts kept', lambda c: _sv(c['formula'].get('superscripts_ok')))
    L.append('')
    if 'single_column' in report:
        sc = report['single_column']
        L.append('## Single-column control page\n')
        L.append(f'- columns detected `{sc["columns"]}` (expected {sc["expected_columns"]}), '
                 f'body blocks {sc["body_blocks"]}, all fixture lines present in order: '
                 f'{sc["lines_in_order"]}, {sc["seconds"]} s\n')
    if 'scanned' in report:
        s = report['scanned']
        L.append('## Scanned page (no text layer)\n')
        L.append(f'- skill status `{s["status"]}`, uncertainties {s["uncertainty_kinds"]}, '
                 f'blocks {s["blocks"]}, {s["seconds"]} s')
        L.append(f'- baseline pypdf text: {s.get("baseline_text_chars")} chars, '
                 f'silently empty: {s.get("baseline_silently_empty")}')
        L.append(f'- skill reports the gap instead of inventing text: {s["honest_no_text_layer"]}\n')
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(L))


def print_summary(report):
    for c in report['conditions']:
        o = c['order']
        print(f"{c['name']}: {c['seconds']}s order_inv={o['order_inversions']} "
              f"alt={o['column_alternations']} splice={o['splice_prone_joins']} "
              f"ev_page={c['evidence']['page_ok']}/{c['evidence']['total']} "
              f"table={c['table'].get('found')} formula={c['formula'].get('found')}")


if __name__ == '__main__':
    main()
