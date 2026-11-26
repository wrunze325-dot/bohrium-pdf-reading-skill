"""Build small, licence-clean PDF fixtures that expose real parsing failures.

Fixture A  fixtures/two_column.pdf  (2 pages)
    * full-width title and a full-width section heading on page 2
    * two-column body drawn in VISUAL ROW ORDER (left line, right line, ...)
      -> a stream-order reader splices the two columns into single lines
    * figure caption in the right column
    * Table 1 starts at the bottom of page 1 and continues on page 2 without
      repeating its header -> a naive reader loses the header mapping
    * footnote, running head, page number
    * one display formula with a raised superscript, a lowered subscript,
      Greek letters and the equation number (2) at the right margin

Fixture B  fixtures/scanned.pdf  (1 page, no text layer)
    * page 2 of fixture A rasterised, wrapped as an image-only PDF
      -> tests the "no text layer" branch and honest unreadable reporting

Run:  python make_fixtures.py
Writes fixtures/*.pdf, fixtures/truth.json, fixtures/README.md
"""
import json
import os
import sys

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'fixtures')

W, H = 595.0, 842.0
LM = 56.0
COL_W = 234.0
GUTTER = 15.0
RX = LM + COL_W + GUTTER          # 305
RE = RX + COL_W                   # 539
BODY = 9.5
LEAD = 11.5

FONT_REG = 'Helvetica'
FONT_BOLD = 'Helvetica-Bold'
for _cand, _name in ((r'C:\Windows\Fonts\arial.ttf', 'FixSans'),
                     (r'C:\Windows\Fonts\arialbd.ttf', 'FixSans-Bold')):
    try:
        if os.path.exists(_cand):
            pdfmetrics.registerFont(TTFont(_name, _cand))
            if _name == 'FixSans':
                FONT_REG = _name
            else:
                FONT_BOLD = _name
    except Exception:  # pragma: no cover
        pass

COLS = [LM, LM + 100, LM + 160, LM + 234]   # table column edges
WARN = []


def sw(text, size, font=None):
    return pdfmetrics.stringWidth(text, font or FONT_REG, size)


def put(c, x, y, s, size=BODY, font=None, max_w=None, label=''):
    font = font or FONT_REG
    w = sw(s, size, font)
    if max_w and w > max_w:
        WARN.append(f'{label or s[:30]!r} width {w:.1f} > {max_w}')
    c.setFont(font, size)
    c.drawString(x, y, s)
    return w


def rich(c, x, y, parts, size=BODY):
    cur = x
    for text, factor, dy in parts:
        sz = size * factor
        c.setFont(FONT_REG, sz)
        c.drawString(cur, y + dy, text)
        cur += sw(text, sz)


def table(c, _x, top, rows):
    row_h = 14.0
    c.setLineWidth(0.6)
    for ri, row in enumerate(rows):
        y0 = top - (ri + 1) * row_h
        for ci, cell in enumerate(row):
            x0, x1 = COLS[ci], COLS[ci + 1]
            c.rect(x0, y0, x1 - x0, row_h, stroke=1, fill=0)
            avail = x1 - x0 - 6
            size = 7.6
            while size > 5.0 and sw(cell, size) > avail:
                size -= 0.2
            put(c, x0 + 3, y0 + 4, cell, size, label=f'cell {cell[:14]}')
    return top - len(rows) * row_h


HDR = 'Journal of Layout Studies 3 (2026) 1-8'
TITLE = 'Reading Order Recovery in Two-Column Scientific PDFs'
AUTH = 'A. Author, B. Author (Institute of Document Engineering)'
ABS1 = 'Abstract. We study how a layout-aware reader restores column order in two-column'
ABS2 = 'papers and keeps every sentence anchored to the page and block it came from.'
H1 = '1  Introduction'
L1 = ['Two-column papers set text in narrow columns,',
      'so one paragraph is split across two frames.',
      'A stream-order reader glues right onto left,',
      'and turns two sentences into one line.',
      'The damage is silent: every word is there.']
R1 = ['Gutter detection alone is not enough: a title,',
      'a caption or a wide table breaks the page',
      'into bands that must be ordered separately.',
      'We segment first, then order inside each band.',
      'Every block keeps its page and bounding box.']
