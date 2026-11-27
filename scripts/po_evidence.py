"""Evidence and answer validation.

Two jobs:

1. Resolve what an agent wrote into a verifiable locator. Give a page and a verbatim
   quote; this module finds the block, checks the quote really occurs on that page,
   and fills the bbox. A page number without a quote is not evidence.

2. Decide whether an answer is allowed to stand. It is rejected when
     * the cited page is out of range (e.g. the document's total page count used
       as a citation page),
     * the quote does not occur verbatim on the cited page,
     * the answer contains a number that no evidence quote contains,
     * a table-cell answer has no row/column, or the table/row/column is not there,
     * a "missing information" claim is not backed by a recorded check.

Nothing here guesses a value: every rejection names the rule it failed.
"""
from __future__ import annotations

import re
import unicodedata

NUM_RE = re.compile(r'(?<![\w.])-?\d[\d,]*(?:\.\d+)?(?![\w.])')
KINDS = ('no_such_object', 'not_reported', 'parse_failure')


def norm(s):
    """Whitespace/typography-insensitive form used for verbatim quote checks."""
    if s is None:
        return ''
    s = unicodedata.normalize('NFKC', str(s))
    s = s.replace('\u00ad', '').replace('\u2010', '-').replace('\u2011', '-')
    s = s.replace('\u2012', '-').replace('\u2013', '-').replace('\u2014', '-')
    s = s.replace('\u2018', "'").replace('\u2019', "'")
    s = s.replace('\u201c', '"').replace('\u201d', '"')
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()


def numbers_in(s):
    out = set()
    for m in NUM_RE.findall(norm(s)):
        out.add(m.replace(',', ''))
    return out


def block_pages(blocks_doc):
    """{page: [block dicts]} for text-bearing blocks."""
    out = {}
    for b in blocks_doc.get('blocks') or []:
        out.setdefault(b.get('page'), []).append(b)
    return out


def find_quote(blocks_doc, quote, page=None):
    """Locate a verbatim quote. Returns list of hits {page, block_id, bbox, text, locator}."""
    q = norm(quote)
    if not q:
        return []
    hits = []
    for b in blocks_doc.get('blocks') or []:
        if b.get('type') == 'table':
            # a table can start on one page and continue on the next, so the page
            # filter has to be applied per row, not to the table block
            rows = b.get('rows') or []
            pages = b.get('row_pages') or [b.get('page')] * len(rows)
            for ri, row in enumerate(rows):
                row_page = pages[ri] if ri < len(pages) else b.get('page')
                if page is not None and row_page != page:
                    continue
                joined = ' | '.join(str(c) for c in row)
                if q in norm(joined):
                    hits.append({'page': row_page, 'block_id': b.get('id'),
                                 'bbox': _row_bbox(b, ri), 'text': joined,
                                 'locator': b.get('locator'), 'kind': 'table_row',
                                 'row_index': ri})
                    break
            else:
                head = ' | '.join(str(c) for c in (b.get('header') or []))
                head_page = b.get('header_page') or b.get('page')
                if q and q in norm(head) and (page is None or head_page == page):
                    hits.append({'page': head_page, 'block_id': b.get('id'),
                                 'bbox': b.get('bbox'), 'text': head,
                                 'locator': b.get('locator'), 'kind': 'table_header'})
            continue
        if page is not None and b.get('page') != page:
            continue
        text = norm(b.get('text') or '')
        if not text:
            continue
        if q in text:
            hits.append({'page': b['page'], 'block_id': b.get('id'),
                         'bbox': b.get('bbox'), 'text': b.get('text'),
                         'locator': b.get('locator'), 'kind': b.get('type')})
    return hits


def _row_bbox(table_block, row_index):
    cells = (table_block.get('row_cells') or [])
    if row_index < len(cells):
        boxes = [c.get('bbox_page') for c in (cells[row_index] or []) if c and c.get('bbox_page')]
        if boxes:
            return [min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes)]
    return table_block.get('bbox')


def document_facts(blocks_doc):
    tables = blocks_doc.get('tables') or []
    return {
        'pages': (blocks_doc.get('source') or {}).get('pages'),
        'parse_status': blocks_doc.get('status'),
        'blocks': len(blocks_doc.get('blocks') or []),
        'tables': len(tables),
        'table_numbers': [t.get('number') for t in tables],
        'formulas': len(blocks_doc.get('formulas') or []),
        'figures': len(blocks_doc.get('figures') or []),
        'uncertainties': [u.get('kind') for u in (blocks_doc.get('uncertainties') or [])],
        'backend': (blocks_doc.get('source') or {}).get('backend'),
    }


