"""
Консольный режим (то же ядро, что в окне программы).

Пример:
  python cli.py --m1 old1.xlsx old2.txt --m2 new.xlsx --out result --filter 4600682 --size 15
"""
import argparse
import os
import sys

import chz_core as cc
import pdf_out as po


def main(argv=None):
    ap = argparse.ArgumentParser(description="Сверка кодов маркировки М1 / М2 и печать М3")
    ap.add_argument("--m1", nargs="+", required=True, help="файлы старой инвентаризации")
    ap.add_argument("--m2", nargs="+", required=True, help="файлы свежей инвентаризации")
    ap.add_argument("--out", default="result", help="папка результата")
    ap.add_argument("--filter", default="", help="часть кода (GTIN/EAN-13/серийный), несколько через пробел")
    ap.add_argument("--sort", default="gtin", choices=["gtin", "serial", "code", "source"])
    ap.add_argument("--size", type=float, default=15.0, help="сторона кода в мм (от 10)")
    ap.add_argument("--gap", type=float, default=4.0, help="расстояние между кодами, мм")
    ap.add_argument("--no-caption", action="store_true", help="без подписи под кодом")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    m1 = cc.build_dataset("М1", a.m1)
    m2 = cc.build_dataset("М2", a.m2)
    m3, both, only_m2 = cc.compare(m1, m2)
    report = cc.make_report(m1, m2, m3, both, only_m2)
    print(report)
    open(os.path.join(a.out, "report.txt"), "w", encoding="utf-8").write(report)
    cc.export_defects([m1, m2], os.path.join(a.out, "brak.xlsx"))

    sel = cc.sort_codes(cc.filter_codes(m3, a.filter), a.sort)
    print("\nК печати/экспорту: %d" % len(sel))
    if not sel:
        return 0
    cc.export_xlsx(sel, os.path.join(a.out, "M3.xlsx"))
    cc.export_txt(sel, os.path.join(a.out, "M3_s_hvostom.txt"), with_tail=True)
    cc.export_txt_by_gtin(sel, os.path.join(a.out, "dlya_CHZ_po_GTIN"))
    bad = po.verify_all(sel)
    if bad:
        print("ВНИМАНИЕ: %d кодов не прошли самопроверку (см. self_check_errors.txt)" % len(bad))
        with open(os.path.join(a.out, "self_check_errors.txt"), "w", encoding="utf-8") as f:
            for c, errs in bad:
                f.write("%s: %s\n" % (c.full, "; ".join(errs)))
        sel = [c for c in sel if c not in {b[0] for b in bad}]
    pages, mm = po.make_pdf(sel, os.path.join(a.out, "M3.pdf"), a.size, a.gap, not a.no_caption,
                            title=a.filter or "M3")
    print("PDF: %d стр., модуль %.2f мм%s" % (pages, mm, "  (МЕЛКО: нужен тест сканером)" if mm < 0.33 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