FIGCAP = 'Figure 1: Bands split by a spanning title.'
H2 = '2  Method'
L2 = ['We group characters into lines by baseline,',
      'then find vertical gutters from line spans.',
      'Lines that cross a gutter become separators.',
      'The rest are sorted column by column.']
T1CAP = 'Table 1: Spliced lines and order accuracy.'
T1_HEAD = ['Layout', 'Spliced', 'Order acc.']
T1_ROW1 = ['Two-column (stream order)', '5', '0.61']
T1_ROW2 = ['Two-column + skill', '0', '0.98']
T1_ROW3 = ['Single column (control)', '1', '0.55']
FN1 = '1 Funded by the Layout Foundation. Correspondence: a.author@example.org'
H3 = '3  Results and Discussion'
T1CONT = 'Table 1 (continued)'
L3 = ['The stream reader splices five lines here,',
      'which is the common word-processor failure.',
      'Band segmentation removes every splice.',
      'The remaining cost is one pass over the spans.']
R3 = ['Evidence is a page number plus a bounding box,',
      'so a reviewer can check the sentence in place.',
      'Table rows keep the header of their first page.',
      'Formulas keep number and script structure.']


def page_one(c):
    put(c, LM, H - 32, HDR, 7.5, label='hdr')
    put(c, LM, H - 72, TITLE, 15, FONT_BOLD, max_w=W - 2 * LM, label='title')
    put(c, LM, H - 92, AUTH, 9.5, max_w=W - 2 * LM, label='auth')
    put(c, LM, H - 112, ABS1, 8.5, max_w=W - 2 * LM, label='abs1')
    put(c, LM, H - 123, ABS2, 8.5, max_w=W - 2 * LM, label='abs2')
    top = H - 150
    put(c, LM, top, H1, 11, FONT_BOLD, max_w=COL_W, label='h1')
    ys = [top - 16 - i * LEAD for i in range(5)]
    for ltxt, rtxt, y in zip(L1, R1, ys):          # visual row order on purpose
        put(c, LM, y, ltxt, BODY, max_w=COL_W, label='L1')
        put(c, RX, y, rtxt, BODY, max_w=COL_W, label='R1')

    c.setLineWidth(0.8)
    c.rect(RX + 30, top - 105, COL_W - 60, 30, stroke=1, fill=0)
    put(c, RX + 34, top - 88, 'band', 6.5, label='fig')
    put(c, RX + 150, top - 88, 'bands', 6.5, label='fig2')
    put(c, RX, top - 118, FIGCAP, 8, max_w=COL_W, label='figcap')

    put(c, LM, top - 140, H2, 11, FONT_BOLD, max_w=COL_W, label='h2')
    for i, txt in enumerate(L2):
        put(c, LM, top - 156 - i * LEAD, txt, BODY, max_w=COL_W, label='L2')

    capy = top - 210
    put(c, LM, capy, T1CAP, 8, max_w=COL_W, label='t1cap')
    table(c, LM, capy - 8, [T1_HEAD, T1_ROW1])
    put(c, LM, 96, FN1, 7.5, max_w=W - 2 * LM, label='fn1')
    put(c, W / 2 - 2, 40, '1', 9, label='pg1')


def page_two(c):
    put(c, LM, H - 32, HDR, 7.5, label='hdr2')
    w = sw(H3, 12, FONT_BOLD)
    put(c, (W - w) / 2, H - 72, H3, 12, FONT_BOLD, max_w=W - 2 * LM, label='h3')
    put(c, LM, H - 96, T1CONT, 7.5, label='t1cont')
    table(c, LM, H - 104, [T1_ROW2, T1_ROW3])
    top = H - 150
    for i, (ltxt, rtxt) in enumerate(zip(L3, R3)):
        y = top - i * LEAD
        put(c, LM, y, ltxt, BODY, max_w=COL_W, label='L3')
        put(c, RX, y, rtxt, BODY, max_w=COL_W, label='R3')
    fy = top - 4 * LEAD - 18
    rich(c, RX, fy, [('S = ', 1.0, 0), ('\u03b1', 1.0, 0), ('x', 1.0, 0), ('2', 0.65, 3.4),
                     (' + ', 1.0, 0), ('\u03b2', 1.0, 0), ('y', 1.0, 0),
                     ('\u22121', 0.65, -2.0)])
    put(c, RX + COL_W - 22, fy, '(2)', BODY, label='eqnum')
    put(c, W / 2 - 2, 40, '2', 9, label='pg2')