def search_document(blocks_doc, query):
    """Count occurrences and list pages, so 'not reported' is a checked claim."""
    q = norm(query)
    pages = []
    count = 0
    for b in blocks_doc.get('blocks') or []:
        if q and q in norm(b.get('text') or ''):
            pages.append(b.get('page'))
            count += norm(b.get('text') or '').count(q)
    return {'query': query, 'hits': count, 'pages': sorted(set(pages))}


def resolve_answer(amount, blocks_doc):
    """Normalise one agent answer into the stored shape, resolving quotes to blocks.

    Returns (answer_dict, issues).
    """
    issues = []
    if isinstance(amount, str):
        amount = {'value': amount}
    if not isinstance(amount, dict):
        return None, [{'field': None, 'rule': 'answer_shape',
                       'detail': 'answer must be an object or a string'}]
    ans = dict(amount)
    pages = (blocks_doc.get('source') or {}).get('pages') or 0
    ev_in = ans.get('evidence')
    if isinstance(ev_in, dict):
        ev_in = [ev_in]
    ev_out = []
    for item in ev_in or []:
        if not isinstance(item, dict):
            continue
        page = item.get('page')
        quote = item.get('quote')
        if not isinstance(page, int):
            try:
                page = int(page)
            except (TypeError, ValueError):
                page = None
        hits = find_quote(blocks_doc, quote, page)
        if hits:
            h = hits[0]
            ev_out.append({
                'page': h['page'],
                'block_id': item.get('block_id') or h['block_id'],
                'quote': item.get('quote'),
                'bbox': h['bbox'],
                'locator': h['locator'],
                'matched_text': h['text'][:400],
                'kind': h['kind'],
            })
        else:
            ev_out.append({
                'page': page,
                'block_id': item.get('block_id'),
                'quote': quote,
                'bbox': item.get('bbox'),
                'locator': item.get('locator'),
                'matched_text': None,
                'resolved': False,
            })
    ans['evidence'] = ev_out
    if ans.get('type') == 'table_cell':
        loc = ans.get('locator') or {}
        if not (loc.get('row') and loc.get('column')):
            issues.append({'field': None, 'rule': 'table_cell_row_column',
                           'detail': 'a table-cell answer needs locator.row and locator.column'})
    return ans, issues


def validate_answer(field, ans, blocks_doc, contract_type=None):
    """Return a list of issues for one answer; empty list means it may stand."""
    issues = []
    if ans is None:
        return [{'field': field, 'rule': 'missing_answer', 'detail': 'no answer was provided'}]
    if isinstance(ans, (str, int, float)):
        ans = {'value': ans}
    if not isinstance(ans, dict):
        return [{'field': field, 'rule': 'answer_shape', 'detail': 'answer must be an object'}]
    value = ans.get('value')
    if value is None or (isinstance(value, str) and not value.strip()):
        issues.append({'field': field, 'rule': 'missing_value', 'detail': 'answer has no value'})

    pages = (blocks_doc.get('source') or {}).get('pages') or 0
    evidence = ans.get('evidence') or []
    if not evidence:
        issues.append({'field': field, 'rule': 'no_evidence',
                       'detail': 'an answer without evidence is not accepted'})
    for ev in evidence:
        page = ev.get('page')
        if not isinstance(page, int) or page < 1 or (pages and page > pages):
            issues.append({'field': field, 'rule': 'page_out_of_range',
                           'detail': f'cited page {page!r} is outside 1..{pages}'})
            continue
        quote = ev.get('quote')
        if not quote or not str(quote).strip():
            issues.append({'field': field, 'rule': 'quote_required',
                           'detail': f'page {page} has no verbatim quote'})
            continue
        hits = find_quote(blocks_doc, quote, page)
        if not hits:
            issues.append({'field': field, 'rule': 'quote_not_found',
                           'detail': f'quote not found verbatim on page {page}: {str(quote)[:60]!r}'})
    if issues:
        return issues

    # numbers in the answer must be supported by the quoted evidence
    ev_text = ' '.join(str(ev.get('quote') or '') + ' ' + str(ev.get('matched_text') or '')
                       for ev in evidence)
    ev_numbers = numbers_in(ev_text)
    answer_numbers = numbers_in(value if not isinstance(value, (int, float)) else str(value))
    unsupported = sorted(n for n in answer_numbers if n not in ev_numbers)
    if unsupported:
        issues.append({'field': field, 'rule': 'number_not_in_evidence',
                       'detail': f'numbers {unsupported} do not appear in any evidence quote'})

    if (ans.get('type') == 'table_cell' or contract_type == 'table_cell'):
        issues.extend(_validate_table_cell(field, ans, blocks_doc, evidence))
    return issues


