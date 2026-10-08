"""
Сверка кодов маркировки «Честный знак»: окно программы (Tkinter, входит в Python).

Порядок работы:
  1. Добавить файлы старой (М1) и свежей (М2) инвентаризации (txt / xlsx / csv).
  2. «Сформировать М3»: очистка от брака и дублей, М3 = коды из М1, которых нет в М2.
  3. Отобрать нужное по части кода (GTIN, EAN-13, кусок серийного номера), выбрать сортировку.
  4. «Создать PDF»: коды с криптохвостом, сетка на A4, сторона кода от 10 мм.
"""
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import chz_core as cc
import datamatrix as dm
import pdf_out as po

SORTS = {"по GTIN, затем серийному": "gtin", "по серийному номеру": "serial",
         "по коду (строка)": "code", "как в файле": "source"}
MAX_ROWS = 5000
MIN_MODULE_MM = 0.33        # мельче - риск, что сканер не прочитает


def open_file(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)          # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Честный знак: сверка инвентаризаций и печать DataMatrix")
        self.geometry("1150x800")
        self.minsize(950, 650)
        self.files = {1: [], 2: []}
        self.m1 = self.m2 = None
        self.m3 = []
        self.view = []
        self.q = queue.Queue()
        self.progress_text = ""
        self.busy = False

        self.status = tk.StringVar(value="Добавьте файлы М1 и М2 и нажмите «Сформировать М3».")
        ttk.Label(self, textvariable=self.status, relief="sunken", anchor="w").pack(fill="x", side="bottom")
        self._build_files_row()
        self._build_action_row()
        self._build_tabs()

    # ------------------------------------------------------------ интерфейс
    def _build_files_row(self):
        row = ttk.Frame(self)
        row.pack(fill="x", padx=8, pady=6)
        self.lists = {}
        for n, title in ((1, "М1 — старая инвентаризация"), (2, "М2 — свежая инвентаризация")):
            fr = ttk.LabelFrame(row, text=title)
            fr.pack(side="left", fill="both", expand=True, padx=4)
            lb = tk.Listbox(fr, height=5, selectmode="extended")
            lb.pack(fill="both", expand=True, padx=4, pady=4)
            self.lists[n] = lb
            bt = ttk.Frame(fr)
            bt.pack(fill="x", padx=4, pady=(0, 4))
            ttk.Button(bt, text="Добавить файлы…", command=lambda n=n: self.add_files(n)).pack(side="left")
            ttk.Button(bt, text="Убрать выбранные", command=lambda n=n: self.remove_files(n)).pack(side="left", padx=4)
            ttk.Button(bt, text="Очистить", command=lambda n=n: self.clear_files(n)).pack(side="left")

    def _build_action_row(self):
        row = ttk.Frame(self)
        row.pack(fill="x", padx=8)
        self.btn_build = ttk.Button(row, text="1. Сформировать М3", command=self.build)
        self.btn_build.pack(side="left")
        self.pb = ttk.Progressbar(row, mode="indeterminate", length=160)
        self.pb.pack(side="left", padx=10)

    def _build_tabs(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=6)
        # отчёт
        t1 = ttk.Frame(nb)
        nb.add(t1, text="Отчёт")
        self.report = tk.Text(t1, wrap="none", font=("Consolas", 10), state="disabled")
        sb = ttk.Scrollbar(t1, command=self.report.yview)
        self.report.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.report.pack(fill="both", expand=True)
        # М3
        t2 = ttk.Frame(nb)
        nb.add(t2, text="М3: отбор и печать")
        ctl = ttk.Frame(t2)
        ctl.pack(fill="x", pady=4)
        ttk.Label(ctl, text="Часть кода (GTIN / EAN-13 / серийный; несколько — через пробел):").pack(side="left")
        self.flt = tk.StringVar()
        e = ttk.Entry(ctl, textvariable=self.flt, width=28)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda _e: self.apply_filter())
        ttk.Label(ctl, text="Сортировка:").pack(side="left", padx=(10, 2))
        self.sort = tk.StringVar(value=list(SORTS)[0])
        cb = ttk.Combobox(ctl, textvariable=self.sort, values=list(SORTS), state="readonly", width=26)
        cb.pack(side="left")
        cb.bind("<<ComboboxSelected>>", lambda _e: self.apply_filter())
        ttk.Button(ctl, text="Применить", command=self.apply_filter).pack(side="left", padx=6)
        self.count_lbl = ttk.Label(ctl, text="")
        self.count_lbl.pack(side="left", padx=6)

        cols = ("n", "gtin", "ean", "serial", "kind")
        self.tree = ttk.Treeview(t2, columns=cols, show="headings", height=12, selectmode="browse")
        for c, h, w in zip(cols, ("№", "GTIN", "EAN-13", "Серийный номер", "Формат"), (60, 130, 130, 220, 140)):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=w, anchor="w")
        sb2 = ttk.Scrollbar(t2, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb2.set)
        sb2.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)

        pr = ttk.LabelFrame(self, text="Печать и экспорт отобранных кодов")
        pr.pack(fill="x", padx=8, pady=(0, 6))
        r1 = ttk.Frame(pr)
        r1.pack(fill="x", padx=6, pady=4)
        self.size = tk.StringVar(value="15")
        self.gap = tk.StringVar(value="4")
        self.cap = tk.BooleanVar(value=True)
        ttk.Label(r1, text="Сторона кода, мм (от 10):").pack(side="left")
        ttk.Spinbox(r1, from_=8, to=60, increment=1, textvariable=self.size, width=5,
                    command=self.update_hint).pack(side="left", padx=4)
        ttk.Label(r1, text="Промежуток, мм:").pack(side="left", padx=(10, 0))
        ttk.Spinbox(r1, from_=1, to=20, increment=1, textvariable=self.gap, width=5).pack(side="left", padx=4)
        ttk.Checkbutton(r1, text="Подпись под кодом (EAN-13 + серийный)", variable=self.cap).pack(side="left", padx=10)
        self.size.trace_add("write", lambda *_: self.update_hint())
        self.hint = ttk.Label(r1, text="", foreground="#555")
        self.hint.pack(side="left", padx=8)
        r2 = ttk.Frame(pr)
        r2.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(r2, text="2. Создать PDF", command=self.make_pdf).pack(side="left")
        ttk.Button(r2, text="Тестовый лист размеров", command=self.size_test).pack(side="left", padx=6)
        ttk.Button(r2, text="Экспорт XLSX", command=self.export_xlsx).pack(side="left", padx=(20, 0))
        ttk.Button(r2, text="Экспорт для ЧЗ (без криптохвоста)", command=self.export_chz).pack(side="left", padx=6)
        ttk.Button(r2, text="Экспорт брака", command=self.export_defects).pack(side="left")
        # брак
        t3 = ttk.Frame(nb)
        nb.add(t3, text="Брак и поправки")
        dc = ("set", "file", "where", "type", "reason", "raw")
        self.dtree = ttk.Treeview(t3, columns=dc, show="headings")
        for c, h, w in zip(dc, ("Набор", "Файл", "Где", "Тип", "Причина", "Фрагмент"), (50, 140, 110, 80, 330, 330)):
            self.dtree.heading(c, text=h)
            self.dtree.column(c, width=w, anchor="w")
        sb3 = ttk.Scrollbar(t3, command=self.dtree.yview)
        self.dtree.configure(yscrollcommand=sb3.set)
        sb3.pack(side="right", fill="y")
        self.dtree.pack(fill="both", expand=True)

    # ------------------------------------------------------------ файлы
    def add_files(self, n):
        paths = filedialog.askopenfilenames(
            title="Файлы с кодами", filetypes=[("Файлы кодов", "*.txt *.xlsx *.xlsm *.csv"), ("Все файлы", "*.*")])
        for p in paths:
            if p not in self.files[n]:
                self.files[n].append(p)
                self.lists[n].insert("end", p)

    def remove_files(self, n):
        for i in reversed(self.lists[n].curselection()):
            self.lists[n].delete(i)
            del self.files[n][i]

    def clear_files(self, n):
        self.lists[n].delete(0, "end")
        self.files[n].clear()

    # ------------------------------------------------------------ фоновые задачи
    def run_bg(self, func, done):
        if self.busy:
            return
        self.busy = True
        self.pb.start(12)
        self.btn_build.state(["disabled"])

        def worker():
            try:
                self.q.put(("ok", func()))
            except Exception:
                self.q.put(("err", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        self.after(120, lambda: self._poll(done))

    def _poll(self, done):
        self.status.set(self.progress_text or "Работаю…")
        try:
            kind, res = self.q.get_nowait()
        except queue.Empty:
            self.after(120, lambda: self._poll(done))
            return
        self.busy = False
        self.pb.stop()
        self.btn_build.state(["!disabled"])
        if kind == "err":
            self.status.set("Ошибка")
            messagebox.showerror("Ошибка", res[-1500:])
        else:
            done(res)

    def _progress(self, text):
        self.progress_text = text

    # ------------------------------------------------------------ М3
    def build(self):
        if not self.files[1] or not self.files[2]:
            messagebox.showwarning("Нет файлов", "Добавьте хотя бы по одному файлу в М1 и М2.")
            return
        f1, f2 = list(self.files[1]), list(self.files[2])

        def work():
            m1 = cc.build_dataset("М1", f1, self._progress)
            m2 = cc.build_dataset("М2", f2, self._progress)
            m3, both, only_m2 = cc.compare(m1, m2)
            return m1, m2, m3, both, only_m2

        def done(res):
            self.m1, self.m2, self.m3, both, only_m2 = res
            self._set_report(cc.make_report(self.m1, self.m2, self.m3, both, only_m2))
            self._fill_defects()
            self.apply_filter()
            self.status.set("Готово: в М3 %d кодов. Отберите нужные и создайте PDF." % len(self.m3))

        self.progress_text = "Читаю файлы…"
        self.run_bg(work, done)

    def _set_report(self, text):
        self.report.configure(state="normal")
        self.report.delete("1.0", "end")
        self.report.insert("1.0", text)
        self.report.configure(state="disabled")

    def _fill_defects(self):
        self.dtree.delete(*self.dtree.get_children())
        for ds in (self.m1, self.m2):
            for d in ds.defects[:5000]:
                self.dtree.insert("", "end", values=(ds.name, d.source, d.where,
                                                     "брак" if d.fatal else "поправка", d.reason, d.raw[:120]))

    def apply_filter(self):
        self.view = cc.sort_codes(cc.filter_codes(self.m3, self.flt.get()), SORTS[self.sort.get()])
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.view[:MAX_ROWS], 1):
            self.tree.insert("", "end", values=(i, c.gtin, c.gtin[1:], c.serial, c.kind))
        extra = " (в таблице первые %d)" % MAX_ROWS if len(self.view) > MAX_ROWS else ""
        self.count_lbl.configure(text="Отобрано: %d из %d%s" % (len(self.view), len(self.m3), extra))
        self.update_hint()

    # ------------------------------------------------------------ печать
    def _num(self, var, default):
        try:
            return float(var.get().replace(",", "."))
        except ValueError:
            return default

    def update_hint(self):
        if not self.view:
            self.hint.configure(text="")
            return
        size = self._num(self.size, 15)
        try:
            side = len(dm.make_matrix(self.view[0].gs_text))
        except dm.DMError:
            return
        mod = size / side
        warn = "  — МЕЛКО, проверьте сканером" if mod < MIN_MODULE_MM else ""
        self.hint.configure(text="символ %dx%d, модуль %.2f мм%s" % (side, side, mod, warn),
                            foreground="#b00020" if warn else "#555")

    def _print_params(self):
        size = self._num(self.size, 15)
        gap = self._num(self.gap, 4)
        if size < 5 or size > 100:
            messagebox.showwarning("Размер", "Сторона кода должна быть от 5 до 100 мм (рекомендуется от 10).")
            return None
        return size, gap, self.cap.get()

    def make_pdf(self):
        if not self.view:
            messagebox.showinfo("Нет кодов", "Сначала сформируйте М3 и отберите коды.")
            return
        params = self._print_params()
        if not params:
            return
        size, gap, cap = params
        path = filedialog.asksaveasfilename(title="Сохранить PDF", defaultextension=".pdf",
                                            filetypes=[("PDF", "*.pdf")], initialfile="M3_codes.pdf")
        if not path:
            return
        codes = list(self.view)
        title = self.flt.get().strip() or "M3"

        def work():
            bad = po.verify_all(codes, self._progress)
            bad_set = {b[0] for b in bad}
            good = [c for c in codes if c not in bad_set]
            pages, mod = po.make_pdf(good, path, size, gap, cap, title, self._progress) if good else (0, None)
            return bad, len(good), pages, mod

        def done(res):
            bad, n_good, pages, mod = res
            msg = "PDF сохранён: %s\nКодов: %d, страниц: %d." % (path, n_good, pages)
            if mod:
                msg += "\nМодуль (самая мелкая клетка): %.2f мм." % mod
                if mod < MIN_MODULE_MM:
                    msg += "\nМЕЛКО: увеличьте размер кода или проверьте печать сканером."
            msg += "\nПечатайте в масштабе 100% («Реальный размер»), не «По размеру страницы»."
            if bad:
                log = os.path.splitext(path)[0] + "_ошибки_самопроверки.txt"
                with open(log, "w", encoding="utf-8") as f:
                    for c, errs in bad:
                        f.write("%s: %s\n" % (c.full, "; ".join(errs)))
                msg += "\n\nНе прошли самопроверку и НЕ попали в PDF: %d (список: %s)" % (len(bad), log)
            self.status.set("PDF готов")
            messagebox.showinfo("Готово", msg)
            if n_good:
                open_file(path)

        self.progress_text = "Проверяю коды…"
        self.run_bg(work, done)

    def size_test(self):
        if not self.view:
            messagebox.showinfo("Нет кодов", "Сначала сформируйте М3 и отберите коды.")
            return
        sel = self.tree.selection()
        code = self.view[int(self.tree.item(sel[0])["values"][0]) - 1] if sel else self.view[0]
        path = filedialog.asksaveasfilename(title="Сохранить тестовый лист", defaultextension=".pdf",
                                            filetypes=[("PDF", "*.pdf")], initialfile="test_sizes.pdf")
        if not path:
            return
        try:
            po.make_size_test_pdf(code, path)
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))
            return
        messagebox.showinfo("Готово", "Тестовый лист: один код (выбранный в таблице или первый) размерами 10–25 мм.\n"
                                      "Напечатайте в масштабе 100% и проверьте сканером/кассой, "
                                      "какой минимальный размер читается надёжно.")
        open_file(path)

    # ------------------------------------------------------------ экспорт
    def export_xlsx(self):
        if not self.view:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                            initialfile="M3.xlsx")
        if path:
            cc.export_xlsx(self.view, path)
            messagebox.showinfo("Готово", "Сохранено: %s" % path)

    def export_chz(self):
        if not self.view:
            return
        folder = filedialog.askdirectory(title="Папка для файлов ЧЗ")
        if not folder:
            return
        cc.export_txt(self.view, os.path.join(folder, "M3_bez_kriptohvosta.txt"), with_tail=False)
        n = cc.export_txt_by_gtin(self.view, os.path.join(folder, "po_GTIN"))
        messagebox.showinfo("Готово", "Общий файл и %d файлов по GTIN сохранены в:\n%s" % (n, folder))

    def export_defects(self):
        if not self.m1:
            return
        path = filedialog.asksaveasfilename(defaultextension=".xlsx", filetypes=[("Excel", "*.xlsx")],
                                            initialfile="brak.xlsx")
        if path:
            cc.export_defects([self.m1, self.m2], path)
            messagebox.showinfo("Готово", "Сохранено: %s" % path)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