# --- the semantic truth, written by hand, not derived from the drawing order ---
ORDER = {
    'page1_body': ['TITLE', 'AUTH', 'ABS1', 'ABS2', 'H1'] + [f'L1{i}' for i in 'abcde'] +
                  ['H2'] + [f'L2{i}' for i in 'abcd'] + ['T1CAP', 'T1R1C0', 'T1R1C1'] +
                  [f'R1{i}' for i in 'abcde'] + ['FIGCAP'],
    'page2_body': ['H3', 'T1CONT', 'T1RC0', 'T1RC1'] + [f'L3{i}' for i in 'abcd'] +
                  [f'R3{i}' for i in 'abcd'] + ['FORM1'],
}
PERIPHERY = {'page1': ['HDR1', 'FN1', 'PG1'], 'page2': ['HDR2', 'PG2']}

ELEMENTS = []


def _el(token, kind, page, column, text, **kw):
    d = dict(token=token, kind=kind, page=page, column=column, text=text)
    d.update(kw)
    ELEMENTS.append(d)


def build_elements():
    _el('HDR1', 'header', 1, 'full', HDR)
    _el('TITLE', 'title', 1, 'full', TITLE)
    _el('AUTH', 'authors', 1, 'full', AUTH)
    _el('ABS1', 'abstract', 1, 'full', ABS1)
    _el('ABS2', 'abstract', 1, 'full', ABS2)
    _el('H1', 'heading', 1, 'left', H1)
    for i, t in zip('abcde', L1):
        _el(f'L1{i}', 'body', 1, 'left', t)
    for i, t in zip('abcde', R1):
        _el(f'R1{i}', 'body', 1, 'right', t)
    _el('FIGCAP', 'caption', 1, 'right', FIGCAP)
    _el('H2', 'heading', 1, 'left', H2)
    for i, t in zip('abcd', L2):
        _el(f'L2{i}', 'body', 1, 'left', t)
    _el('T1CAP', 'caption', 1, 'left', T1CAP)
    _el('T1R1C0', 'table_header', 1, 'left', ' | '.join(T1_HEAD), cells=T1_HEAD, header=True)
    _el('T1R1C1', 'table_row', 1, 'left', ' | '.join(T1_ROW1), cells=T1_ROW1)
    _el('FN1', 'footnote', 1, 'left', FN1)
    _el('PG1', 'footer', 1, 'full', '1')
    _el('HDR2', 'header', 2, 'full', HDR)
    _el('H3', 'heading', 2, 'full', H3)
    _el('T1CONT', 'caption', 2, 'left', T1CONT)
    _el('T1RC0', 'table_row', 2, 'left', ' | '.join(T1_ROW2), cells=T1_ROW2)
    _el('T1RC1', 'table_row', 2, 'left', ' | '.join(T1_ROW3), cells=T1_ROW3)
    for i, t in zip('abcd', L3):
        _el(f'L3{i}', 'body', 2, 'left', t)
    for i, t in zip('abcd', R3):
        _el(f'R3{i}', 'body', 2, 'right', t)
    _el('FORM1', 'formula', 2, 'right', 'S = \u03b1x2 + \u03b2y\u22121')
    _el('PG2', 'footer', 2, 'full', '2')


SINGLE = [
    'A single-column page is the control condition: the reader must not invent',
    'columns where there are none. Every line starts at the left margin, the text',
    'runs the full width of the type area, and paragraph breaks are marked by',
    'vertical space rather than by a column change. A layout detector that keys',
    'on whitespace alone can split such a page in the middle of a sentence, so',
    'the control case matters as much as the two-column case does.',
]


