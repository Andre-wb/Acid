"""
Печать DataMatrix в PDF (векторная графика, сетка на A4) и самопроверка кодов.
"""
import random

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

import datamatrix as dm

_FONT = "Helvetica"
_CYR = False


def _setup_font():
    """Подключает шрифт с кириллицей (Arial в Windows / DejaVu в Linux); иначе - только латиница."""
    global _FONT, _CYR
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    for path in (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\tahoma.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "/Library/Fonts/Arial.ttf"):
        try:
            pdfmetrics.registerFont(TTFont("CHZ", path))
            _FONT, _CYR = "CHZ", True
            return
        except Exception:
            continue


_setup_font()


def verify_code(code, deep=False):
    """Проверка: код -> символ -> обратно в текст. deep=True: ещё через картинку
    (и через zxing-cpp, если установлен). Возвращает (матрица, список ошибок)."""
    errors = []
    text = code.gs_text
    try:
        mat = dm.make_matrix(text)
    except dm.DMError as e:
        return None, ["не удалось создать символ: %s" % e]
    try:
        back, gs1 = dm.decode_matrix(mat)
        if back != text:
            errors.append("после декодирования код отличается от исходного")
        if not gs1:
            errors.append("нет признака GS1 (FNC1)")
    except dm.DMError as e:
        errors.append("ошибка декодирования: %s" % e)
    if deep and not errors:
        try:
            img = dm.render_image(mat, 6)
            back, _ = dm.decode_image(img)
            if back != text:
                errors.append("после рендера в картинку код отличается")
            ext = dm.external_decode(img)
            if ext is not None and ext.replace(dm.GS, "") != text.replace(dm.GS, ""):
                errors.append("zxing-cpp прочитал другое значение")
        except dm.DMError as e:
            errors.append("проверка по картинке: %s" % e)
    return mat, errors


def verify_all(codes, progress=None):
    """Проверяет все коды (символ), выборочно и «глубоко» (картинка + zxing).
    Возвращает список (код, [ошибки])."""
    bad = []
    n = len(codes)
    deep_idx = set(range(min(n, 5)))
    if n > 5:
        deep_idx |= set(random.Random(1).sample(range(n), min(n, 60)))
    for i, c in enumerate(codes):
        _, errs = verify_code(c, deep=i in deep_idx)
        if errs:
            bad.append((c, errs))
        if progress and i % 100 == 0:
            progress("Проверка кодов: %d / %d" % (i, n))
    return bad


def module_mm(side, size_mm):
    return size_mm / side


def draw_matrix(c, mat, x, y, size):
    """Рисует символ в квадрате size (pt) с левым нижним углом (x, y)."""
    side = len(mat)
    m = size / side
    eps = m * 0.03
    c.setFillColorRGB(0, 0, 0)
    for r in range(side):
        row = mat[r]
        yy = y + size - (r + 1) * m
        cc = 0
        while cc < side:
            if row[cc]:
                start = cc
                while cc < side and row[cc]:
                    cc += 1
                c.rect(x + start * m, yy - eps / 2, (cc - start) * m, m + eps, stroke=0, fill=1)
            else:
                cc += 1


def make_pdf(codes, path, size_mm=15.0, gap_mm=4.0, caption=True, title="", progress=None):
    """Сетка кодов на листах A4. Возвращает (число страниц, минимальный модуль в мм)."""
    W, H = A4
    margin = 10 * mm
    head = 8 * mm
    size = size_mm * mm
    gap = gap_mm * mm
    cap_h = 3.2 * mm if caption else 0
    cell_w = size + gap
    cell_h = size + cap_h + gap
    cols = max(1, int((W - 2 * margin + gap) // cell_w))
    rows = max(1, int((H - 2 * margin - head + gap) // cell_h))
    per_page = cols * rows
    # центрируем сетку по ширине
    grid_w = cols * cell_w - gap
    x0 = (W - grid_w) / 2

    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle(title or "DataMatrix")
    pages = (len(codes) + per_page - 1) // per_page or 1
    min_module = None
    for i, code in enumerate(codes):
        idx = i % per_page
        if idx == 0:
            if i:
                c.showPage()
            c.setFont(_FONT, 7)
            c.setFillColorRGB(0.3, 0.3, 0.3)
            if _CYR:
                head_txt = "%s  |  коды %d–%d из %d  |  размер %.1f мм" % (
                    title, i + 1, min(i + per_page, len(codes)), len(codes), size_mm)
                page_txt = "стр. %d / %d" % (i // per_page + 1, pages)
            else:
                head_txt = "codes %d-%d of %d | size %.1f mm" % (
                    i + 1, min(i + per_page, len(codes)), len(codes), size_mm)
                page_txt = "page %d / %d" % (i // per_page + 1, pages)
            c.drawString(margin, H - margin, head_txt)
            c.drawRightString(W - margin, H - margin, page_txt)
        r, col = divmod(idx, cols)
        x = x0 + col * cell_w
        ytop = H - margin - head - r * cell_h
        y = ytop - size
        mat, errs = verify_code(code)
        if mat is None:
            raise dm.DMError("Код %s: %s" % (code.key, "; ".join(errs)))
        mm_ = size_mm / len(mat)
        min_module = mm_ if min_module is None else min(min_module, mm_)
        draw_matrix(c, mat, x, y, size)
        if caption:
            c.setFont(_FONT, 4.5)
            c.setFillColorRGB(0, 0, 0)
            c.drawString(x, y - 2.6 * mm, "%s %s" % (code.gtin[1:], code.serial))
        if progress and i % 100 == 0:
            progress("PDF: %d / %d" % (i, len(codes)))
    c.save()
    return pages, min_module


def make_size_test_pdf(code, path, sizes=(10, 12, 14, 15, 16, 18, 20, 25), caption=True):
    """Тестовый лист: один и тот же код разных размеров - для проверки сканером и принтером."""
    W, H = A4
    margin = 15 * mm
    c = canvas.Canvas(path, pagesize=A4)
    c.setTitle("Тест размеров DataMatrix")
    mat, errs = verify_code(code, deep=True)
    if mat is None:
        raise dm.DMError("; ".join(errs))
    c.setFont(_FONT, 9)
    c.drawString(margin, H - margin, "Test sheet: one code at different sizes. Print at 100%% (Actual size). Symbol %dx%d modules." % (len(mat), len(mat)))
    x = margin
    y = H - margin - 10 * mm
    rowh = 0
    for s in sizes:
        size = s * mm
        if x + size > W - margin:
            x = margin
            y -= rowh + 10 * mm
            rowh = 0
        draw_matrix(c, mat, x, y - size, size)
        c.setFont(_FONT, 7)
        c.drawString(x, y - size - 3 * mm, "%d mm (module %.2f mm)" % (s, s / len(mat)))
        x += size + 8 * mm
        rowh = max(rowh, size)
    c.save()
    return errs
