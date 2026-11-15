import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SRC_OPTIONS = [
    ("Автовизначення (auto)", "auto"),
    ("Українська (uk)", "uk"),
    ("Англійська (en)", "en"),
]

DST_OPTIONS = [
    ("Англійська (en)", "en"),
    ("Німецька (de)", "de"),
    ("Французька (fr)", "fr"),
    ("Іспанська (es)", "es"),
    ("Італійська (it)", "it"),
    ("Португальська (pt)", "pt"),
    ("Китайська (zh)", "zh"),
    ("Японська (ja)", "ja"),
    ("Корейська (ko)", "ko"),
]

SYNTH_OPTIONS = [
    "= як мова перекладу",
    "English",
    "German",
    "French",
    "Spanish",
    "Italian",
    "Portuguese",
    "Chinese",
    "Japanese",
    "Korean",
]

STAGE_DEFS = [
    ("1. Аудіо", "Витягнення доріжки"),
    ("2. Розпізнавання", "Whisper + паузи"),
    ("3. Переклад", "Gemma під таймінг"),
    ("4. Озвучення", "Клон голосу Qwen3-TTS"),
    ("5. Склейка", "Підгонка часу + MP4"),
]

BG_MAIN = "#0F172A"
BG_CARD = "#1E293B"
BG_CARD_ALT = "#334155"
BG_INPUT = "#0B1120"
BORDER_CLR = "#334155"
ACCENT_BLUE = "#38BDF8"
ACCENT_INDIGO = "#6366F1"
ACCENT_GREEN = "#10B981"
TEXT_PRIMARY = "#F8FAFC"
TEXT_SECONDARY = "#94A3B8"
TEXT_MUTED = "#64748B"


class DubGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("SkyViDub — Автоматичний дубляж відео")
        root.geometry("1120x720")
        root.minsize(1000, 660)
        root.configure(bg=BG_MAIN)

        self.job_q = queue.Queue()
        self.result_path = None

        import settings
        settings.init_settings()
        preset_names = [r[0] for r in settings.list_base_voices()]
        self.voice_options = [
            ("orig — Голос диктора з відео", "orig"),
        ] + [(f"{name} (пресет)", name) for name in preset_names]

        self._setup_styles()
        self._build_header()
        self._build_pipeline_strip()
        self._build_main_body()

        self.root.after(200, self.poll_log)

    def _setup_styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure(".", background=BG_MAIN, foreground=TEXT_PRIMARY, font=("Segoe UI", 9))
        style.configure("TFrame", background=BG_MAIN)
        style.configure("TLabel", background=BG_CARD, foreground=TEXT_PRIMARY, font=("Segoe UI", 9))

        style.configure(
            "TCombobox",
            fieldbackground=BG_INPUT,
            background=BG_CARD_ALT,
            foreground=TEXT_PRIMARY,
            arrowcolor=ACCENT_BLUE,
            bordercolor=BORDER_CLR,
            lightcolor=BORDER_CLR,
            darkcolor=BORDER_CLR,
            padding=5,
        )
        style.map(
            "TCombobox",
            fieldbackground=[("readonly", BG_INPUT)],
            foreground=[("readonly", TEXT_PRIMARY)],
            selectbackground=[("readonly", BG_INPUT)],
            selectforeground=[("readonly", ACCENT_BLUE)],
        )

        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=BG_INPUT,
            background=ACCENT_BLUE,
            bordercolor=BORDER_CLR,
            lightcolor=ACCENT_BLUE,
            darkcolor=ACCENT_INDIGO,
            thickness=12,
        )

        style.configure(
            "Horizontal.TScale",
            background=BG_CARD,
            troughcolor=BG_INPUT,
            sliderThickness=14,
        )

    def _build_header(self):
        hdr = tk.Frame(self.root, bg="#0B1120", padx=20, pady=12, highlightthickness=1, highlightbackground="#1E293B")
        hdr.pack(fill="x")

        left = tk.Frame(hdr, bg="#0B1120")
        left.pack(side="left")

        title_row = tk.Frame(left, bg="#0B1120")
        title_row.pack(anchor="w")

        tk.Label(
            title_row, text="SkyViDub", bg="#0B1120", fg=ACCENT_BLUE,
            font=("Segoe UI", 15, "bold")
        ).pack(side="left", padx=(0, 12))

        tk.Label(
            title_row,
            text="Дубляж відео іншою мовою зі збереженням голосу диктора",
            bg="#0B1120", fg=TEXT_PRIMARY, font=("Segoe UI", 11)
        ).pack(side="left", pady=(2, 0))

    def _build_pipeline_strip(self):
        strip_wrap = tk.Frame(self.root, bg=BG_MAIN, padx=20, pady=10)
        strip_wrap.pack(fill="x")

        self.stage_cards = []
        for idx, (st_title, st_sub) in enumerate(STAGE_DEFS):
            card = tk.Frame(
                strip_wrap, bg=BG_CARD, padx=12, pady=7,
                highlightthickness=1, highlightbackground=BORDER_CLR
            )
            card.grid(row=0, column=idx, sticky="nsew", padx=(0 if idx == 0 else 8, 0))
            strip_wrap.columnconfigure(idx, weight=1, uniform="stage")

            top_r = tk.Frame(card, bg=BG_CARD)
            top_r.pack(fill="x")

            lbl_title = tk.Label(top_r, text=st_title, bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9, "bold"))
            lbl_title.pack(side="left")

            lbl_state = tk.Label(top_r, text="Очікування", bg=BG_CARD, fg=TEXT_MUTED, font=("Segoe UI", 8))
            lbl_state.pack(side="right")

            lbl_sub = tk.Label(card, text=st_sub, bg=BG_CARD, fg=TEXT_SECONDARY, font=("Segoe UI", 8))
            lbl_sub.pack(anchor="w", pady=(2, 0))

            self.stage_cards.append((card, top_r, lbl_title, lbl_state, lbl_sub))

    def _set_pipeline_stage(self, active_stage: int, done_all: bool = False):
        for idx, (card, top_r, lbl_title, lbl_state, lbl_sub) in enumerate(self.stage_cards, 1):
            if done_all or idx < active_stage:
                bg = "#063628"
                bdr = ACCENT_GREEN
                st_txt = "✓ Готово"
                st_fg = "#34D399"
            elif idx == active_stage:
                bg = "#0F2942"
                bdr = ACCENT_BLUE
                st_txt = "● У роботі"
                st_fg = ACCENT_BLUE
            else:
                bg = BG_CARD
                bdr = BORDER_CLR
                st_txt = "Очікування"
                st_fg = TEXT_MUTED

            card.configure(bg=bg, highlightbackground=bdr)
            top_r.configure(bg=bg)
            lbl_title.configure(bg=bg)
            lbl_state.configure(bg=bg, text=st_txt, fg=st_fg)
            lbl_sub.configure(bg=bg)

    def _build_main_body(self):
        body = tk.Frame(self.root, bg=BG_MAIN, padx=20, pady=4)
        body.pack(fill="both", expand=True, pady=(0, 14))
        body.columnconfigure(0, weight=45, uniform="cols")
        body.columnconfigure(1, weight=55, uniform="cols")
        body.rowconfigure(0, weight=1)

        left_col = tk.Frame(
            body, bg=BG_CARD, padx=16, pady=14,
            highlightthickness=1, highlightbackground=BORDER_CLR
        )
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_col.columnconfigure(1, weight=1)

        tk.Label(
            left_col, text="Налаштування дубляжу",
            bg=BG_CARD, fg=ACCENT_BLUE, font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

        tk.Label(left_col, text="Відеофайл:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=1, column=0, sticky="w", pady=5
        )
        self.video_var = tk.StringVar()
        tk.Entry(
            left_col, textvariable=self.video_var, bg=BG_INPUT, fg=TEXT_PRIMARY, width=26,
            insertbackground=TEXT_PRIMARY, relief="flat", font=("Segoe UI", 9),
            highlightthickness=1, highlightbackground=BORDER_CLR, highlightcolor=ACCENT_BLUE
        ).grid(row=1, column=1, sticky="ew", padx=6, pady=5, ipady=4)
        tk.Button(
            left_col, text="Обрати...", command=self.pick_video,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, activebackground=ACCENT_INDIGO, activeforeground="#FFF",
            relief="flat", font=("Segoe UI", 8, "bold"), padx=10, pady=4, cursor="hand2"
        ).grid(row=1, column=2, pady=5, sticky="ew")

        tk.Label(left_col, text="Зберегти як:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=2, column=0, sticky="w", pady=5
        )
        self.out_var = tk.StringVar()
        tk.Entry(
            left_col, textvariable=self.out_var, bg=BG_INPUT, fg=TEXT_PRIMARY, width=26,
            insertbackground=TEXT_PRIMARY, relief="flat", font=("Segoe UI", 9),
            highlightthickness=1, highlightbackground=BORDER_CLR, highlightcolor=ACCENT_BLUE
        ).grid(row=2, column=1, sticky="ew", padx=6, pady=5, ipady=4)
        tk.Button(
            left_col, text="Змінити...", command=self.pick_out,
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, activebackground=ACCENT_INDIGO, activeforeground="#FFF",
            relief="flat", font=("Segoe UI", 8, "bold"), padx=10, pady=4, cursor="hand2"
        ).grid(row=2, column=2, pady=5, sticky="ew")

        tk.Frame(left_col, bg=BORDER_CLR, height=1).grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)

        tk.Label(left_col, text="Мова оригіналу:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=4, column=0, sticky="w", pady=5
        )
        self.src_display_var = tk.StringVar(value=SRC_OPTIONS[1][0])
        ttk.Combobox(
            left_col, textvariable=self.src_display_var,
            values=[x[0] for x in SRC_OPTIONS], state="readonly"
        ).grid(row=4, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=5)

        tk.Label(left_col, text="Перекласти на:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=5, column=0, sticky="w", pady=5
        )
        self.dst_display_var = tk.StringVar(value=DST_OPTIONS[0][0])
        ttk.Combobox(
            left_col, textvariable=self.dst_display_var,
            values=[x[0] for x in DST_OPTIONS], state="readonly"
        ).grid(row=5, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=5)

        tk.Label(left_col, text="Голос озвучення:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=6, column=0, sticky="w", pady=5
        )
        self.voice_display_var = tk.StringVar(value=self.voice_options[0][0])
        ttk.Combobox(
            left_col, textvariable=self.voice_display_var,
            values=[x[0] for x in self.voice_options], state="readonly"
        ).grid(row=6, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=5)

        tk.Label(left_col, text="Мова синтезу:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=7, column=0, sticky="w", pady=5
        )
        self.slang_var = tk.StringVar(value=SYNTH_OPTIONS[0])
        ttk.Combobox(
            left_col, textvariable=self.slang_var,
            values=SYNTH_OPTIONS, state="readonly"
        ).grid(row=7, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=5)

        tk.Label(left_col, text="Фон оригіналу:", bg=BG_CARD, fg=TEXT_PRIMARY, font=("Segoe UI", 9)).grid(
            row=8, column=0, sticky="w", pady=5
        )
        self.bg_var = tk.DoubleVar(value=0.12)
        scale_frame = tk.Frame(left_col, bg=BG_CARD)
        scale_frame.grid(row=8, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=5)
        ttk.Scale(scale_frame, from_=0.0, to=0.5, variable=self.bg_var, length=160).pack(side="left", fill="x", expand=True)
        self.bg_lbl = tk.Label(
            scale_frame, text="0.12", bg=BG_INPUT, fg=ACCENT_BLUE,
            font=("Consolas", 9, "bold"), padx=8, pady=2
        )
        self.bg_lbl.pack(side="right", padx=(8, 0))
        self.bg_var.trace_add("write", lambda *a: self.bg_lbl.config(text=f"{self.bg_var.get():.2f}"))

        tk.Frame(left_col, bg=BORDER_CLR, height=1).grid(row=9, column=0, columnspan=3, sticky="ew", pady=10)

        tk.Label(
            left_col, text="Додаткові опції обробки",
            bg=BG_CARD, fg=ACCENT_BLUE, font=("Segoe UI", 10, "bold")
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(0, 6))

        self.semantic_var = tk.BooleanVar(value=True)
        self._make_checkbox(
            left_col,
            "Об'єднувати дрібні фрази за змістом (до кінця речення)",
            self.semantic_var
        ).grid(row=11, column=0, columnspan=3, sticky="w", pady=3)

        self.resynth_var = tk.BooleanVar(value=True)
        self._make_checkbox(
            left_col,
            "Перегенерувати фрази, що не влізли у таймінг (скорочення через Gemma)",
            self.resynth_var
        ).grid(row=12, column=0, columnspan=3, sticky="w", pady=3)

        self.music_var = tk.BooleanVar(value=False)
        self._make_checkbox(
            left_col,
            "Зберегти музику та звукові ефекти з оригіналу (Demucs)",
            self.music_var
        ).grid(row=13, column=0, columnspan=3, sticky="w", pady=3)

        btn_row = tk.Frame(left_col, bg=BG_CARD)
        btn_row.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(16, 0))
        btn_row.columnconfigure(0, weight=58, uniform="btns")
        btn_row.columnconfigure(1, weight=42, uniform="btns")

        self.start_btn = tk.Button(
            btn_row, text="▶ Дублювати відео",
            command=self.start, bg=ACCENT_INDIGO, fg="#FFFFFF",
            activebackground="#4F46E5", activeforeground="#FFFFFF",
            relief="flat", font=("Segoe UI", 10, "bold"), pady=9, cursor="hand2"
        )
        self.start_btn.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.open_btn = tk.Button(
            btn_row, text="Відкрити результат",
            command=self.open_result, state="disabled",
            bg=BG_CARD_ALT, fg=TEXT_PRIMARY, disabledforeground=TEXT_MUTED,
            activebackground=ACCENT_GREEN, activeforeground="#FFFFFF",
            relief="flat", font=("Segoe UI", 9, "bold"), pady=9, cursor="hand2"
        )
        self.open_btn.grid(row=0, column=1, sticky="ew")

        right_col = tk.Frame(
            body, bg=BG_CARD, padx=16, pady=14,
            highlightthickness=1, highlightbackground=BORDER_CLR
        )
        right_col.grid(row=0, column=1, sticky="nsew")
        right_col.columnconfigure(0, weight=1)
        right_col.rowconfigure(2, weight=1)

        tk.Label(
            right_col, text="Процес виконання та лог",
            bg=BG_CARD, fg=ACCENT_BLUE, font=("Segoe UI", 11, "bold")
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        prog_box = tk.Frame(right_col, bg=BG_INPUT, padx=12, pady=10, highlightthickness=1, highlightbackground=BORDER_CLR)
        prog_box.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        prog_box.columnconfigure(0, weight=1)

        prog_top = tk.Frame(prog_box, bg=BG_INPUT)
        prog_top.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        self.status_lbl = tk.Label(
            prog_top, text="Оберіть відеофайл і натисніть «Дублювати відео»",
            bg=BG_INPUT, fg=TEXT_PRIMARY, font=("Segoe UI", 9, "bold")
        )
        self.status_lbl.pack(side="left")

        self.pct_lbl = tk.Label(
            prog_top, text="0%", bg=BG_INPUT, fg=ACCENT_BLUE, font=("Consolas", 10, "bold")
        )
        self.pct_lbl.pack(side="right")

        self.prog_var = tk.DoubleVar(value=0.0)
        self.pbar = ttk.Progressbar(prog_box, variable=self.prog_var, maximum=100.0, style="Horizontal.TProgressbar")
        self.pbar.grid(row=1, column=0, sticky="ew")

        log_wrap = tk.Frame(right_col, bg=BG_INPUT, highlightthickness=1, highlightbackground=BORDER_CLR)
        log_wrap.grid(row=2, column=0, sticky="nsew")
        log_wrap.rowconfigure(0, weight=1)
        log_wrap.columnconfigure(0, weight=1)

        self.log = tk.Text(
            log_wrap, bg="#080D1A", fg="#E2E8F0",
            font=("Consolas", 9), relief="flat", padx=12, pady=10,
            state="disabled", wrap="word"
        )
        self.log.grid(row=0, column=0, sticky="nsew")

        sb = tk.Scrollbar(log_wrap, command=self.log.yview, bg=BG_CARD, troughcolor="#080D1A", relief="flat")
        sb.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=sb.set)

        self.log.tag_configure("ts", foreground="#64748B")
        self.log.tag_configure("stage", foreground="#38BDF8", font=("Consolas", 9, "bold"))
        self.log.tag_configure("seg", foreground="#A5B4FC")
        self.log.tag_configure("ok", foreground="#34D399", font=("Consolas", 9, "bold"))
        self.log.tag_configure("warn", foreground="#FBBF24")
        self.log.tag_configure("err", foreground="#F87171", font=("Consolas", 9, "bold"))

    def _make_checkbox(self, parent, text, var):
        return tk.Checkbutton(
            parent, text=text, variable=var,
            bg=BG_CARD, fg=TEXT_PRIMARY, selectcolor=BG_INPUT,
            activebackground=BG_CARD, activeforeground=ACCENT_BLUE,
            font=("Segoe UI", 9), anchor="w", justify="left", cursor="hand2"
        )

    def _resolve_code(self, display_val: str, pairs: list[tuple[str, str]], default: str) -> str:
        for label, code in pairs:
            if label == display_val:
                return code
        return default

    def pick_video(self):
        p = filedialog.askopenfilename(
            title="Оберіть відеофайл",
            filetypes=[("Відео", "*.mp4 *.mkv *.avi *.mov *.webm"), ("Усі файли", "*.*")]
        )
        if p:
            self.video_var.set(p)
            if not self.out_var.get():
                base, _ = os.path.splitext(p)
                self.out_var.set(base + "_dubbed.mp4")

    def pick_out(self):
        p = filedialog.asksaveasfilename(
            title="Зберегти як",
            defaultextension=".mp4",
            filetypes=[("MP4", "*.mp4")]
        )
        if p:
            self.out_var.set(p)

    def append_log(self, msg: str):
        ts = time.strftime("%H:%M:%S")
        self.log.config(state="normal")
        self.log.insert("end", f"[{ts}] ", "ts")

        tag = None
        if "ГОТОВО" in msg or "Готово!" in msg or "100%" in msg:
            tag = "ok"
        elif any(k in msg for k in ("1/5", "2/5", "2.5/5", "3/5", "4/5", "5/5", "[gui]")):
            tag = "stage"
        elif msg.lstrip().startswith("[0") or msg.lstrip().startswith("[1") or "Озвучено" in msg or "Синтезовано" in msg:
            tag = "seg"
        elif "вийшла довшою" in msg or "перевищення" in msg or "Прискорено" in msg:
            tag = "warn"
        elif "ПОМИЛКА" in msg or "Помилка" in msg:
            tag = "err"

        if tag:
            self.log.insert("end", str(msg) + "\n", tag)
        else:
            self.log.insert("end", str(msg) + "\n")

        self.log.see("end")
        self.log.config(state="disabled")

    def poll_log(self):
        try:
            while True:
                kind, payload = self.job_q.get_nowait()
                if kind == "log":
                    self.append_log(payload)
                elif kind == "stage":
                    st_idx, pct, st_txt = payload
                    self.prog_var.set(pct)
                    self.pct_lbl.config(text=f"{int(pct)}%")
                    self.status_lbl.config(text=st_txt)
                    self._set_pipeline_stage(st_idx, done_all=(pct >= 100))
                elif kind == "done":
                    self.result_path = payload
                    self.start_btn.config(state="normal")
                    self.open_btn.config(
                        state="normal" if payload else "disabled",
                        bg=ACCENT_GREEN if payload else BG_CARD_ALT
                    )
                    if payload:
                        self._set_pipeline_stage(5, done_all=True)
                        self.prog_var.set(100.0)
                        self.pct_lbl.config(text="100%")
                        self.status_lbl.config(text=f"Готово: {os.path.basename(payload)}")
                    else:
                        self.status_lbl.config(text="Зупинено — перевірте лог")
        except queue.Empty:
            pass
        self.root.after(150, self.poll_log)

    def open_result(self):
        if self.result_path and os.path.exists(self.result_path):
            os.startfile(self.result_path)

    def start(self):
        video = self.video_var.get().strip()
        if not video or not os.path.exists(video):
            messagebox.showwarning("SkyViDub", "Оберіть відеофайл")
            return

        src_code = self._resolve_code(self.src_display_var.get(), SRC_OPTIONS, "auto")
        dst_code = self._resolve_code(self.dst_display_var.get(), DST_OPTIONS, "en")
        voice_code = self._resolve_code(self.voice_display_var.get(), self.voice_options, "orig")
        slang = None if self.slang_var.get().startswith("=") else self.slang_var.get()

        job = {
            "input": video,
            "src": src_code,
            "dst": dst_code,
            "voice": voice_code,
            "out": self.out_var.get().strip() or None,
            "bg": round(self.bg_var.get(), 2),
            "lang": slang,
            "resynth": bool(self.resynth_var.get()),
            "music": bool(self.music_var.get()),
            "semantic": bool(self.semantic_var.get()),
        }
        self.start_btn.config(state="disabled")
        self.open_btn.config(state="disabled", bg=BG_CARD_ALT)
        self.result_path = None
        q = self.job_q

        def worker():
            try:
                import cli
                q.put(("log", f"[gui] Старт: {os.path.basename(video)} ({src_code} -> {dst_code}), голос: {voice_code}"))
                res = cli.run_job(
                    job,
                    log=lambda m: q.put(("log", m)),
                    progress_cb=lambda st, pct, txt: q.put(("stage", (st, pct, txt))),
                )
                q.put(("done", res))
            except Exception as e:
                import traceback
                q.put(("log", f"[gui] ПОМИЛКА: {type(e).__name__}: {e}"))
                q.put(("log", traceback.format_exc(limit=3)))
                q.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    DubGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