def single_column():
    path = os.path.join(OUT, 'single_column.pdf')
    c = pdfcanvas.Canvas(path, pagesize=(W, H))
    c.setTitle('Single column control page')
    put(c, LM, H - 60, 'Single Column Control Page', 15, FONT_BOLD, max_w=W - 2 * LM, label='single title')
    y = H - 95
    n = 0
    for _ in range(4):                      # four paragraphs of wrapped lines
        for txt in SINGLE:
            put(c, LM, y, txt, BODY, max_w=W - 2 * LM, label='single body')
            y -= LEAD
            n += 1
        y -= LEAD                            # paragraph spacing
    put(c, LM, 60, '1', 9, label='single footer')
    c.showPage()
    c.save()
    if WARN:
        print('WIDTH WARNINGS (single):', *WARN, sep='\n  ', file=sys.stderr)
    return path, n


def main():
    out = OUT
    if len(sys.argv) > 1 and sys.argv[1] in ('--out',):
        out = os.path.abspath(sys.argv[2])
    globals()['OUT'] = out
    os.makedirs(out, exist_ok=True)
    path = os.path.join(OUT, 'two_column.pdf')
    c = pdfcanvas.Canvas(path, pagesize=(W, H))
    c.setTitle(TITLE)
    c.setAuthor('Fixture Author')
    c.setSubject('Synthetic two-column layout fixture')
    page_one(c)
    c.showPage()
    page_two(c)
    c.showPage()
    c.save()
    if WARN:
        print('WIDTH WARNINGS:', *WARN, sep='\n  ', file=sys.stderr)

    single_path, single_lines = single_column()

    scanned = False
    try:
        import pymupdf
        src = pymupdf.open(path)
        pix = src[1].get_pixmap(dpi=130)
        tmp = os.path.join(OUT, '_page2.png')
        pix.save(tmp)
        out = pymupdf.open()
        page = out.new_page(width=W, height=H)
        page.insert_image(page.rect, filename=tmp)
        out.save(os.path.join(OUT, 'scanned.pdf'), deflate=True, garbage=4)
        out.close()
        src.close()
        os.remove(tmp)
        scanned = True
    except Exception as exc:  # pragma: no cover
        print('scanned fixture skipped:', exc, file=sys.stderr)

    build_elements()
    meta = {
        'fixture': 'two_column.pdf',
        'pages': 2,
        'generated_by': 'tests/make_fixtures.py',
        'licence': 'synthetic text written for this project; no third-party content',
        'reading_order': ORDER['page1_body'] + ORDER['page2_body'],
        'reading_order_by_page': ORDER,
        'periphery': PERIPHERY,
        'elements': ELEMENTS,
        'columns': {'left_x': [LM, LM + COL_W], 'right_x': [RX, RE],
                    'gutter': [LM + COL_W, RX]},
        'tables': {'table_1': {'caption': T1CAP, 'header': T1_HEAD,
                               'rows': [T1_ROW1, T1_ROW2, T1_ROW3],
                               'spans_pages': [1, 2]}},
        'formulas': {'FORM1': {'number': '(2)', 'superscripts': ['2', '\u22121'],
                               'greek': ['\u03b1', '\u03b2']}},
        'scanned_fixture': scanned,
        'single_column': {'fixture': os.path.basename(single_path), 'body_lines': single_lines,
                          'expected_columns': 1, 'expected_lines': SINGLE},
    }
    with open(os.path.join(OUT, 'truth.json'), 'w', encoding='utf-8') as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    with open(os.path.join(OUT, 'README.md'), 'w', encoding='utf-8') as fh:
        fh.write(
            '# Test fixtures\n\n'
            'Both PDFs are produced by `tests/make_fixtures.py`. The text was written for this\n'
            'project and carries no third-party material, so the fixtures can be redistributed.\n\n'
            '| file | what it exercises |\n'
            '|---|---|\n'
            '| `two_column.pdf` | two-column body drawn in visual row order, full-width title,\n'
            'spanning heading, figure caption, Table 1 spanning pages 1-2 without a repeated\n'
            'header, footnote, running head, page number, display formula with sup/sub and `(2)` |\n'
            '| `scanned.pdf` | page 2 rasterised, no text layer, OCR branch |\n\n'
            '`truth.json` lists the expected reading order and the expected Table 1 mapping.\n'
        )
    print('wrote', path, '| scanned:', scanned)


if __name__ == '__main__':
    main()
