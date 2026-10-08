"""
Генератор GS1 DataMatrix (ECC200) на чистом Python, без внешних библиотек,
плюс декодер для самопроверки.

Кодирование: ASCII-режим с упаковкой пар цифр, первый кодовое слово FNC1 (232),
символ-разделитель GS (0x1D) кодируется как FNC1 (232) - так требует GS1 DataMatrix.
Размеры: квадратные символы 10x10 ... 52x52.
"""
from functools import lru_cache

GS = "\x1d"
FNC1 = 232
PAD = 129

# (сторона, кодовых слов данных, слов коррекции всего, блоков RS, регионов по стороне, сторона региона)
SIZES = [
    (10, 3, 5, 1, 1, 8),
    (12, 5, 7, 1, 1, 10),
    (14, 8, 10, 1, 1, 12),
    (16, 12, 12, 1, 1, 14),
    (18, 18, 14, 1, 1, 16),
    (20, 22, 18, 1, 1, 18),
    (22, 30, 20, 1, 1, 20),
    (24, 36, 24, 1, 1, 22),
    (26, 44, 28, 1, 1, 24),
    (32, 62, 36, 1, 2, 14),
    (36, 86, 42, 1, 2, 16),
    (40, 114, 48, 1, 2, 18),
    (44, 144, 56, 1, 2, 20),
    (48, 174, 68, 1, 2, 22),
    (52, 204, 84, 2, 2, 24),
]


class DMError(Exception):
    pass


# ---------------------------------------------------------------- Рид-Соломон
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x12D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


@lru_cache(maxsize=None)
def _gen_poly(n):
    g = [1]
    for i in range(1, n + 1):
        ng = g + [0]
        for j, coef in enumerate(g):
            ng[j + 1] ^= _mul(coef, _EXP[i])
        g = ng
    return tuple(g)


def rs_encode(data, n):
    """n слов коррекции для списка data."""
    g = _gen_poly(n)
    ecc = [0] * n
    for d in data:
        k = d ^ ecc[0]
        ecc = ecc[1:] + [0]
        if k:
            for j in range(n):
                ecc[j] ^= _mul(g[j + 1], k)
    return ecc


# ---------------------------------------------------------------- кодирование
def encode_codewords(text):
    """Строка (GS = \\x1d) -> кодовые слова (с ведущим FNC1)."""
    cw = [FNC1]
    i = 0
    n = len(text)
    digits = "0123456789"
    while i < n:
        c = text[i]
        if c == GS:
            cw.append(FNC1)
            i += 1
        elif c in digits and i + 1 < n and text[i + 1] in digits:
            cw.append(130 + int(text[i:i + 2]))
            i += 2
        else:
            o = ord(c)
            if o > 127:
                raise DMError("Символ вне ASCII: %r" % c)
            cw.append(o + 1)
            i += 1
    return cw


def decode_codewords(cws):
    """Кодовые слова данных -> (строка с GS, признак GS1)."""
    out = []
    gs1 = False
    for idx, c in enumerate(cws):
        if c == PAD:
            break
        if c == FNC1:
            if idx == 0:
                gs1 = True
            else:
                out.append(GS)
        elif 1 <= c <= 128:
            out.append(chr(c - 1))
        elif 130 <= c <= 229:
            out.append("%02d" % (c - 130))
        else:
            raise DMError("Неподдерживаемое кодовое слово %d" % c)
    return "".join(out), gs1


def _pad(cw, cap):
    cw = list(cw)
    if len(cw) < cap:
        cw.append(PAD)
    while len(cw) < cap:
        pos = len(cw) + 1
        r = ((149 * pos) % 253) + 1
        t = PAD + r
        if t > 254:
            t -= 254
        cw.append(t)
    return cw


def pick_size(n_cw, min_side=0):
    for spec in SIZES:
        if spec[1] >= n_cw and spec[0] >= min_side:
            return spec
    raise DMError("Данные не помещаются в символ до 52x52 (%d слов)" % n_cw)


def _full_stream(data, spec):
    _, cap, ecc_total, blocks, _, _ = spec
    per = ecc_total // blocks
    eccs = [rs_encode(data[b::blocks], per) for b in range(blocks)]
    ecc = [0] * ecc_total
    for b in range(blocks):
        for i in range(per):
            ecc[i * blocks + b] = eccs[b][i]
    return list(data) + ecc


