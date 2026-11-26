"""Layout-aware PDF reader: reading order, structure, tables, formulas, provenance.

Entry point
    python pdf_outline.py --pdf INPUT.pdf --out OUTDIR [--ocr auto|on|off] [--max-pages N]

Outputs in OUTDIR
    blocks.json     structured blocks in reading order, each with page + bbox + evidence
    document.md     the same content rendered as Markdown, with page/block markers
    summary.json    one-screen status, layout guess, counts, uncertainties
    run.log         everything that happened, including degradations

Design notes
  * Reading order = detect vertical gutters, treat gutter-crossing items as band
    separators, then read each band column by column (left to right, top to bottom).
    This is what stops a two-column page from being flattened into spliced lines.
  * Coordinates are PDF points with the origin at the top-left corner of the page,
    the same space PyMuPDF and pdfplumber report, so bboxes can be re-checked
    against a render without any conversion.
  * Nothing is invented. A page without a text layer that cannot be OCR'd is
    reported as unreadable with its reason and a rendered image, never filled in.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import po_formulas as PF      # noqa: E402
import po_ocr as PO           # noqa: E402
import po_tables as PT        # noqa: E402

SCHEMA = 'pdfoutline/blocks/v1'

CAPTION_RE = re.compile(r'^\s*(?P<label>Figure|Fig\.|Table|Scheme|Algorithm|Listing|Chart)\s*'
                        r'(?P<num>[0-9]+[A-Za-z]?|[IVX]+)\s*[.:]?', re.I)
HEADING_NUM_RE = re.compile(r'^\s*(?P<num>\d{1,2}(?:\.\d{1,2}){0,3})\.?\s{0,4}(?P<title>[A-Z\u4e00-\u9fff].{0,120})$')
HEADING_WORD_RE = re.compile(r'^\s*(Abstract|摘要|Introduction|Related [Ww]ork|Background|Methods?|'
                             r'Results?|Discussion|Conclusions?|References|Bibliography|'
                             r'Acknowledg(?:e)?ments?|Appendix|Supplementary)\b', re.I)
FOOTNOTE_RE = re.compile(r'^\s*(?:\d{1,2}|[*\u2020\u2021\u00a7\u00b6])\s*[A-Z\u4e00-\u9fff][^.]{0,200}$')
PAGENO_RE = re.compile(r'^\s*(?:[-–—]\s*)?(?:\d{1,4}|[ivxlcdm]{1,6}|Page \d+ of \d+)(?:\s*[-–—])?\s*$', re.I)


# --------------------------------------------------------------------------- utils
def log(msg, logf=None):
    line = f'[{time.strftime("%H:%M:%S")}] {msg}'
    print(line, file=sys.stderr)
    if logf is not None:
        logf.write(line + '\n')


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def median(vals, default=0.0):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else default


# --------------------------------------------------------------------- extraction
def extract_document(path, logf=None):
    """Return dict with pages: [{width,height,chars:[...],images:[...]}] and metadata."""
    doc = {'backend': None, 'pages': [], 'meta': {}, 'notes': []}
    try:
        import pymupdf as fitz
    except Exception:
        try:
            import fitz
        except Exception:
            fitz = None
    if fitz is not None:
        try:
            pdf = fitz.open(path)
            doc['backend'] = 'pymupdf'
            doc['meta'] = {
                'pages': pdf.page_count,
                'pdf_version': getattr(pdf, 'pdf_version', None),
                'producer': (pdf.metadata or {}).get('producer') or '',
                'creator': (pdf.metadata or {}).get('creator') or '',
                'title': (pdf.metadata or {}).get('title') or '',
                'encrypted': bool(getattr(pdf, 'is_encrypted', False)),
            }
            for pno in range(pdf.page_count):
                page = pdf[pno]
                rect = page.rect
                raw = page.get_text('rawdict')
                chars = []
                for blk in raw.get('blocks', []):
                    if blk.get('type') != 0:
                        continue
                    for ln in blk.get('lines', []):
                        direction = ln.get('dir') or (1, 0)
                        if abs(direction[1]) > 0.1:      # rotated / vertical text
                            continue
                        for sp in ln.get('spans', []):
                            font = sp.get('font') or ''
                            size = float(sp.get('size') or 0)
                            flags = int(sp.get('flags') or 0)
                            for ch in sp.get('chars', []):
                                c = ch.get('c') or ''
                                x0, y0, x1, y1 = ch.get('bbox') or [0, 0, 0, 0]
                                oy = (ch.get('origin') or [0, y1])[1]
                                chars.append({'c': c, 'x0': x0, 'y0': y0, 'x1': x1, 'y1': y1,
                                              'oy': oy, 'size': size, 'font': font, 'flags': flags})
                images = []
                try:
                    for info in page.get_image_info():
                        bb = info.get('bbox')
                        if bb:
                            images.append({'bbox': [round(v, 1) for v in bb],
                                           'width': info.get('width'), 'height': info.get('height')})
                except Exception:
                    pass
                rects = []
                try:
                    for d in page.get_drawings():
                        r = d.get('rect')
                        if r is None:
                            continue
                        if r.width > 40 and r.height > 20 and (r.width * r.height) > 2500:
                            rects.append([r.x0, r.y0, r.x1, r.y1])
                except Exception:
                    rects = []
                doc['pages'].append({'width': rect.width, 'height': rect.height,
                                     'chars': chars, 'images': images, 'rects': rects,
                                     'rotation': page.rotation})
            _ = fitz  # keep reference
            doc['_doc'] = pdf
            return doc
        except Exception as exc:
            doc['notes'].append(f'pymupdf failed: {exc!r}')
            log(f'pymupdf failed: {exc!r}', logf)
    # fallback: pdfplumber words (no exact font flags, coarser scripts)
    try:
        import pdfplumber
        pdf = pdfplumber.open(path)
        doc['backend'] = 'pdfplumber'
        for pno, page in enumerate(pdf.pages):
            chars = []
            for w in page.extract_words(extra_attrs=['size', 'fontname']) or []:
                txt = w['text']
                size = float(w.get('size') or 9.0)
                for i, ch in enumerate(txt):
                    chars.append({'c': ch, 'x0': w['x0'] + i * 0.1, 'y0': w['top'],
                                  'x1': w['x0'] + (i + 1) * 0.1, 'y1': w['bottom'],
                                  'oy': w['bottom'], 'size': size,
                                  'font': w.get('fontname') or '', 'flags': 0})
                chars.append({'c': ' ', 'x0': w['x1'], 'y0': w['top'], 'x1': w['x1'] + 1,
                              'y1': w['bottom'], 'oy': w['bottom'], 'size': size,
                              'font': w.get('fontname') or '', 'flags': 0})
            doc['pages'].append({'width': float(page.width), 'height': float(page.height),
                                 'chars': chars, 'images': [], 'rotation': 0})
        doc['meta'] = {'pages': len(doc['pages'])}
        doc['_doc'] = pdf
        doc['notes'].append('degraded backend pdfplumber: font sizes coarse, scripts may be missed')
        return doc
    except Exception as exc:
        doc['notes'].append(f'pdfplumber failed: {exc!r}')
        log(f'pdfplumber failed: {exc!r}', logf)
    # last resort: pypdf text only
    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        doc['backend'] = 'pypdf'
        for page in reader.pages:
            txt = page.extract_text() or ''
            chars = []
            y = 0
            for line in txt.splitlines():
                y += 12
                for i, ch in enumerate(line):
                    chars.append({'c': ch, 'x0': i * 5.0, 'y0': y, 'x1': i * 5.0 + 4,
                                  'y1': y + 9, 'oy': y + 9, 'size': 9, 'font': '', 'flags': 0})
            doc['pages'].append({'width': 595, 'height': 842, 'chars': chars,
                                 'images': [], 'rotation': 0, 'text_only': txt})
        doc['notes'].append('degraded backend pypdf: geometry is synthetic, bbox not reliable')
        return doc
    except Exception as exc:
        raise RuntimeError(f'no PDF backend available (pymupdf/pdfplumber/pypdf): {exc}')
    return doc


# ------------------------------------------------------------------ line building
def _cluster_by_baseline(chars, tol=1.6):
    """Group chars that sit on the same baseline."""
    if not chars:
        return []
    chars = sorted(chars, key=lambda c: (round(c['oy'], 1), c['x0']))
    clusters = []
    cur = [chars[0]]
    for c in chars[1:]:
        if abs(c['oy'] - cur[-1]['oy']) <= tol:
            cur.append(c)
        else:
            clusters.append(cur)
            cur = [c]
    clusters.append(cur)
    return clusters


def build_lines(chars, gutters, page_w):
    """Cluster chars into lines, splitting at gutters and at wide word gaps.

    Script glyphs (x^2, H_2O) sit on their own, shifted baseline, so a plain
    baseline cluster would tear them into separate "lines". They are attached to
    the nearest full-size line that spans them horizontally, which keeps the
    formula in one piece; only then is the sup/sub structure recovered.
    """
    if not chars:
        return []
    sizes = [c['size'] for c in chars if c['c'].strip()]
    body_est = median(sizes) or 9.0
    primary = [c for c in chars if c['size'] >= 0.72 * body_est]
    secondary = [c for c in chars if c['size'] < 0.72 * body_est]
    clusters = _cluster_by_baseline(primary)
    orphans = []
    for c in secondary:
        best = None
        best_d = 1e9
        for cl in clusters:
            cl_size = median([x['size'] for x in cl]) or body_est
            bl = median([x['oy'] for x in cl])
            d = abs(c['oy'] - bl)
            if d > 0.85 * cl_size:
                continue
            cx0 = min(x['x0'] for x in cl)
            cx1 = max(x['x1'] for x in cl)
            if c['x0'] < cx0 - 2.0 * cl_size or c['x1'] > cx1 + 5.0 * cl_size:
                continue
            if d < best_d:
                best, best_d = cl, d
        if best is not None:
            best.append(c)
        else:
            orphans.append(c)
    clusters.extend(_cluster_by_baseline(orphans))

    gutter_xs = [(g[0] + g[1]) / 2.0 for g in gutters]
    lines = []
    for cluster in clusters:
        cluster.sort(key=lambda c: c['x0'])
        runs = []
        run = [cluster[0]]
        for c in cluster[1:]:
            prev = run[-1]
            size = max(prev['size'], c['size'], 1.0)
            gap = c['x0'] - prev['x1']
            split = gap > 1.35 * size
            if not split:
                for gx in gutter_xs:
                    if prev['x1'] <= gx <= c['x0']:
                        split = True
                        break
            if split:
                runs.append(run)
                run = [c]
            else:
                run.append(c)
        runs.append(run)
        for run in runs:
            if not run:
                continue
            real = [c for c in run if c['c'].strip()] or run
            max_size = max(c['size'] for c in real) or 9.0
            main = [c for c in real if c['size'] >= 0.85 * max_size] or real
            size = median([c['size'] for c in main]) or 9.0
            fonts = [c['font'] for c in main if c['c'].strip()]
            font = statistics.mode(fonts) if fonts else ''
            bold = ('bold' in font.lower() or 'black' in font.lower() or
                    any((c['flags'] & 16) for c in main))
            lines.append({
                'chars': run,
                'x0': min(c['x0'] for c in run),
                'x1': max(c['x1'] for c in run),
                'y0': min(c['y0'] for c in run),
                'y1': max(c['y1'] for c in run),
                'baseline': median([c['oy'] for c in main]),
                'size': size, 'font': font, 'bold': bold,
            })
    lines.sort(key=lambda l: (l['y0'], l['x0']))
    return lines


def line_text(line):
    """Assemble text with spaces where the PDF used positional gaps."""
    out = []
    prev = None
    base = line['size'] or 9.0
    for c in line['chars']:
        if prev is not None:
            gap = c['x0'] - prev['x1']
            if gap > 0.22 * base and out and out[-1] not in (' ',) and c['c'] not in (' ',):
                out.append(' ')
        out.append(c['c'])
        prev = c
    return ''.join(out)


def compute_scripts(line):
    base = line['size'] or 9.0
    base_y = line['baseline']
    enriched = []
    for c in line['chars']:
        dy = base_y - c['oy']          # positive means raised on the page
        enriched.append({'c': c['c'], 'size': c['size'], 'dy': dy})
    text, rich, spans = PF.annotate_scripts(enriched, base)
    return text, rich, spans


# ---------------------------------------------------------------- layout analysis
def _column_signature(lines, x0, x1, tol=10.0):
    """A real column has a consistent left edge; ragged prose does not."""
    if len(lines) < 2:
        return False
    anchor = min(l['x0'] for l in lines)
    good = sum(1 for l in lines if abs(l['x0'] - anchor) <= tol)
    return good >= 0.5 * len(lines)


def detect_gutters(lines, page_w, depth=0):
    """Find vertical corridors that split the text into columns.

    Instead of thresholding a coverage histogram (which fails whenever a title or
    an abstract runs across the page), scan candidate cut positions and pick the
    one that the fewest lines cross, requiring both sides to look like columns
    (consistent left edge) and to carry real mass.
    """
    spans = [(l['x0'], l['x1']) for l in lines if l['x1'] > l['x0']]
    if len(spans) < 6 or depth > 2:
        return [], (min((s[0] for s in spans), default=0.0),
                    max((s[1] for s in spans), default=page_w))
    x0 = min(s[0] for s in spans)
    x1 = max(s[1] for s in spans)
    width = x1 - x0
    if width < 90:
        return [], (x0, x1)
    n = len(lines)
    best = None
    lo = int(x0 + 0.26 * width)
    hi = int(x0 + 0.74 * width)
    for g in range(lo, hi):
        cross = [l for l in lines if l['x0'] < g < l['x1']]
        left = [l for l in lines if l['x1'] <= g]
        right = [l for l in lines if l['x0'] >= g]
        if len(left) < max(2, 0.18 * n) or len(right) < max(2, 0.18 * n):
            continue
        if len(cross) > 0.22 * n:
            continue
        if not _column_signature(left, x0, x1) or not _column_signature(right, x0, x1):
            continue
        key = (len(cross), -min(len(left), len(right)))
        if best is None or key < best[0]:
            best = (key, g, left, right)
    if best is None:
        # fallback: two clusters of line starts (a column has a consistent left edge,
        # so its lines start at nearly the same x). Used on pages where figures or
        # full-width floats break the strict scan.
        starts = sorted(l['x0'] for l in lines)
        groups = []
        for x in starts:
            if groups and x - groups[-1][-1] <= 14:
                groups[-1].append(x)
            else:
                groups.append([x])
        groups = [g for g in groups if len(g) >= max(3, 0.12 * n)]
        if len(groups) >= 2:
            left_g = max(groups, key=len) if False else groups[-2]
            right_g = groups[-1]
            gx0 = max((l['x1'] for l in lines if l['x0'] <= left_g[-1]), default=None)
            gx1 = min((l['x0'] for l in lines if l['x0'] >= right_g[0]), default=None)
            if gx0 is not None and gx1 is not None and 2 <= gx1 - gx0 <= 0.25 * width:
                cross = sum(1 for l in lines if l['x0'] < (gx0 + gx1) / 2 < l['x1'])
                if cross <= 0.45 * n:
                    return [(gx0, gx1)], (x0, x1)
        return [], (x0, x1)
    _, g, left, right = best
    gx0 = max((l['x1'] for l in left), default=g - 3)
    gx1 = min((l['x0'] for l in right), default=g + 3)
    if gx1 - gx0 < 3:
        gx0, gx1 = g - 3, g + 3
    # one gutter per page: the target documents are one- or two-column papers, and
    # recursing produced spurious sub-columns inside the right column of a title page
    return [(gx0, gx1)], (x0, x1)


def columns_from_gutters(gutters, content):
    bounds = [content[0]]
    for gx0, gx1 in gutters:
        bounds.append((gx0 + gx1) / 2.0)
    bounds.append(content[1])
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


def col_label(idx, total):
    if total <= 1:
        return 'full'
    if total == 2:
        return 'L' if idx == 0 else 'R'
    return f'C{idx + 1}'


def item_regions(item, columns):
    overlaps = []
    for i, (a, b) in enumerate(columns):
        ov = min(item['x1'], b) - max(item['x0'], a)
        if ov > 0:
            overlaps.append((i, ov))
    if not overlaps:
        return [], 0.0
    width = max(item['x1'] - item['x0'], 1e-6)
    overlaps.sort(key=lambda t: -t[1])
    best = overlaps[0][0]
    multi = [i for i, ov in overlaps if ov / width > 0.15]
    return multi, best


def reading_order(items, columns):
    """Band-and-column order: gutter-crossing items split the page into bands."""
    spanning = []
    local = []
    for it in items:
        multi, best = item_regions(it, columns)
        it['regions'] = multi
        it['region'] = best
        if len(multi) >= 2:
            it['spanning'] = True
            spanning.append(it)
        else:
            it['spanning'] = False
            local.append(it)
    spanning.sort(key=lambda t: t['y0'])
    ordered = []
    prev_y = -1e9
    band = []
    for sp in spanning:
        band = [it for it in local if prev_y < it['y0'] < sp['y0']]
        ordered.extend(_order_band(band, columns))
        ordered.append(sp)
        prev_y = sp['y0']
        local = [it for it in local if not (it['y0'] < sp['y0'])]
    ordered.extend(_order_band(local, columns))
    return ordered


def _order_band(items, columns):
    by_col = {}
    for it in items:
        by_col.setdefault(it.get('region', 0), []).append(it)
    out = []
    for idx in sorted(by_col):
        out.extend(sorted(by_col[idx], key=lambda t: (round(t['y0'], 1), t['x0'])))
    return out


# --------------------------------------------------------------- classification
def classify(lines, page_h, body_size, strong_repeat, rects=None, pageno=1):
    last_title = None
    for l in lines:
        text = l['text'].strip()
        size = l['size']
        l['type'] = 'body'
        if not text:
            l['type'] = 'empty'
            continue
        if PAGENO_RE.match(text) and l['y0'] > 0.85 * page_h:
            l['type'] = 'footer'
            continue
        in_top = l['y0'] < 0.075 * page_h
        in_bottom = l['y0'] > 0.925 * page_h
        if (in_top or in_bottom) and size <= body_size * 1.06:
            if in_top:
                l['type'] = 'header'
                continue
            l['type'] = 'footer'
            continue
        if in_top and text in strong_repeat:
            l['type'] = 'header'
            continue
        if in_bottom and text in strong_repeat:
            l['type'] = 'footer'
            continue
        if CAPTION_RE.match(text):
            l['type'] = 'caption'
            m = CAPTION_RE.match(text)
            l['caption_label'] = m.group('label').lower().replace('.', '')
            l['caption_number'] = m.group('num')
            l['continuation'] = bool(PT.CONT_RE.search(text))
            continue
        if re.match(r'^\s*(Abstract|摘要)[.:：]?\s', text, re.I):
            l['type'] = 'abstract'
            continue
        if rects:
            cx = (l['x0'] + l['x1']) / 2.0
            cy = (l['y0'] + l['y1']) / 2.0
            hit = None
            for r in rects:
                if r[0] <= cx <= r[2] and r[1] <= cy <= r[3]:
                    hit = r
                    break
            if hit is not None:
                l['type'] = 'figure_label'
                l['figure_rect'] = [round(v, 1) for v in hit]
                continue
        if l['y0'] > 0.78 * page_h and size < body_size * 0.96 and FOOTNOTE_RE.match(text):
            l['type'] = 'footnote'
            continue
        if HEADING_WORD_RE.match(text) and len(text) < 60 and (size >= body_size * 0.98 or l['bold']):
            l['type'] = 'heading'
            l['heading_level'] = 1
            continue
        m = HEADING_NUM_RE.match(text)
        if m and len(text) < 140 and (l['bold'] or size >= body_size * 1.02):
            l['type'] = 'heading'
            l['heading_level'] = m.group('num').count('.') + 1
            l['heading_number'] = m.group('num')
            continue
        if size >= body_size * 1.35 and l['y0'] < 0.35 * page_h and 8 <= len(text) < 250:
            if pageno == 1:
                l['type'] = 'title'
                last_title = l
            else:
                l['type'] = 'heading'
                l['heading_level'] = 1
            continue
        if (last_title is not None and -6 <= l['y0'] - last_title['y1'] <= 45 and
                0.85 * body_size <= size <= 1.15 * body_size and len(text) < 220 and
                not text.endswith('.') and '@' not in text and
                re.search(r'[A-Z][a-z]+\s+[A-Z][a-z]+', text) and
                (',' in text or ' and ' in text or '(' in text or '\u2020' in text)):
            l['type'] = 'authors'
            continue
        if size < body_size * 0.9 and l['y0'] > 0.72 * page_h:
            l['type'] = 'footnote'
            continue
    return lines


def paragraphize(items):
    """Merge consecutive body lines of the same column into paragraph blocks."""
    blocks = []
    for it in items:
        if it.get('kind') == 'table':
            blocks.append(it)
            continue
        if it['type'] in ('table_member', 'empty'):
            continue
        if it['type'] in ('header', 'footer', 'footnote'):
            blk = _line_to_block(it)
            blk['role'] = 'periphery'
            blocks.append(blk)
            continue
        if it['type'] in ('title', 'heading', 'caption', 'authors', 'figure_label'):
            blocks.append(_line_to_block(it))
            continue
        if it.get('formula'):
            blocks.append(_formula_to_block(it))
            continue
        prev = blocks[-1] if blocks else None
        mergeable = (
            prev is not None and prev.get('type') in ('body', 'abstract') and
            it['type'] in ('body', 'abstract') and
            prev.get('region') == it.get('region') and
            abs(prev.get('size', 0) - it['size']) < 0.6 and
            (it['y0'] - prev['bbox'][3]) < 0.75 * it['size'] and
            it['y0'] >= prev['bbox'][3] - 1.0 and
            (prev.get('_last_full') or prev.get('_hyphen') or
             (it['text'][:1].islower() and (it['y0'] - prev['bbox'][3]) < 0.6 * it['size']))
        )
        if mergeable:
            text = prev['text']
            cur_rich = it.get('rich') or it['text']
            hyphen = text.endswith('-') and it['text'][:1].islower()
            if hyphen:
                prev['text'] = text[:-1] + it['text']
                prev['rich_text'] = prev['rich_text'][:-1] + cur_rich
            else:
                prev['text'] = text + ' ' + it['text']
                prev['rich_text'] = prev['rich_text'] + ' ' + cur_rich
            prev['bbox'][2] = max(prev['bbox'][2], it['x1'])
            prev['bbox'][3] = max(prev['bbox'][3], it['y1'])
            prev['lines'].append({'y0': round(it['y0'], 1), 'y1': round(it['y1'], 1)})
            prev['_last_full'] = it.get('full')
            prev['_hyphen'] = hyphen
            continue
        blk = _line_to_block(it)
        blk['_last_full'] = it.get('full')
        blk['_hyphen'] = it['text'].endswith('-')
        blocks.append(blk)
    for b in blocks:
        b.pop('_last_full', None)
        b.pop('_hyphen', None)
    return blocks


def _locator(page, col, bbox):
    return (f'p.{page} col={col} bbox=[{bbox[0]:.1f},{bbox[1]:.1f},{bbox[2]:.1f},{bbox[3]:.1f}]')


def _line_to_block(it):
    bbox = [round(it['x0'], 1), round(it['y0'], 1), round(it['x1'], 1), round(it['y1'], 1)]
    blk = {
        'type': it['type'],
        'page': it['page'],
        'bbox': bbox,
        'column': it.get('label', 'full'),
        'text': it['text'],
        'rich_text': it['rich'],
        'scripts': it.get('scripts') or [],
        'lines': [{'y0': round(it['y0'], 1), 'y1': round(it['y1'], 1)}],
        'confidence': 1.0,
        'source': 'text-layer',
        'region': it.get('region'),
        'size': round(it['size'], 1),
    }
    if it['type'] == 'heading':
        blk['heading_level'] = it.get('heading_level', 1)
        if it.get('heading_number'):
            blk['number'] = it['heading_number']
    if it['type'] == 'caption':
        blk['caption_label'] = it.get('caption_label')
        blk['caption_number'] = it.get('caption_number')
        blk['continuation'] = it.get('continuation', False)
    blk['locator'] = _locator(it['page'], blk['column'], bbox)
    return blk


def _formula_to_block(it):
    f = it['formula']
    bbox = [round(it['x0'], 1), round(it['y0'], 1), round(it['x1'], 1), round(it['y1'], 1)]
    return {
        'type': 'formula', 'page': it['page'], 'bbox': bbox,
        'column': it.get('label', 'full'),
        'text': f['text'], 'rich_text': f['rich_text'], 'scripts': f['spans'],
        'formula_number': f['number'], 'greek': f['greek'], 'symbols': f['symbols'],
        'lines': [{'y0': round(it['y0'], 1), 'y1': round(it['y1'], 1)}],
        'confidence': f['confidence'], 'source': 'text-layer',
        'region': it.get('region'), 'size': round(it['size'], 1),
        'locator': _locator(it['page'], it.get('label', 'full'), bbox),
    }


# --------------------------------------------------------------------- per page
def page_model(page, pno, path, logf, ocr_mode, rendered_dir, doc):
    w, h = page['width'], page['height']
    chars = page['chars']
    text_ok = len([c for c in chars if c['c'].strip()]) >= 8
    notes = []
    ocr_used = None
    if not text_ok:
        notes.append({'page': pno + 1, 'kind': 'no_text_layer',
                      'reason': '页面没有可提取的文字层（扫描页或纯图片页）',
                      'action': 'OCR fallback attempted' if ocr_mode != 'off' else 'OCR disabled'})
        if ocr_mode != 'off':
            backend = PO.available_backend()
            img = None
            try:
                img = PO.render_page(doc['_doc'][pno], rendered_dir, pno + 1)
            except Exception:
                img = None
            if backend and img:
                words, used = PO.run_ocr(img, backend)
                if words:
                    words = PO.scale_words(words, w, h, 200)
                    chars = []
                    for wd in words:
                        x0, y0, x1, y1 = wd['bbox']
                        txt = wd['text']
                        step = (x1 - x0) / max(len(txt), 1)
                        for i, ch in enumerate(txt):
                            chars.append({'c': ch, 'x0': x0 + i * step, 'y0': y0,
                                          'x1': x0 + (i + 1) * step, 'y1': y1,
                                          'oy': y1 - 0.2 * (y1 - y0), 'size': (y1 - y0) * 0.85,
                                          'font': 'ocr', 'flags': 0})
                        chars.append({'c': ' ', 'x0': x1, 'y0': y0, 'x1': x1 + 1, 'y1': y1,
                                      'oy': y1 - 0.2 * (y1 - y0), 'size': (y1 - y0) * 0.85,
                                      'font': 'ocr', 'flags': 0})
                    ocr_used = used
                    notes.append({'page': pno + 1, 'kind': 'ocr_used', 'backend': used,
                                  'reason': '文字层缺失，改用 OCR；结果置信度低于文字层',
                                  'confidence': 0.7})
                else:
                    notes.append({'page': pno + 1, 'kind': 'ocr_failed', 'backend': used,
                                  'reason': 'OCR 已尝试但未返回文字'})
            else:
                notes.append({'page': pno + 1, 'kind': 'ocr_unavailable',
                              'reason': '本机没有可用的 OCR 后端，页面保持未识别状态',
                              'image': img})

    lines = build_lines(chars, [], w)
    for l in lines:
        l['text'], l['rich'], l['scripts'] = compute_scripts(l)
        l['page'] = pno + 1
    lines = [l for l in lines if l['text'].strip()]
    gutters, content = detect_gutters(lines, w)
    # rebuild with gutter-aware splitting
    lines = build_lines(chars, gutters, w)
    for i, l in enumerate(lines):
        l['text'], l['rich'], l['scripts'] = compute_scripts(l)
        l['page'] = pno + 1
        l['y'] = l['baseline']
        l['id'] = f'p{pno + 1}-l{i}'
    lines = [l for l in lines if l['text'].strip()]
    columns = columns_from_gutters(gutters, content)

    return {
        'pageno': pno + 1, 'width': w, 'height': h, 'lines': lines,
        'gutters': gutters, 'columns': columns, 'content': content,
        'notes': notes, 'ocr_used': ocr_used, 'text_ok': text_ok,
        'images': page.get('images') or [], 'rects': page.get('rects') or [],
    }


# ------------------------------------------------------------------------ main
def run(args):
    started = time.time()
    os.makedirs(args.out, exist_ok=True)
    logf = open(os.path.join(args.out, 'run.log'), 'w', encoding='utf-8')
    uncertainties = []
    log(f'open {args.pdf}', logf)
    doc = extract_document(args.pdf, logf)
    log(f'backend={doc["backend"]} pages={len(doc["pages"])}', logf)
    for n in doc['notes']:
        log('note: ' + n, logf)
        uncertainties.append({'kind': 'backend', 'reason': n})

    max_pages = args.max_pages or len(doc['pages'])
    pages = doc['pages'][:max_pages]

    # pass 1: raw models so repeated headers/footers can be recognised
    rendered_dir = os.path.join(args.out, 'rendered')
    models = []
    for pno, page in enumerate(pages):
        m = page_model(page, pno, args.pdf, logf, args.ocr, rendered_dir, doc)
        models.append(m)
        log(f'page {pno+1}: {len(m["lines"])} lines, gutters={[(round(a,1),round(b,1)) for a,b in m["gutters"]]}', logf)

    # repeated header/footer text across pages
    repeat = {}
    for m in models:
        h = m['height']
        for l in m['lines']:
            if l['y0'] < 0.09 * h or l['y0'] > 0.91 * h:
                key = re.sub(r'\d+', '#', l['text'].strip().lower())
                repeat[key] = repeat.get(key, 0) + 1
    strong_repeat = {k for k, v in repeat.items() if v >= 2}

    # body font size = size that carries the most characters
    size_weight = {}
    for m in models:
        for l in m['lines']:
            size_weight[round(l['size'], 1)] = size_weight.get(round(l['size'], 1), 0) + len(l['text'])
    body_size = max(size_weight.items(), key=lambda kv: kv[1])[0] if size_weight else 9.5
    log(f'body font size ~ {body_size}', logf)

    # tables (pdfplumber preferred, geometry fallback otherwise)
    def text_ok(pno):
        return pno < len(models) and models[pno]['text_ok']

    tables_by_page = PT.find_tables(args.pdf, pages, text_ok)
    log(f'tables found on pages: {sorted(k + 1 for k in tables_by_page)}', logf)

    # classification + block assembly, page by page
    all_blocks = []
    order = 0
    table_blocks = []
    for m in models:
        h = m['height']
        classify(m['lines'], h, body_size, strong_repeat, m.get('rects'), m['pageno'])
        colw = [(b - a) for a, b in m['columns']] or [m['content'][1] - m['content'][0]]

        # tables on this page -> items, and mark their member lines
        t_items = []
        for t in tables_by_page.get(m['pageno'] - 1, []):
            tb = t['bbox']
            members = []
            for l in m['lines']:
                cx = (l['x0'] + l['x1']) / 2
                cy = (l['y0'] + l['y1']) / 2
                if tb[0] - 2 <= cx <= tb[2] + 2 and tb[1] - 2 <= cy <= tb[3] + 2:
                    members.append(l)
            if len(members) < 2:
                continue
            for l in members:
                l['type'] = 'table_member'
                l['table_ref'] = len(table_blocks)
            t_items.append({
                'kind': 'table', 'type': 'table', 'page': m['pageno'],
                'x0': tb[0], 'x1': tb[2], 'y0': tb[1], 'y1': tb[3],
                'bbox': [round(v, 1) for v in tb], 'raw': t,
                'n_cols': t['n_cols'], 'n_rows': t['n_rows'],
                'confidence': t['confidence'], 'backend': t['backend'],
            })

        items = []
        for l in m['lines']:
            if l['type'] in ('empty', 'table_member'):
                continue
            l['formula'] = None
            fs = PF.formula_score(l) if l['type'] == 'body' else 0.0
            if fs >= 0.5:
                fl = dict(l)
                fl['type'] = 'formula'
                items.append(fl)
                continue
            items.append(l)
        items.extend(t_items)

        # formula grouping inside each column happens after ordering
        ordered = reading_order(items, m['columns'])

        # annotate column labels
        for it in ordered:
            it['label'] = ('full' if it.get('spanning')
                           else col_label(it.get('region', 0), len(m['columns'])))
            col_width = colw[min(it.get('region', 0), len(colw) - 1)]
            it['full'] = (it['x1'] - it['x0']) >= 0.68 * col_width

        # formulas: group consecutive formula lines within the same column
        merged_items = []
        i = 0
        while i < len(ordered):
            it = ordered[i]
            if it.get('type') == 'formula':
                group = [it]
                j = i + 1
                while (j < len(ordered) and ordered[j].get('type') == 'formula' and
                       ordered[j].get('region') == it.get('region') and
                       0 <= group[-1]['y0'] - ordered[j]['y0'] <= 2.8 * ordered[j]['size']):
                    group.append(ordered[j])
                    j += 1
                glines = [dict(g) for g in group]
                fs = PF.collect_formulas(glines, m['pageno'], it['label'])
                if fs:
                    f = fs[0]
                    # an equation number often sits far right of the formula, outside
                    # the formula's own text run: attach the nearest standalone "(n)"
                    if f.get('number') is None:
                        for k in range(j, min(j + 3, len(ordered))):
                            cand = ordered[k]
                            if cand.get('kind') == 'table':
                                continue
                            mnum = PF.EQNUM_ONLY_RE.match((cand.get('text') or '').strip())
                            if mnum and abs(cand['y0'] - it['y0']) <= 8:
                                f['number'] = mnum.group('num')
                                j = k + 1
                                break
                    it = dict(it)
                    it['formula'] = f
                    it['text'] = f['text']
                    it['rich'] = f['rich_text']
                    it['scripts'] = f['spans']
                    merged_items.append(it)
                i = j
                continue
            merged_items.append(it)
            i += 1

        blocks = paragraphize(merged_items)
        # page periphery (running head, page number, footnote) is kept, but it is
        # listed after the page body so it cannot contaminate the reading order
        body = [b for b in blocks if b.get('role') != 'periphery']
        peri = [b for b in blocks if b.get('role') == 'periphery']
        blocks = body + peri
        for bi, b in enumerate(blocks):
            b.setdefault('id', f'p{m["pageno"]}-b{bi + 1}')

        # table blocks: structure, caption link, evidence
        for b in blocks:
            if b.get('kind') != 'table':
                continue
            t = b.pop('raw')
            t['page'] = b['page']
            raw_rows = t['rows']
            cap = _nearest_caption(blocks, b, m['pageno'])
            number = None
            if cap:
                number = PT.classify_table_number(cap['text'])
            b.update({
                'type': 'table',
                'raw_rows': raw_rows,
                'row_pages': [b['page']] * len(raw_rows),
                'page_row_counts': [len(raw_rows)],
                'caption_id': cap['id'] if cap else None,
                'number': number,
                'continuation': bool(cap and cap.get('continuation')),
                'backend': t.get('backend'),
                'locator': _locator(m['pageno'], b.get('label', 'full'), b['bbox']),
            })
            table_blocks.append(b)

        all_blocks.extend(blocks)
        for b in blocks:
            order += 1
            b['order'] = order

    # merge cross-page table continuations, then fix header/data split on the
    # merged table so a continuation row is never mistaken for a header row
    all_blocks, merged_log = merge_tables(all_blocks)
    for b in all_blocks:
        if b.get('type') == 'table':
            finalize_table(b)
            if not b.get('header') or not b.get('rows'):
                uncertainties.append({'page': b['page'], 'bbox': b['bbox'],
                                      'kind': 'table_structure_uncertain',
                                      'reason': '表格行列不完整，仅保留可读单元格'})

    # figures: estimate a region from each figure caption and the whitespace above it
    figures = estimate_figures(all_blocks, models)

    # uncertainties for suspicious text and captions
    uncertainties.extend(check_text_quality(all_blocks))
    uncertainties.extend(check_caption_conflicts(all_blocks))
    for m in models:
        uncertainties.extend(m['notes'])
        if not m['text_ok'] and not m['ocr_used']:
            uncertainties.append({'page': m['pageno'], 'kind': 'unreadable_page',
                                  'reason': '该页无文字层且未能 OCR，内容未纳入结果'})

    status = 'ok'
    if any(u.get('kind') in ('unreadable_page', 'ocr_failed') for u in uncertainties):
        status = 'partial'
    if doc['backend'] in ('pypdf',):
        status = 'partial'
    if not all_blocks:
        status = 'failed'

    col_counts = {}
    for m in models:
        if m['text_ok']:
            col_counts[len(m['columns'])] = col_counts.get(len(m['columns']), 0) + 1
    modal_cols = max(col_counts.items(), key=lambda kv: kv[1])[0] if col_counts else 1
    layout = {
        'columns': modal_cols,
        'columns_by_page': [len(m['columns']) for m in models],
        'gutters': [[round(a, 1), round(b, 1)] for a, b in (models[0]['gutters'] if models else [])],
        'body_font_size': body_size,
        'page_size': [round(models[0]['width'], 1), round(models[0]['height'], 1)] if models else None,
    }

    out = {
        'schema': SCHEMA,
        'source': {
            'path': os.path.abspath(args.pdf),
            'sha256': sha256_file(args.pdf),
            'pages': len(doc['pages']),
            'pages_read': len(models),
            'backend': doc['backend'],
            'meta': doc['meta'],
        },
        'layout': layout,
        'blocks': all_blocks,
        'tables': [b for b in all_blocks if b.get('type') == 'table'],
        'formulas': [b for b in all_blocks if b.get('type') == 'formula'],
        'figures': figures,
        'uncertainties': uncertainties,
        'status': status,
        'timing': {'seconds': round(time.time() - started, 2)},
    }
    with open(os.path.join(args.out, 'blocks.json'), 'w', encoding='utf-8') as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, 'document.md'), 'w', encoding='utf-8') as fh:
        fh.write(render_markdown(out))
    summary = {
        'status': status,
        'pages': len(doc['pages']),
        'blocks': len(all_blocks),
        'by_type': _count_by(all_blocks, 'type'),
        'tables': len(out['tables']),
        'formulas': len(out['formulas']),
        'figures': len(figures),
        'uncertainties': len(uncertainties),
        'layout': layout,
        'backend': doc['backend'],
        'seconds': round(time.time() - started, 2),
    }
    with open(os.path.join(args.out, 'summary.json'), 'w', encoding='utf-8') as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    log(f'done status={status} blocks={len(all_blocks)} seconds={summary["seconds"]}', logf)
    logf.close()
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    return 0 if status != 'failed' else 2


def _count_by(items, key):
    out = {}
    for it in items:
        k = it.get(key) or 'unknown'
        out[k] = out.get(k, 0) + 1
    return out


def _nearest_caption(blocks, table_block, pageno):
    best = None
    best_d = 1e9
    for b in blocks:
        if b.get('type') != 'caption' or b.get('page') != pageno:
            continue
        label = (b.get('caption_label') or '')
        if label not in ('table', 'tab'):
            continue
        d = table_block['bbox'][1] - b['bbox'][3]
        if 0 <= d < 60 and d < best_d:
            best, best_d = b, d
    return best


def merge_tables(blocks):
    """Merge a table on page N+1 back onto the table it continues from page N."""
    merged = []
    for b in blocks:
        if b.get('type') != 'table':
            merged.append(b)
            continue
        prev = next((x for x in reversed(merged) if x.get('type') == 'table'), None)
        same_number = bool(prev and b.get('number') and prev.get('number') == b.get('number'))
        looks_cont = b.get('continuation') or not b.get('caption_id')
        near_top = b['bbox'][1] < 0.30 * 842
        adjacent = bool(prev and b['page'] in (prev['page'], prev['page'] + 1))
        same_shape = bool(prev and prev.get('header') and len(b.get('header') or []) == len(prev['header']))
        if prev and same_number and looks_cont and adjacent:
            _absorb(prev, b, 'table number repeats on the next page without a new caption')
            continue
        if prev and not b.get('caption_id') and near_top and adjacent and same_shape:
            _absorb(prev, b, 'unlabelled table at the top of the next page with the same column count')
            continue
        merged.append(b)
    return merged, None


def finalize_table(b):
    rows = b.get('raw_rows') or []
    pages = b.get('row_pages') or []
    counts = b.get('page_row_counts') or [len(rows)]
    if not rows:
        b['header'], b['rows'], b['row_pages'] = [], [], []
        return b
    head_n = max(1, min(3, counts[0] if counts else len(rows)))
    best_i, best = 0, -1
    for i, row in enumerate(rows[:head_n]):
        filled = sum(1 for c in row if c and c.get('text'))
        alpha = sum(1 for c in row if c and re.search(r'[^\W\d_]', c.get('text') or ''))
        if filled + alpha > best:
            best, best_i = filled + alpha, i
    header_row = rows[best_i]
    b['header'] = [c['text'] if c else '' for c in header_row]
    b['header_page'] = pages[best_i] if best_i < len(pages) else b.get('page')
    b['header_bbox'] = [c['bbox_page'] if c else None for c in header_row]
    data = rows[best_i + 1:]
    b['rows'] = [[c['text'] if c else '' for c in r] for r in data]
    b['row_pages'] = pages[best_i + 1:]
    b['row_cells'] = [[({'text': c['text'], 'bbox_page': c['bbox_page']} if c else None)
                       for c in r] for r in data]
    b['spans_pages'] = sorted(set(pages))
    b['n_cols'] = max((len(r) for r in rows), default=0)
    b.pop('raw_rows', None)
    b.pop('page_row_counts', None)
    return b


def _absorb(prev, nxt, reason):
    prev['raw_rows'] = (prev.get('raw_rows') or []) + (nxt.get('raw_rows') or [])
    prev['row_pages'] = (prev.get('row_pages') or []) + (nxt.get('row_pages') or [nxt['page']])
    prev['page_row_counts'] = (prev.get('page_row_counts') or [0]) + (nxt.get('page_row_counts') or [0])
    prev.setdefault('continuation_pages', []).append(nxt['page'])
    prev['merge_records'] = prev.get('merge_records', []) + [{
        'merged_block': nxt.get('id'), 'from_page': nxt['page'],
        'reason': reason, 'rows_added': len(nxt.get('raw_rows') or []),
    }]
    prev['bbox'] = [min(prev['bbox'][0], nxt['bbox'][0]), min(prev['bbox'][1], nxt['bbox'][1]),
                    max(prev['bbox'][2], nxt['bbox'][2]), max(prev['bbox'][3], nxt['bbox'][3])]
    prev['spans_pages'] = sorted(set([prev['page']] + prev.get('continuation_pages', [])))


def estimate_figures(blocks, models):
    figs = []
    labels = [b for b in blocks if b.get('type') == 'figure_label']
    for b in blocks:
        if b.get('type') != 'caption':
            continue
        label = (b.get('caption_label') or '')
        if label not in ('figure', 'fig', 'scheme', 'chart'):
            continue
        cap = b['bbox']
        rects = []
        for lb in labels:
            if lb.get('page') != b['page']:
                continue
            r = lb.get('figure_rect')
            if not r:
                continue
            if r[3] <= cap[1] + 8 and cap[1] - r[3] <= 220:
                rects.append(r)
        box = list(cap)
        conf = 0.6
        note = 'figure area estimated from the caption; the artwork itself is not interpreted'
        if rects:
            box = [min(r[0] for r in rects), min(r[1] for r in rects),
                   max(r[2] for r in rects), max(r[3] for r in rects)]
            conf = 0.85
            note = ('figure frame located from the PDF drawing operators; in-figure text is '
                    'reported separately as figure_label blocks')
        figs.append({
            'caption_id': b.get('id'),
            'page': b['page'],
            'caption_bbox': cap,
            'estimated_bbox': box,
            'caption_number': b.get('caption_number'),
            'label': b['text'][:120],
            'confidence': conf,
            'note': note,
        })
    return figs


def check_text_quality(blocks):
    out = []
    for b in blocks:
        text = b.get('text') or ''
        if not text:
            continue
        bad = sum(1 for ch in text if ch == '\ufffd' or (ord(ch) < 32 and ch not in '\t\n'))
        if bad >= 3 or (len(text) > 20 and bad / len(text) > 0.05):
            out.append({'page': b['page'], 'bbox': b['bbox'], 'kind': 'garbled_text',
                        'reason': '字体缺少 ToUnicode 映射，文字可能乱码，已保留原样'})
    return out


def check_caption_conflicts(blocks):
    seen = {}
    out = []
    for b in blocks:
        if b.get('type') != 'caption':
            continue
        label = b.get('caption_label')
        num = b.get('caption_number')
        if not label or not num:
            continue
        key = (label, num)
        if key in seen and not b.get('continuation'):
            prev = seen[key]
            out.append({'page': b['page'], 'bbox': b['bbox'], 'kind': 'caption_conflict',
                        'reason': f'{label} {num} 在 p.{prev["page"]} 也出现过，需人工确认编号',
                        'first_occurrence': {'page': prev['page'], 'bbox': prev['bbox']}})
        seen.setdefault(key, b)
    return out


def render_markdown(doc):
    buf = []
    buf.append(f'# {os.path.basename(doc["source"]["path"])}\n')
    src = doc['source']
    buf.append(f'- pages: {src["pages"]} (read {src["pages_read"]}), sha256 `{src["sha256"][:16]}…`\n')
    buf.append(f'- backend: {src["backend"]}; status: **{doc["status"]}**\n')
    lay = doc['layout']
    buf.append(f'- layout: {lay["columns"]} column(s), body ≈ {lay["body_font_size"]} pt\n')
    buf.append('\n## Content in reading order\n')
    cur_page = None
    for b in doc['blocks']:
        if b.get('type') == 'figure_label':
            continue
        if b['page'] != cur_page:
            cur_page = b['page']
            buf.append(f'\n### Page {cur_page}\n')
        t = b['type']
        if t == 'title':
            buf.append(f'\n# {b["text"]}\n')
        elif t == 'authors':
            buf.append(f'*{b["text"]}*\n')
        elif t == 'abstract':
            buf.append(f'> {b["text"]}\n')
        elif t == 'heading':
            lvl = min(int(b.get('heading_level') or 1) + 1, 6)
            buf.append(f'\n{"#" * lvl} {b["text"]}\n')
        elif t == 'caption':
            buf.append(f'\n**{b["text"]}**\n')
        elif t == 'footnote':
            buf.append(f'\n<small>{b["text"]}</small>\n')
        elif t == 'formula':
            num = f'  ({b["formula_number"]})' if b.get('formula_number') else ''
            buf.append(f'\n```\n{b["rich_text"]}{num}\n```\n')
        elif t == 'table':
            buf.append('\n')
            buf.append('| ' + ' | '.join(str(c) for c in b['header']) + ' |\n')
            buf.append('|' + '---|' * len(b['header']) + '\n')
            for row in b['rows']:
                buf.append('| ' + ' | '.join(str(c) for c in row) + ' |\n')
            pages = b.get('spans_pages') or [b['page']]
            buf.append(f'<sub>table pages {pages}, {b.get("number") or "unlabelled"}</sub>\n')
        else:
            buf.append(f'\n{b["text"]}\n')
        buf.append(f'<!-- block p.{b["page"]} {b["locator"]} -->\n')
    if doc.get('figures'):
        buf.append('\n## Figures (located, not interpreted)\n')
        for f in doc['figures']:
            bb = f.get('estimated_bbox') or []
            buf.append(f'- p.{f["page"]} {f.get("caption_number") or ""} '
                       f'bbox=[{", ".join(f"{v:.1f}" for v in bb)}] '
                       f'(confidence {f.get("confidence")}) {f.get("label", "")}\n')
    if doc['uncertainties']:
        buf.append('\n## Uncertainties\n')
        for u in doc['uncertainties']:
            buf.append(f'- p.{u.get("page", "?")} **{u.get("kind")}** — {u.get("reason")}\n')
    return ''.join(buf)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Layout-aware PDF reader (reading order + provenance)')
    ap.add_argument('--pdf', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--ocr', default='auto', choices=['auto', 'on', 'off'])
    ap.add_argument('--max-pages', type=int, default=0)
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args(argv)
    try:
        return run(args)
    except Exception:
        traceback.print_exc()
        return 3


if __name__ == '__main__':
    sys.exit(main())
