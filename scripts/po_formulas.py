"""Formula and script (sup/sub) preservation for the PDF reading skill.

The skill does not try to understand the science of an equation. It keeps:
  * the equation text as printed,
  * the equation number beside it, e.g. "(2)",
  * superscript / subscript structure as ^{...} and _{...} plus explicit spans,
  * the Greek letters and symbols exactly as they appear in the text layer.
"""
from __future__ import annotations

import re
import unicodedata

MATH_CHARS = set('∑∫∬√±×÷≈≤≥≠∞∂∇∈∉⊂⊃∪∩→←↔⇒⇔∮∝⊕⊗⊥∥⌈⌉⌊⌋⋯…§¶†‡')
GREEK = set('αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ')
MATH_FONT_RE = re.compile(r'(?:CMMI|CMSY|CMEX|MSBM|MathematicalPi|STIX|Symbol|Math|Euclid|rsfs|wasy)',
                          re.I)
EQNUM_RE = re.compile(r'\(\s*(?P<num>\d{1,3}[a-z]?|[A-Z]\d{1,2}|[ivx]{1,4})\s*\)\s*$')
EQNUM_ONLY_RE = re.compile(r'^\(\s*(?P<num>\d{1,3}[a-z]?|[A-Z]\d{1,2})\s*\)$')


def is_math_char(ch):
    if ch in MATH_CHARS or ch in GREEK:
        return True
    cat = unicodedata.category(ch)
    return cat in ('Sm',)  # math symbols


def annotate_scripts(chars, base_size):
    """Split a run of chars into normal text plus superscript/subscript runs.

    Rule: a char is a script char when its font size is < 0.82 * base_size and its
    baseline is shifted by more than 0.35 * base_size; upward shifts are
    superscripts, downward shifts subscripts. This matches how LaTeX and Word
    encode scripts (smaller glyph, shifted origin) without relying on a ToUnicode
    mapping that may not exist.
    """
    plain = []
    rich = []
    spans = []
    run = None
    for ch in chars:
        size = ch.get('size') or base_size
        dy = ch.get('dy') or 0.0
        c = ch.get('c') or ''
        kind = None
        if base_size and size < 0.85 * base_size and dy > 0.18 * base_size:
            kind = 'sup'
        elif base_size and size < 0.85 * base_size and dy < -0.12 * base_size:
            kind = 'sub'
        if kind != run:
            if run:
                spans[-1]['end'] = len(plain)
            if kind:
                spans.append({'type': kind, 'start': len(plain), 'end': len(plain) + 1,
                              'text': ''})
            run = kind
        plain.append(c)
        if kind:
            spans[-1]['end'] = len(plain)
            spans[-1]['text'] += c
    text = ''.join(plain)
    # rich rendering with explicit markers
    if spans:
        pieces = []
        last = 0
        for sp in spans:
            pieces.append(text[last:sp['start']])
            mark = '^{' if sp['type'] == 'sup' else '_{'
            pieces.append(mark + sp['text'] + '}')
            last = sp['end']
        pieces.append(text[last:])
        rich = ''.join(pieces)
    else:
        rich = text
    return text, rich, spans


def formula_score(line):
    text = (line.get('text') or '').strip()
    if not text or len(text) > 240:
        return 0.0
    if line.get('type') in ('heading', 'caption'):
        return 0.0
    math = sum(1 for ch in text if is_math_char(ch))
    ratio = math / max(len(text), 1)
    font_hit = bool(MATH_FONT_RE.search(line.get('font') or ''))
    has_eq = ('=' in text) or ('≈' in text) or ('∼' in text)
    score = 0.0
    if font_hit:
        score += 0.55
    if has_eq:
        score += 0.25
    score += min(0.5, ratio * 2.2)
    if EQNUM_RE.search(text):
        score += 0.2
    if not has_eq and ratio < 0.10 and not font_hit:
        score -= 0.35
    return score


def collect_formulas(lines, page_no, column_label):
    """Group consecutive formula-ish lines and attach their equation number."""
    formulas = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        if formula_score(ln) < 0.5:
            i += 1
            continue
        group = [ln]
        j = i + 1
        while j < n:
            nxt = lines[j]
            gap = group[-1]['y'] - nxt['y']
            if formula_score(nxt) >= 0.5 and 0 <= gap <= 2.6 * nxt['size']:
                group.append(nxt)
                j += 1
            else:
                break
        text = ' '.join(g['text'].strip() for g in group)
        rich = ' '.join(g['rich'] for g in group)
        number = None
        m = EQNUM_RE.search(text)
        if m:
            number = m.group('num')
            text = EQNUM_RE.sub('', text).strip()
            rich = EQNUM_RE.sub('', rich).strip()
        # standalone number to the right of the group
        if number is None:
            for cand in lines:
                if cand is group[-1]:
                    continue
                if abs(cand['y'] - group[-1]['y']) <= 6 and EQNUM_ONLY_RE.match(cand['text'].strip()):
                    number = EQNUM_ONLY_RE.match(cand['text'].strip()).group('num')
                    break
        bbox = [min(g['x0'] for g in group), min(g['y0'] for g in group),
                max(g['x1'] for g in group), max(g['y1'] for g in group)]
        formulas.append({
            'text': text,
            'rich_text': rich,
            'number': number,
            'page': page_no,
            'bbox': [round(v, 1) for v in bbox],
            'column': column_label,
            'greek': sorted({ch for ch in text if ch in GREEK}),
            'spans': [sp for g in group for sp in g.get('scripts', [])],
            'symbols': sorted({ch for ch in text if is_math_char(ch)}),
            'line_ids': [g['id'] for g in group],
            'confidence': 0.9 if formula_score(group[0]) > 0.75 else 0.65,
        })
        i = j
    return formulas
