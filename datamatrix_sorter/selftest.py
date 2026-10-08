"""Быстрая проверка ядра: python selftest.py"""
import datamatrix as dm
import chz_core as cc
import pdf_out as po

assert dm.rs_encode([142, 164, 186], 5) == [114, 25, 5, 88, 102], "Рид-Соломон"
for spec in dm.SIZES:                         # размещение занимает каждую ячейку ровно один раз
    nd = spec[4] * spec[5]
    cells, fill = dm.placement(nd)
    used = {rc for p in cells.values() for rc in p}
    assert len(used) == (spec[1] + spec[2]) * 8 and len(used) + (4 if fill else 0) == nd * nd

short = "0104600682054627215.1m)NY934p9o"
c, issues = cc.parse_cell(short)
assert len(c) == 1 and not issues and c[0].full == short
c, issues = cc.parse_cell(short + short)                      # два кода слиплись
assert len(c) == 2
c, issues = cc.parse_cell("4600682054627")                    # штрихкод - брак
assert not c and issues
chips = '"0104607087520528215JB""sX8""5dgGq91EE1292Y+ZYOkR+gA342N2ciQMFvxt48jtXjB9cyNFJJS8T+ZM="'
c, issues = cc.parse_cell(chips)                              # CSV-кавычки, длинный код
assert len(c) == 1 and c[0].kind.startswith("длинный") and c[0].serial == '5JB"sX8"5dgGq'
for code in (c[0], cc.parse_cell(short)[0][0]):
    mat, errs = po.verify_code(code, deep=True)
    assert not errs, errs
    assert dm.decode_matrix(mat)[0] == code.gs_text
print("OK: все проверки пройдены")