def _validate_table_cell(field, ans, blocks_doc, evidence):
    issues = []
    loc = ans.get('locator') or {}
    row, col = loc.get('row'), loc.get('column')
    if not row or not col:
        issues.append({'field': field, 'rule': 'table_cell_row_column',
                       'detail': 'table-cell answer must name the row and the column'})
        return issues
    tables = blocks_doc.get('tables') or []
    if not tables:
        issues.append({'field': field, 'rule': 'table_cell_no_table',
                       'detail': 'the document contains no table, so a cell value cannot be cited'})
        return issues
    want = loc.get('table')
    numbered = [t for t in tables if want and str(t.get('number')) == str(want)]
    pool = numbered or tables
    hit = None
    for t in pool:
        header = [str(c) for c in (t.get('header') or [])]
        try:
            ci = header.index(col)
        except ValueError:
            ci = None
        for ri, r in enumerate(t.get('rows') or []):
            cells = [str(c) for c in r]
            if norm(cells[0] if cells else '') != norm(row):
                continue
            if ci is not None and ci < len(cells) and norm(cells[ci]) == norm(ans.get('value')):
                hit = {'table': t.get('number'), 'row': row, 'column': col,
                       'value': cells[ci], 'page': (t.get('row_pages') or [t.get('page')])[ri]
                       if ri < len(t.get('row_pages') or []) else t.get('page')}
                break
        if hit:
            break
    if hit is None:
        issues.append({'field': field, 'rule': 'table_cell_mismatch',
                       'detail': f'table {want or "(first)"} has no row {row!r} with column '
                                 f'{col!r} equal to {ans.get("value")!r}'})
    else:
        pages = {ev.get('page') for ev in evidence}
        if hit['page'] not in pages:
            issues.append({'field': field, 'rule': 'table_cell_page_mismatch',
                           'detail': f'row {row!r} is on page {hit["page"]}, evidence cites {sorted(pages)}'})
        ans['locator_resolved'] = hit
    return issues


def validate_missing(field, entries, blocks_doc, facts):
    """Validate claims about information the document does not contain."""
    issues = []
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list) or not entries:
        return [{'field': field, 'rule': 'missing_shape',
                 'detail': 'missing-information must be a non-empty list'}]
    for e in entries:
        if not isinstance(e, dict):
            issues.append({'field': field, 'rule': 'missing_shape', 'detail': 'entry must be an object'})
            continue
        kind = e.get('kind')
        if kind not in KINDS:
            issues.append({'field': field, 'rule': 'missing_kind',
                           'detail': f'kind {kind!r} is not one of {KINDS}'})
            continue
        checked = e.get('checked') or {}
        if kind == 'no_such_object':
            if checked.get('tables') is None:
                issues.append({'field': field, 'rule': 'missing_check',
                               'detail': 'no_such_object must record checked.tables'})
            elif int(checked['tables']) != facts.get('tables'):
                issues.append({'field': field, 'rule': 'missing_check_mismatch',
                               'detail': f'claimed {checked["tables"]} tables, document has {facts.get("tables")}'})
        elif kind == 'not_reported':
            queries = checked.get('queries') or []
            if not queries:
                issues.append({'field': field, 'rule': 'missing_check',
                               'detail': 'not_reported must record the queries that were searched'})
            for q in queries:
                if isinstance(q, dict):
                    hits = q.get('hits')
                    if hits is None:
                        issues.append({'field': field, 'rule': 'missing_check',
                                       'detail': 'each query must record hits'})
                    elif int(hits) != 0:
                        issues.append({'field': field, 'rule': 'missing_claim_false',
                                       'detail': f'query {q.get("query")!r} has {hits} hits, so it is reported'})
        elif kind == 'parse_failure':
            if facts.get('parse_status') == 'ok' and not facts.get('uncertainties'):
                issues.append({'field': field, 'rule': 'missing_claim_false',
                               'detail': 'parse_failure claimed but the document parsed cleanly'})
    return issues