# ---------------------------------------------------------------- размещение
@lru_cache(maxsize=None)
def placement(n):
    """Размещение для области данных n x n (ISO 16022, Annex F).
    Возвращает (cells, fill): cells[pos][bit-1] = (row, col); fill = тёмные модули-заполнители."""
    nrows = ncols = n
    grid = [[None] * ncols for _ in range(nrows)]
    cells = {}

    def module(row, col, pos, bit):
        if row < 0:
            row += nrows
            col += 4 - ((nrows + 4) % 8)
        if col < 0:
            col += ncols
            row += 4 - ((ncols + 4) % 8)
        grid[row][col] = (pos, bit)
        cells.setdefault(pos, [None] * 8)[bit - 1] = (row, col)

    def utah(row, col, pos):
        module(row - 2, col - 2, pos, 1)
        module(row - 2, col - 1, pos, 2)
        module(row - 1, col - 2, pos, 3)
        module(row - 1, col - 1, pos, 4)
        module(row - 1, col, pos, 5)
        module(row, col - 2, pos, 6)
        module(row, col - 1, pos, 7)
        module(row, col, pos, 8)

    def corner1(pos):
        module(nrows - 1, 0, pos, 1)
        module(nrows - 1, 1, pos, 2)
        module(nrows - 1, 2, pos, 3)
        module(0, ncols - 2, pos, 4)
        module(0, ncols - 1, pos, 5)
        module(1, ncols - 1, pos, 6)
        module(2, ncols - 1, pos, 7)
        module(3, ncols - 1, pos, 8)

    def corner2(pos):
        module(nrows - 3, 0, pos, 1)
        module(nrows - 2, 0, pos, 2)
        module(nrows - 1, 0, pos, 3)
        module(0, ncols - 4, pos, 4)
        module(0, ncols - 3, pos, 5)
        module(0, ncols - 2, pos, 6)
        module(0, ncols - 1, pos, 7)
        module(1, ncols - 1, pos, 8)

    def corner3(pos):
        module(nrows - 3, 0, pos, 1)
        module(nrows - 2, 0, pos, 2)
        module(nrows - 1, 0, pos, 3)
        module(0, ncols - 2, pos, 4)
        module(0, ncols - 1, pos, 5)
        module(1, ncols - 1, pos, 6)
        module(2, ncols - 1, pos, 7)
        module(3, ncols - 1, pos, 8)

    def corner4(pos):
        module(nrows - 1, 0, pos, 1)
        module(nrows - 1, ncols - 1, pos, 2)
        module(0, ncols - 3, pos, 3)
        module(0, ncols - 2, pos, 4)
        module(0, ncols - 1, pos, 5)
        module(1, ncols - 3, pos, 6)
        module(1, ncols - 2, pos, 7)
        module(1, ncols - 1, pos, 8)

    pos = 0
    row, col = 4, 0
    while True:
        if row == nrows and col == 0:
            corner1(pos); pos += 1
        if row == nrows - 2 and col == 0 and ncols % 4 != 0:
            corner2(pos); pos += 1
        if row == nrows - 2 and col == 0 and ncols % 8 == 4:
            corner3(pos); pos += 1
        if row == nrows + 4 and col == 2 and ncols % 8 == 0:
            corner4(pos); pos += 1
        while True:
            if row < nrows and col >= 0 and grid[row][col] is None:
                utah(row, col, pos); pos += 1
            row -= 2
            col += 2
            if not (row >= 0 and col < ncols):
                break
        row += 1
        col += 3
        while True:
            if row >= 0 and col < ncols and grid[row][col] is None:
                utah(row, col, pos); pos += 1
            row += 2
            col -= 2
            if not (row < nrows and col >= 0):
                break
        row += 3
        col += 1
        if not (row < nrows or col < ncols):
            break
    fill = []
    if grid[nrows - 1][ncols - 1] is None:
        fill = [(nrows - 1, ncols - 1), (nrows - 2, ncols - 2)]
    return cells, fill


# ---------------------------------------------------------------- сборка символа
def make_matrix(text, min_side=0):
    """text (GS = \\x1d) -> матрица N x N (1 = тёмный модуль)."""
    cw = encode_codewords(text)
    spec = pick_size(len(cw), min_side)
    side, cap, _, _, regions, dsize = spec
    data = _pad(cw, cap)
    stream = _full_stream(data, spec)

    nd = regions * dsize
    cells, fill = placement(nd)
    if len(cells) != len(stream):
        raise DMError("Внутренняя ошибка размещения (%d != %d)" % (len(cells), len(stream)))
    area = [[0] * nd for _ in range(nd)]
    for pos, word in enumerate(stream):
        for bit in range(8):
            r, c = cells[pos][bit]
            area[r][c] = (word >> (7 - bit)) & 1
    for r, c in fill:
        area[r][c] = 1

    mat = [[0] * side for _ in range(side)]
    for r in range(nd):
        ri, rr = divmod(r, dsize)
        for c in range(nd):
            ci, cc = divmod(c, dsize)
            mat[ri * (dsize + 2) + 1 + rr][ci * (dsize + 2) + 1 + cc] = area[r][c]
    n = dsize + 2
    for ri in range(regions):
        for ci in range(regions):
            r0, c0 = ri * n, ci * n
            for k in range(n):
                mat[r0 + n - 1][c0 + k] = 1
                mat[r0 + k][c0] = 1
                mat[r0][c0 + k] = 1 if k % 2 == 0 else 0
                mat[r0 + k][c0 + n - 1] = 1 if k % 2 == 1 else 0
    return mat


