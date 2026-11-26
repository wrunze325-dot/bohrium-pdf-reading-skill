"""OCR fallback for pages without a text layer.

Order of preference (first available wins):
  1. pytesseract + tesseract binary
  2. tesseract CLI on a rendered PNG
  3. rapidocr_onnxruntime
  4. easyocr
  5. paddleocr

If none is installed the page is reported as unreadable, with the reason and the
rendered image path, instead of being silently filled with guessed text.
"""
from __future__ import annotations

import os
import shutil
import subprocess

BACKENDS = ('pytesseract', 'tesseract-cli', 'rapidocr', 'easyocr', 'paddleocr')


def available_backend():
    try:
        import pytesseract  # noqa: F401
        if shutil.which('tesseract'):
            return 'pytesseract'
    except Exception:
        pass
    if shutil.which('tesseract'):
        return 'tesseract-cli'
    for mod, name in (('rapidocr_onnxruntime', 'rapidocr'), ('easyocr', 'easyocr'),
                      ('paddleocr', 'paddleocr')):
        try:
            __import__(mod)
            return name
        except Exception:
            continue
    return None


def render_page(page, outdir, pageno, dpi=200):
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f'page_{pageno:03d}.png')
    try:
        pix = page.get_pixmap(dpi=dpi)
        pix.save(path)
        return path
    except Exception:
        return None


def run_ocr(image_path, backend=None):
    """Return list of {text, bbox_local, conf} in image pixel coordinates."""
    backend = backend or available_backend()
    if not image_path or not backend:
        return None, None
    try:
        if backend == 'pytesseract':
            import pytesseract
            from PIL import Image
            img = Image.open(image_path)
            data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
            words = []
            for i, txt in enumerate(data['text']):
                if not (txt or '').strip():
                    continue
                words.append({
                    'text': txt.strip(),
                    'bbox_local': [data['left'][i], data['top'][i],
                                   data['left'][i] + data['width'][i],
                                   data['top'][i] + data['height'][i]],
                    'conf': float(data['conf'][i]) / 100.0 if data['conf'][i] not in ('-1', -1) else 0.0,
                })
            return words, backend
        if backend == 'tesseract-cli':
            out = image_path + '.tsv'
            subprocess.run(['tesseract', image_path, image_path, 'tsv'], check=True,
                           capture_output=True, timeout=180)
            words = []
            with open(out, encoding='utf-8', errors='replace') as fh:
                for line in fh.read().splitlines()[1:]:
                    parts = line.split('\t')
                    if len(parts) < 12 or not parts[11].strip():
                        continue
                    lvl = int(parts[0])
                    if lvl != 5:
                        continue
                    try:
                        conf = float(parts[10])
                    except ValueError:
                        conf = 0.0
                    words.append({
                        'text': parts[11].strip(),
                        'bbox_local': [int(parts[6]), int(parts[7]),
                                       int(parts[6]) + int(parts[8]), int(parts[7]) + int(parts[9])],
                        'conf': conf / 100.0,
                    })
            return words, backend
        if backend == 'rapidocr':
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
            result, _ = engine(image_path)
            words = []
            for box, txt, score in result or []:
                ys = [p[1] for p in box]
                xs = [p[0] for p in box]
                words.append({'text': txt, 'bbox_local': [min(xs), min(ys), max(xs), max(ys)],
                              'conf': float(score)})
            return words, backend
        if backend == 'easyocr':
            import easyocr
            reader = easyocr.Reader(['en'])
            res = reader.readtext(image_path)
            words = [{'text': t, 'bbox_local': [min(p[0] for p in b), min(p[1] for p in b),
                                                max(p[0] for p in b), max(p[1] for p in b)],
                      'conf': float(c)} for b, t, c in res]
            return words, backend
        if backend == 'paddleocr':
            from paddleocr import PaddleOCR
            ocr = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
            res = ocr.ocr(image_path, cls=True)
            words = []
            for line in (res[0] if res else []):
                box, (txt, conf) = line
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                words.append({'text': txt, 'bbox_local': [min(xs), min(ys), max(xs), max(ys)],
                              'conf': float(conf)})
            return words, backend
    except Exception:
        return None, backend
    return None, backend


def scale_words(words, page_width, page_height, dpi):
    """Convert pixel boxes from a dpi render back to PDF points."""
    if not words:
        return []
    scale = 72.0 / max(dpi, 1)
    out = []
    for w in words:
        x0, y0, x1, y1 = w['bbox_local']
        out.append({
            'text': w['text'],
            'bbox': [x0 * scale, y0 * scale, x1 * scale, y1 * scale],
            'conf': w.get('conf', 0.0),
        })
    return out
