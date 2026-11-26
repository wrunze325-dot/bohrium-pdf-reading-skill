"""Table structure recovery for the PDF reading skill.

Primary backend : pdfplumber (when installed)
Fallback        : geometry-only row/cell split from MuPDF words, confidence 0.5

Every table keeps, per cell, the page and the bbox it came from, so a reviewer can
re-open the page. Tables that continue on a later page are merged back onto the
header row of the page where the table started.
"""
from __future__ import annotations

import re

CAPTION_RE = re.compile(
    r'^\s*(?P<label>Table|TABLE|Tab\.)\s*(?P<num>[0-9]+[A-Za-z]?|[IVX]+)\b'
)
CONT_RE = re.compile(r'\(\s*(?:cont|continued|cont\'d|续)\b|^\s*(?:continued|cont\.)', re.I)


def find_tables(path, pages, page_text_ok):
    """Return {pageno0: [table, ...]} for pages that carry tables."""
    out = {}
    try:
        import pdfplumber
    except Exception:
        return _fallback_tables(path, pages, page_text_ok)
    try:
        pdf = pdfplumber.open(path)
    except Exception:
        return _fallback_tables(path, pages, page_text_ok)
    try:
        for pno, page in enumerate(pdf.pages):
            if not page_text_ok(pno):
                continue
            try:
                found = page.find_tables()
            except Exception:
                found = []
            tables = []
            for tb in found:
                rows = []
                for r in tb.rows:
                    cells = []
                    for c in r.cells:
                        if c is None:
                            cells.append(None)
                            continue
                        x0, top, x1, bottom = c
                        try:
                            txt = (page.crop((x0, top, x1, bottom)).extract_text() or '').strip()
                        except Exception:
                            txt = ''
                        cells.append({'text': txt, 'bbox_page': [round(x0, 1), round(top, 1),
                                                                 round(x1, 1), round(bottom, 1)]})
                    rows.append(cells)
                tables.append({
                    'bbox': [round(v, 1) for v in tb.bbox],
                    'n_rows': len(rows),
                    'n_cols': max((len(r) for r in rows), default=0),
                    'rows': rows,
                    'backend': 'pdfplumber',
                    'confidence': 1.0,
                })
            if tables:
                out[pno] = tables
    finally:
        try:
            pdf.close()
        except Exception:
            pass
    return out


def _fallback_tables(path, pages, page_text_ok):
    """Split paragraphs of the page into pseudo tables using x-gaps (low confidence)."""
    out = {}
    for pno, page in enumerate(pages):
        if not page_text_ok(pno):
            continue
        words = page.get('words') or []
        if not words:
            continue
        rows = {}
        for w in words:
            x0, y0, x1, y1, txt = w[:5]
            key = round(y0 / 3.0)
            rows.setdefault(key, []).append((x0, y0, x1, y1, txt))
        candidate = []
        for key in sorted(rows):
            cells = sorted(rows[key], key=lambda t: t[0])
            if len(cells) < 3:
                continue
            groups = [[cells[0]]]
            for c in cells[1:]:
                if c[0] - groups[-1][-1][2] > 12:
                    groups.append([c])
                else:
                    groups[-1].append(c)
            if len(groups) >= 2:
                candidate.append(groups)
        if len(candidate) >= 2:
            text_rows = [[{'text': ' '.join(g[0][4] for g in grp) if False else
                           ' '.join(w[4] for w in grp),
                           'bbox_page': None} for grp in row] for row in candidate]
            xs0 = min(r[0][0][0] for r in candidate)
            xs1 = max(r[-1][-1][2] for r in candidate)
            ys0 = min(r[0][0][1] for r in candidate)
            ys1 = max(r[0][-1][3] for r in candidate)
            out[pno] = [{'bbox': [xs0, ys0, xs1, ys1],
                         'n_rows': len(text_rows),
                         'n_cols': max(len(r) for r in text_rows),
                         'rows': text_rows,
                         'backend': 'geometry-fallback',
                         'confidence': 0.5}]
    return out


def caption_index(blocks):
    """Map (label, number) -> list of caption block ids, for table association."""
    idx = {}
    for b in blocks:
        if b.get('type') != 'caption':
            continue
        m = CAPTION_RE.match(b.get('text', ''))
        if not m:
            continue
        key = (m.group('label').lower(), m.group('num').lower())
        idx.setdefault(key, []).append(b)
    return idx


def classify_table_number(caption_text):
    m = CAPTION_RE.match(caption_text or '')
    if not m:
        return None
    return f"{'table'}-{m.group('num').lower()}"


def merge_continuations(tables):
    """Merge tables that continue across pages.

    A table is a continuation when it starts near the top of a page, has no
    caption of its own, and the previous table on the previous page has the same
    column count. The header row of the first page is carried over.
    """
    ordered = sorted(tables, key=lambda t: (t['page'], t['bbox'][1]))
    merged = []
    for t in ordered:
        prev = merged[-1] if merged else None
        same_shape = prev is not None and prev['n_cols'] == t['n_cols']
        near_top = t['bbox'][1] < 0.28 * t.get('page_height', 842)
        adjacent = prev is not None and t['page'] in (prev['page'], prev['page'] + 1)
        if (prev is not None and same_shape and near_top and adjacent and
                not t.get('caption') and prev.get('open', True)):
            for row in t['rows']:
                prev['rows'].append(row)
                prev['row_pages'].append(t['page'])
            prev['continuation_pages'].append(t['page'])
            prev['n_rows'] = len(prev['rows'])
            prev['confidence'] = min(prev['confidence'], t['confidence'])
            continue
        t = dict(t)
        t['row_pages'] = [t['page']] * len(t['rows'])
        t['continuation_pages'] = []
        t['open'] = True
        merged.append(t)
    for t in merged:
        t.pop('open', None)
    return merged


def structure(table):
    """Return header, body rows and per-cell provenance in a stable shape."""
    rows = table['rows']
    if not rows:
        return {'header': [], 'rows': [], 'row_pages': []}
    header_idx = 0
    best = -1
    for i, row in enumerate(rows[:3]):
        filled = sum(1 for c in row if c and c['text'])
        alpha = sum(1 for c in row if c and re.search(r'[^\W\d_]', c['text']))
        score = filled + alpha
        if score > best:
            best, header_idx = score, i
    header = [c['text'] if c else '' for c in rows[header_idx]]
    body = []
    for row in rows[header_idx + 1:]:
        body.append([c['text'] if c else '' for c in row])
    row_pages = table.get('row_pages') or [table['page']] * len(rows)
    return {
        'header': header,
        'header_bbox': [c['bbox_page'] if c else None for c in rows[header_idx]],
        'rows': body,
        'row_pages': row_pages[header_idx + 1:],
        'header_page': row_pages[header_idx],
    }