# ---------------------------------------------------------------- декодер (самопроверка)
def decode_matrix(mat):
    """Матрица -> (текст с GS, признак GS1). Бросает DMError при любой ошибке."""
    side = len(mat)
    spec = next((s for s in SIZES if s[0] == side), None)
    if spec is None or any(len(r) != side for r in mat):
        raise DMError("Неизвестный размер символа %d" % side)
    _, cap, ecc_total, blocks, regions, dsize = spec
    n = dsize + 2
    for ri in range(regions):
        for ci in range(regions):
            r0, c0 = ri * n, ci * n
            for k in range(n):
                if not (mat[r0 + n - 1][c0 + k] and mat[r0 + k][c0]):
                    raise DMError("Нарушен L-образный шаблон поиска")
                if mat[r0][c0 + k] != (1 if k % 2 == 0 else 0) or \
                        mat[r0 + k][c0 + n - 1] != (1 if k % 2 == 1 else 0):
                    if not (k == n - 1 and mat[r0 + k][c0 + n - 1]) and not (k == 0 and mat[r0][c0 + k]):
                        raise DMError("Нарушена тактовая линия")
    nd = regions * dsize
    area = [[0] * nd for _ in range(nd)]
    for r in range(nd):
        ri, rr = divmod(r, dsize)
        for c in range(nd):
            ci, cc = divmod(c, dsize)
            area[r][c] = mat[ri * n + 1 + rr][ci * n + 1 + cc]
    cells, _ = placement(nd)
    total = cap + ecc_total
    if len(cells) != total:
        raise DMError("Несовпадение числа кодовых слов")
    stream = []
    for pos in range(total):
        w = 0
        for bit in range(8):
            r, c = cells[pos][bit]
            w = (w << 1) | area[r][c]
        stream.append(w)
    data = stream[:cap]
    expected = _full_stream(data, spec)
    if expected != stream:
        raise DMError("Ошибка контроля Рида-Соломона")
    return decode_codewords(data)


# ---------------------------------------------------------------- растр
def render_image(mat, module_px=8, quiet=2):
    """Матрица -> PIL.Image (L): чёрные модули на белом, с тихой зоной."""
    from PIL import Image
    side = len(mat)
    size = (side + 2 * quiet) * module_px
    img = Image.new("L", (size, size), 255)
    px = img.load()
    for r in range(side):
        for c in range(side):
            if mat[r][c]:
                x0 = (c + quiet) * module_px
                y0 = (r + quiet) * module_px
                for y in range(y0, y0 + module_px):
                    for x in range(x0, x0 + module_px):
                        px[x, y] = 0
    return img


def matrix_candidates(img):
    """Растровое изображение символа -> варианты матриц (размер подбирается по тактовой линии)."""
    from PIL import ImageChops
    g = img.convert("L")
    bbox = ImageChops.invert(g).point(lambda v: 255 if v > 127 else 0).getbbox()
    if not bbox:
        raise DMError("На изображении нет символа")
    x0, y0, x1, y1 = bbox
    px = g.load()
    w = x1 - x0
    h = y1 - y0
    for spec in SIZES:                      # подбираем размер по тактовой линии сверху
        side = spec[0]
        mw, mh = w / side, h / side
        if mw < 1.5:
            continue
        top = [px[min(int(x0 + (c + 0.5) * mw), x1 - 1), min(int(y0 + 0.5 * mh), y1 - 1)] < 128 for c in range(side)]
        left = [px[min(int(x0 + 0.5 * mw), x1 - 1), min(int(y0 + (r + 0.5) * mh), y1 - 1)] < 128 for r in range(side)]
        if all(top[c] == (c % 2 == 0) for c in range(side)) and all(left):
            yield [[1 if px[min(int(x0 + (c + 0.5) * mw), x1 - 1), min(int(y0 + (r + 0.5) * mh), y1 - 1)] < 128 else 0
                    for c in range(side)] for r in range(side)]


def decode_image(img):
    last = DMError("Не удалось определить размер символа по тактовой линии")
    for mat in matrix_candidates(img):
        try:
            return decode_matrix(mat)
        except DMError as e:
            last = e
    raise last


def external_decode(img):
    """Независимое чтение сторонней библиотекой, если она установлена (zxing-cpp).
    Возвращает текст или None, если библиотеки нет."""
    try:
        import zxingcpp
    except Exception:
        return None
    res = zxingcpp.read_barcodes(img)
    if not res:
        raise DMError("zxing-cpp не смог прочитать изображение")
    return res[0].text


if __name__ == "__main__":
    # тест ISO: "123456" -> 142 164 186 | 114 25 5 88 102
    cw = encode_codewords("123456")
    cw = [130 + 12, 130 + 34, 130 + 56]
    print(cw, rs_encode(cw, 5))
