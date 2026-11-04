"""
LAS点群処理ツール GUI（tkinter）
--------------------------------
LASファイルから 展開図 / 平面図 / 縦断図 / 横断図 を DXF・PNG で出力する。
パラメータは config.json と連動。重い処理は別スレッドで実行しUIは固まらない。

起動：
    python gui_app.py
"""

import os
import re
import json
import math
import queue
import shutil
import ctypes
import zipfile
import datetime
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import numpy as np
from PIL import Image, ImageTk

import las_processor as lp
from config import Config
import viewer_3d

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = str(BASE_DIR / "config.json")
WORKS_DIR = BASE_DIR / "works"                 # 現場ごとの作成図面（メモリ）
PROJECTS_JSON = WORKS_DIR / "projects.json"    # 現場の索引


def _sanitize(name: str) -> str:
    """現場名をフォルダ名に使える形へ。"""
    name = (name or "未分類").strip() or "未分類"
    return re.sub(r'[\\/:*?"<>|]+', "_", name)


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("LAS点群処理ツール")
        root.geometry("980x720")

        self.cfg = Config.load(CONFIG_PATH)
        self.msg_q = queue.Queue()
        self._tkimg = None       # プレビュー画像の参照保持
        self._generated = []     # 直近の実行で作成した図面 [{title,dxf,png,meta}]
        self._preview_paths = {} # title → png_path（プレビュー切替用）
        self._preview_metas = {} # title → meta dict（計測スケール）
        self._current_meta = None
        self._workdir = None     # 直近の作業フォルダ
        self._las_paths: list = []  # 選択された全ファイルパス（複数結合対応）
        # 2D計測
        self._measure_mode = False
        self._measure_pts_canvas = []  # [(cx, cy), ...]
        self._measure_pts_world = []   # [(wx, wy), ...] 実寸(m)
        self._measure_items = []       # canvas item ID

        self._build_ui()
        self._poll_queue()

    # ---------------- 現場メモリ ----------------
    def _load_projects(self) -> dict:
        try:
            with open(PROJECTS_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_projects(self, data: dict):
        WORKS_DIR.mkdir(parents=True, exist_ok=True)
        with open(PROJECTS_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _record_project(self, genba: str, workdir: str, drawings: list):
        data = self._load_projects()
        data[genba] = {
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
            "dir": workdir,
            "drawings": [g["title"] for g in drawings],
        }
        self._save_projects(data)
        if hasattr(self, "cmb_project"):
            self.cmb_project["values"] = sorted(data.keys())

    # ---------------- UI構築 ----------------
    def _build_ui(self):
        self.root.minsize(820, 480)
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill="both", expand=True)

        # 左パネルはスクロール可能にする（ウィンドウが低くても全項目に届く）
        left_container = ttk.Frame(main)
        left_container.pack(side="left", fill="y")
        canvas = tk.Canvas(left_container, width=330, highlightthickness=0,
                           borderwidth=0)
        vsb = ttk.Scrollbar(left_container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="y", expand=False)

        left = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=left, anchor="nw")

        def _on_left_config(_e):
            canvas.configure(scrollregion=canvas.bbox("all"))
        left.bind("<Configure>", _on_left_config)

        def _on_canvas_config(e):
            # 内側フレームの幅をキャンバスに合わせる
            canvas.itemconfigure(win, width=e.width)
        canvas.bind("<Configure>", _on_canvas_config)

        def _on_wheel(e):
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind("<Enter>", lambda _e: canvas.bind_all("<MouseWheel>", _on_wheel))
        canvas.bind("<Leave>", lambda _e: canvas.unbind_all("<MouseWheel>"))

        right = ttk.Frame(main)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))

        # --- 入力 ---
        f_in = ttk.LabelFrame(left, text="入力", padding=6)
        f_in.pack(fill="x", pady=4)
        self.var_las = tk.StringVar()
        ttk.Entry(f_in, textvariable=self.var_las, width=34).grid(row=0, column=0, columnspan=2, sticky="we")
        brow = ttk.Frame(f_in)
        brow.grid(row=1, column=0, columnspan=2, sticky="we", pady=2)
        ttk.Button(brow, text="ファイル選択(LAS/GLB)", command=self._pick_las).pack(side="left")
        ttk.Button(brow, text="3D表示・計測", command=self._open_3d).pack(side="left", padx=2)
        self.lbl_las_count = ttk.Label(brow, text="", foreground="#00aaff")
        self.lbl_las_count.pack(side="left", padx=6)
        ttk.Label(f_in, text="現場名 / 工事名（表題欄・現場メモリ）:").grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.var_project = tk.StringVar(value=self.cfg.project_name)
        self.cmb_project = ttk.Combobox(f_in, textvariable=self.var_project, width=26,
                                        values=sorted(self._load_projects().keys()))
        self.cmb_project.grid(row=3, column=0, sticky="we")
        ttk.Button(f_in, text="履歴", width=5, command=self._open_project).grid(row=3, column=1, sticky="we")
        srow = ttk.Frame(f_in)
        srow.grid(row=4, column=0, columnspan=2, sticky="we", pady=(4, 0))
        ttk.Label(srow, text="縮尺:").pack(side="left")
        self.var_scale = tk.StringVar(value=self.cfg.drawing_scale)
        ttk.Combobox(srow, textvariable=self.var_scale,
                     values=["1:50", "1:100", "1:200", "1:250", "1:500"],
                     width=8).pack(side="left", padx=4)
        orow = ttk.Frame(f_in)
        orow.grid(row=5, column=0, columnspan=2, sticky="we", pady=(4, 0))
        ttk.Label(orow, text="出力先:").pack(side="left")
        self.var_outdir = tk.StringVar(value="")
        ttk.Entry(orow, textvariable=self.var_outdir, width=20).pack(side="left", padx=2)
        ttk.Button(orow, text="参照", width=4, command=self._pick_outdir).pack(side="left")
        ttk.Label(orow, text="(空=作業フォルダ)").pack(side="left", padx=2)

        # --- 対象種別 ---
        f_type = ttk.LabelFrame(left, text="対象種別", padding=6)
        f_type.pack(fill="x", pady=4)
        self.var_type = tk.StringVar(value="wall")
        for txt, val in [("擁壁（平面壁）", "wall"),
                         ("トンネル（円筒）", "tunnel"),
                         ("水路（両壁）", "channel")]:
            ttk.Radiobutton(f_type, text=txt, value=val, variable=self.var_type,
                            command=self._update_type_ui).pack(anchor="w")
        self.var_align = tk.BooleanVar(value=True)
        ttk.Checkbutton(f_type, text="アライメント自動整列（中心線に沿わせる・推奨）",
                        variable=self.var_align,
                        command=self._update_type_ui).pack(anchor="w", pady=(4, 0))
        self.var_mirror = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_type, text="左右ミラー（進行方向が逆のとき反転）",
                        variable=self.var_mirror).pack(anchor="w", pady=(2, 0))

        # トンネル選択時のみ表示：展開基準＋展開範囲（1行にまとめる）
        self.row_tunnel = ttk.Frame(f_type)
        ttk.Label(self.row_tunnel, text="基準:").pack(side="left")
        self.var_seam = tk.StringVar(
            value="地面基準" if self.cfg.tunnel_seam == "invert" else "天井基準")
        ttk.Combobox(self.row_tunnel, textvariable=self.var_seam,
                     values=["天井基準", "地面基準"],
                     width=8, state="readonly").pack(side="left", padx=4)
        ttk.Label(self.row_tunnel, text="範囲:").pack(side="left")
        self.var_devmode = tk.StringVar(value="全周")
        ttk.Combobox(self.row_tunnel, textvariable=self.var_devmode,
                     values=["全周", "左壁のみ", "右壁のみ", "左右両方"],
                     width=9, state="readonly").pack(side="left", padx=4)

        # 擁壁/水路 のときのみ表示：展開する範囲（左岸/右岸）
        self.row_wall = ttk.Frame(f_type)
        ttk.Label(self.row_wall, text="範囲:").pack(side="left")
        self.var_wallside = tk.StringVar(value="全体")
        ttk.Combobox(self.row_wall, textvariable=self.var_wallside,
                     values=["全体", "左岸のみ", "右岸のみ", "左右両方"],
                     width=10, state="readonly").pack(side="left", padx=4)

        # 擁壁/水路 かつ 整列OFF時のみ表示：壁面の水平軸
        self.row_axis = ttk.Frame(f_type)
        ttk.Label(self.row_axis, text="壁面の水平軸:").pack(side="left")
        self.var_axis = tk.StringVar(value="x")
        ttk.Combobox(self.row_axis, textvariable=self.var_axis, values=["x", "y"],
                     width=4, state="readonly").pack(side="left", padx=4)
        self._update_type_ui()

        # --- 出力図面 ---
        f_out = ttk.LabelFrame(left, text="作成する図面", padding=6)
        f_out.pack(fill="x", pady=4)
        self.var_elev = tk.BooleanVar(value=True)
        self.var_plan = tk.BooleanVar(value=True)
        self.var_prof = tk.BooleanVar(value=False)
        self.var_cross = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_out, text="展開図", variable=self.var_elev).pack(anchor="w")
        ttk.Checkbutton(f_out, text="平面図", variable=self.var_plan).pack(anchor="w")
        ttk.Checkbutton(f_out, text="縦断図", variable=self.var_prof).pack(anchor="w")
        self.var_prof_split = tk.BooleanVar(value=False)
        ttk.Checkbutton(f_out, text="　└ 天端/左壁/右壁/地面/センターを個別DXFでも出力",
                        variable=self.var_prof_split).pack(anchor="w")
        ttk.Checkbutton(f_out, text="横断図", variable=self.var_cross).pack(anchor="w")
        crow = ttk.Frame(f_out)
        crow.pack(fill="x", pady=(2, 0))
        self.var_cross_multi = tk.BooleanVar(value=True)
        ttk.Checkbutton(crow, text="横断:複数(ピッチ)", variable=self.var_cross_multi).pack(side="left")
        ttk.Label(crow, text="単一STA:").pack(side="left")
        self.var_station = tk.StringVar(value="0.0")
        ttk.Entry(crow, textvariable=self.var_station, width=6).pack(side="left")

        # --- 詳細設定（折りたたみ・既定で非表示。普段は触らない項目）---
        self.var_adv = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="詳細設定（解像度・グリッド等）を表示",
                        variable=self.var_adv,
                        command=self._toggle_adv).pack(anchor="w", pady=(4, 0))
        f_par = ttk.LabelFrame(left, text="詳細設定", padding=6)
        self.f_par = f_par
        self.params = {}
        rows = [
            ("展開図 解像度(m/px)", "elev_res", self.cfg.elevation_resolution),
            ("平面図 解像度(m/px)", "plan_res", self.cfg.plan_resolution),
            ("平面図 グリッド(m)", "plan_grid", self.cfg.plan_grid),
            ("縦断図 縦スケール", "v_scale", self.cfg.profile_v_scale),
            ("横断 スライス厚(m)", "cross_th", self.cfg.cross_section_thickness),
            ("横断 測点ピッチ(m)", "cross_pitch", self.cfg.cross_section_pitch),
            ("トンネル 坑口トリム(各端m)", "trim_ends", self.cfg.dev_trim_ends_m),
        ]
        for i, (label, key, val) in enumerate(rows):
            ttk.Label(f_par, text=label).grid(row=i, column=0, sticky="w")
            v = tk.StringVar(value=str(val))
            ttk.Entry(f_par, textvariable=v, width=10).grid(row=i, column=1, sticky="e", pady=1)
            self.params[key] = v

        # --- 操作ボタン ---
        f_btn = ttk.Frame(left)
        self.f_btn = f_btn
        f_btn.pack(fill="x", pady=6)
        self.btn_run = ttk.Button(f_btn, text="実行（作成・確認）", command=self._run)
        self.btn_run.pack(side="left", expand=True, fill="x")
        self.btn_save = ttk.Button(f_btn, text="保存…", command=self._save, state="disabled")
        self.btn_save.pack(side="left", padx=2)

        f_btn2 = ttk.Frame(left)
        f_btn2.pack(fill="x")
        ttk.Button(f_btn2, text="設定保存", command=self._save_cfg).pack(side="left")
        ttk.Button(f_btn2, text="設定読込", command=self._load_cfg).pack(side="left", padx=2)
        ttk.Button(f_btn2, text="作業フォルダを開く", command=self._open_outdir).pack(side="left")

        # --- 右側：プレビュー＋ログ ---
        f_prev = ttk.LabelFrame(
            right,
            text="プレビュー（ホイール=拡大縮小／Shift+ホイール・スクロールバー=横移動／ドラッグ=移動）",
            padding=6)
        f_prev.pack(fill="both", expand=True)

        # 図面選択（複数生成時に切り替え）
        sel_row = ttk.Frame(f_prev)
        sel_row.pack(fill="x", pady=(0, 2))
        ttk.Label(sel_row, text="図面:").pack(side="left")
        self.var_preview_sel = tk.StringVar()
        self.cmb_preview = ttk.Combobox(sel_row, textvariable=self.var_preview_sel,
                                         state="readonly", width=30)
        self.cmb_preview.pack(side="left", padx=4)
        self.cmb_preview.bind("<<ComboboxSelected>>", self._on_preview_select)

        # ズーム操作バー
        bar = ttk.Frame(f_prev)
        bar.pack(fill="x")
        ttk.Button(bar, text="＋", width=3, command=lambda: self._zoom(1.25)).pack(side="left")
        ttk.Button(bar, text="－", width=3, command=lambda: self._zoom(0.8)).pack(side="left", padx=2)
        ttk.Button(bar, text="原寸", width=5, command=self._zoom_reset).pack(side="left")
        ttk.Button(bar, text="幅", width=4, command=self._zoom_fit_width).pack(side="left", padx=2)
        ttk.Button(bar, text="高さ", width=5, command=self._zoom_fit_height).pack(side="left")
        ttk.Button(bar, text="全体", width=5, command=self._zoom_fit).pack(side="left", padx=2)
        self.zoom_label = ttk.Label(bar, text="—")
        self.zoom_label.pack(side="left", padx=6)

        # 回転操作
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=4)
        ttk.Label(bar, text="回転").pack(side="left")
        ttk.Button(bar, text="⟲", width=3, command=lambda: self._rotate(-90)).pack(side="left")
        ttk.Button(bar, text="⟳", width=3, command=lambda: self._rotate(90)).pack(side="left", padx=2)
        self.var_angle = tk.StringVar(value="0")
        ttk.Entry(bar, textvariable=self.var_angle, width=5).pack(side="left")
        ttk.Label(bar, text="°").pack(side="left")
        ttk.Button(bar, text="適用", width=5, command=self._apply_angle).pack(side="left", padx=2)
        self.angle_label = ttk.Label(bar, text="0°")
        self.angle_label.pack(side="left", padx=4)

        # 計測操作
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=6)
        self.var_measure = tk.BooleanVar(value=False)
        self.btn_measure = ttk.Checkbutton(
            bar, text="計測モード", variable=self.var_measure,
            command=self._toggle_measure)
        self.btn_measure.pack(side="left")
        ttk.Button(bar, text="クリア", width=5,
                   command=self._measure_clear).pack(side="left", padx=2)
        self.measure_label = ttk.Label(bar, text="", foreground="#00cc88")
        self.measure_label.pack(side="left", padx=4)

        # キャンバス＋縦横スクロールバー（図面が画面外に出たらスクロール）
        pv = ttk.Frame(f_prev)
        pv.pack(fill="both", expand=True, pady=(4, 0))
        self.preview = tk.Canvas(pv, background="#202020", highlightthickness=0)
        hsb = ttk.Scrollbar(pv, orient="horizontal", command=self.preview.xview)
        vsb = ttk.Scrollbar(pv, orient="vertical", command=self.preview.yview)
        self.preview.configure(xscrollcommand=hsb.set, yscrollcommand=vsb.set)
        self.preview.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        pv.rowconfigure(0, weight=1)
        pv.columnconfigure(0, weight=1)

        self._pil_full = None        # 表示中の元画像（フル解像度）
        self._scale = 1.0            # 現在の表示倍率
        self._angle = 0.0            # 現在の回転角(度)
        self._img_id = None          # キャンバス上の画像ID
        self._hint_id = self.preview.create_text(
            10, 10, anchor="nw", fill="#888888", text="（実行後に図面を表示）")

        # マウス操作（ホイール=拡大縮小、Shift+ホイール=横スクロール、ドラッグ=移動）
        self.preview.bind("<MouseWheel>", self._on_preview_wheel)
        self.preview.bind("<Shift-MouseWheel>",
                          lambda e: self.preview.xview_scroll(int(-e.delta / 120), "units"))
        self.preview.bind("<ButtonPress-1>", self._on_preview_click)
        self.preview.bind("<B1-Motion>", self._on_preview_drag)

        f_log = ttk.LabelFrame(right, text="ログ", padding=6)
        f_log.pack(fill="x", pady=(8, 0))
        self.log = tk.Text(f_log, height=9, wrap="word", state="disabled")
        self.log.pack(fill="both", expand=True)

    # ---------------- イベント ----------------
    def _pick_outdir(self):
        d = filedialog.askdirectory(title="出力先フォルダを選択")
        if d:
            self.var_outdir.set(d)

    def _pick_las(self):
        paths = filedialog.askopenfilenames(
            title="点群LAS / 3Dメッシュ(GLB/OBJ/PLY) を選択（複数可）",
            filetypes=[("点群・メッシュ", "*.las *.glb *.gltf *.obj *.ply *.stl"),
                       ("LAS", "*.las"),
                       ("3Dメッシュ", "*.glb *.gltf *.obj *.ply *.stl"),
                       ("すべて", "*.*")])
        if not paths:
            return
        self._las_paths = list(paths)
        if len(paths) == 1:
            self.var_las.set(paths[0])
            self.lbl_las_count.config(text="")
        else:
            self.var_las.set(os.path.basename(paths[0]))
            self.lbl_las_count.config(text=f"他 {len(paths) - 1} ファイル  計 {len(paths)} 件")

    def _open_outdir(self):
        """直近の作業フォルダ（無ければworks）を開く。"""
        d = self._workdir or str(WORKS_DIR)
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)   # Windows
        except Exception as e:
            messagebox.showwarning("確認", f"フォルダを開けませんでした:\n{e}")

    def _open_3d(self):
        """選択中のファイルを組み込み3Dビューアで表示・計測する。複数ファイル対応。"""
        paths = self._las_paths if self._las_paths else []
        if not paths:
            single = self.var_las.get().strip()
            paths = [single] if single else []
        missing = [p for p in paths if not os.path.exists(p)]
        if not paths or missing:
            messagebox.showwarning("3D表示・計測", "先にLAS/GLBファイルを選択してください。")
            return
        label = (os.path.basename(paths[0]) if len(paths) == 1
                 else f"{len(paths)} ファイル結合")
        self._log(
            f"3Dビューア起動中… ({label})  "
            "[Shift+クリック=計測点追加 / ウィンドウを閉じる=計測実行]"
        )
        viewer_3d.launch_viewer_thread(paths, log_cb=self._log)

    def _open_project(self):
        """選択中の現場の作成済み図面フォルダを開く（現場メモリ）。"""
        name = self.var_project.get().strip()
        data = self._load_projects()
        info = data.get(name)
        d = (info or {}).get("dir") or str(WORKS_DIR / _sanitize(name))
        if not os.path.isdir(d):
            messagebox.showinfo("現場メモリ",
                                "この現場の作成済み図面はまだありません。\n"
                                "「実行」で作成すると現場ごとに記憶されます。")
            return
        if info:
            self._log(f"現場「{name}」の図面: {', '.join(info.get('drawings', []))}"
                      f"（更新 {info.get('updated','')}）")
        try:
            os.startfile(d)
        except Exception as e:
            messagebox.showwarning("確認", f"フォルダを開けませんでした:\n{e}")

    def _toggle_adv(self):
        """詳細設定の表示/非表示。操作ボタンの直前に差し込み、順序を保つ。"""
        if self.var_adv.get():
            self.f_par.pack(fill="x", pady=4, before=self.f_btn)
        else:
            self.f_par.pack_forget()

    def _update_type_ui(self):
        """対象種別・整列に応じて関係する項目だけ表示する（操作をシンプルに）。"""
        typ = self.var_type.get()
        # トンネル専用（展開基準・展開範囲）
        if typ == "tunnel":
            self.row_tunnel.pack(fill="x", pady=(2, 0))
        else:
            self.row_tunnel.pack_forget()
        # 擁壁/水路（左岸/右岸の範囲選択）
        if typ in ("wall", "channel"):
            self.row_wall.pack(fill="x", pady=(2, 0))
        else:
            self.row_wall.pack_forget()
        # 壁面の水平軸は「擁壁/水路」かつ「整列OFF」のときだけ必要
        if typ in ("wall", "channel") and not self.var_align.get():
            self.row_axis.pack(fill="x", pady=(2, 0))
        else:
            self.row_axis.pack_forget()

    def _collect_params(self):
        single = self.var_las.get().strip()
        las_paths = self._las_paths if self._las_paths else ([single] if single else [])
        return {
            "las": las_paths[0] if las_paths else "",  # 後方互換（ログ・バリデ用）
            "las_paths": las_paths,
            "type": self.var_type.get(),
            "axis": self.var_axis.get(),
            "do_elev": self.var_elev.get(),
            "do_plan": self.var_plan.get(),
            "do_prof": self.var_prof.get(),
            "prof_split": self.var_prof_split.get(),
            "do_cross": self.var_cross.get(),
            "cross_multi": self.var_cross_multi.get(),
            "align": self.var_align.get(),
            "mirror": self.var_mirror.get(),
            "seam": "invert" if self.var_seam.get() == "地面基準" else "crown",
            "devmode": self.var_devmode.get(),
            "wallside": self.var_wallside.get(),
            "station": self._to_float(self.var_station.get(), 0.0),
            "project": self.var_project.get().strip(),
            "scale": self.var_scale.get().strip(),
            "outdir_custom": self.var_outdir.get().strip(),
            "fields": {k: v.get() for k, v in self.params.items()},
        }

    # 図面の内部キーと表示名
    DRAWINGS = [("do_elev", "展開図"), ("do_plan", "平面図"),
                ("do_prof", "縦断図"), ("do_cross", "横断図")]

    def _run(self):
        """実行：作業フォルダ(現場別)に図面を作成しプレビュー（外部保存はしない）。"""
        p = self._collect_params()
        paths = p.get("las_paths", [])
        missing = [pp for pp in paths if not os.path.exists(pp)]
        if not paths or missing:
            msg = "有効なLASファイルを選択してください。"
            if missing:
                msg = f"ファイルが見つかりません:\n{missing[0]}"
            messagebox.showwarning("確認", msg)
            return
        if not any(p[k] for k, _ in self.DRAWINGS):
            messagebox.showwarning("確認", "作成する図面を1つ以上チェックしてください。")
            return
        genba = p["project"] or "未分類"
        custom = p.get("outdir_custom", "").strip()
        if custom:
            # 出力先を指定した場合は、そのフォルダに直接出力（任意の場所）
            workdir = os.path.join(custom, _sanitize(genba)) if genba != "未分類" else custom
        else:
            workdir = str(WORKS_DIR / _sanitize(genba))
        os.makedirs(workdir, exist_ok=True)
        p["outdir"] = workdir
        p["genba"] = genba
        self._workdir = workdir
        self.btn_run.config(state="disabled")
        self.btn_save.config(state="disabled")
        threading.Thread(target=self._worker, args=(p,), daemon=True).start()

    def _save(self):
        """保存：実行で作成済みの図面を、保存先を選んで外部に出力。"""
        if not self._generated:
            messagebox.showinfo("保存", "先に「実行」で図面を作成してください。")
            return
        choice = self._ask_save(self._generated)
        if choice is None:
            return
        dest, titles, method = choice
        try:
            n = self._export(dest, titles, method)
            self._log(f"保存しました（{method}・{n}件）: {dest}")
            messagebox.showinfo("保存", f"{n}件を保存しました。\n{dest}")
        except Exception:
            self._log("保存エラー:\n" + traceback.format_exc())
            messagebox.showerror("保存", "保存に失敗しました。ログを確認してください。")

    def _ask_save(self, generated):
        """保存ダイアログ：保存先・図面の最終選択・出力方法を返す。
        戻り値 (dest, titles:set, method) または None。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("保存（出力先・図面・方法）")
        dlg.transient(self.root)
        dlg.grab_set()
        result = {}

        # 保存先
        drow = ttk.Frame(dlg, padding=6)
        drow.pack(fill="x")
        ttk.Label(drow, text="保存先:").pack(side="left")
        var_dest = tk.StringVar(value=str(Path.home() / "Desktop"))
        ttk.Entry(drow, textvariable=var_dest, width=32).pack(side="left", padx=4)

        def _pick():
            d = filedialog.askdirectory(title="保存先フォルダ", parent=dlg)
            if d:
                var_dest.set(d)
        ttk.Button(drow, text="選択…", command=_pick).pack(side="left")

        ttk.Label(dlg, text="保存する図面（チェックを外すと除外）:",
                  padding=(6, 2)).pack(anchor="w")
        vars_ = {}
        for g in generated:
            v = tk.BooleanVar(value=True)
            vars_[g["title"]] = v
            ttk.Checkbutton(dlg, text=g["title"], variable=v).pack(anchor="w", padx=16)

        ttk.Separator(dlg, orient="horizontal").pack(fill="x", pady=6)
        ttk.Label(dlg, text="出力方法:", padding=(6, 0)).pack(anchor="w")
        method = tk.StringVar(value="individual")
        ttk.Radiobutton(dlg, text="個別ファイル（図面ごとにDXF＋PNG）",
                        value="individual", variable=method).pack(anchor="w", padx=16)
        ttk.Radiobutton(dlg, text="1枚のDXFにまとめる（一覧シート）",
                        value="merge", variable=method).pack(anchor="w", padx=16)
        ttk.Radiobutton(dlg, text="ZIPに各図面を格納",
                        value="zip", variable=method).pack(anchor="w", padx=16)

        btns = ttk.Frame(dlg, padding=8)
        btns.pack(fill="x")

        def _ok():
            sel = {t for t, v in vars_.items() if v.get()}
            if not sel:
                messagebox.showwarning("確認", "1つ以上選択してください。", parent=dlg)
                return
            if not var_dest.get().strip():
                messagebox.showwarning("確認", "保存先を指定してください。", parent=dlg)
                return
            result["v"] = (var_dest.get().strip(), sel, method.get())
            dlg.destroy()

        ttk.Button(btns, text="保存", command=_ok).pack(side="right")
        ttk.Button(btns, text="キャンセル", command=dlg.destroy).pack(side="right", padx=6)
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        dlg.update_idletasks()
        dlg.geometry(f"+{self.root.winfo_rootx()+60}+{self.root.winfo_rooty()+60}")
        self.root.wait_window(dlg)
        return result.get("v")

    def _export(self, dest, titles, method):
        """作成済み図面を保存先へ出力。"""
        items = [g for g in self._generated if g["title"] in titles]
        os.makedirs(dest, exist_ok=True)
        genba = _sanitize(self.var_project.get())
        if method == "zip":
            zpath = os.path.join(dest, f"{genba}_図面一式.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for g in items:
                    for f in (g["dxf"], g.get("png")):
                        if f and os.path.exists(f):
                            zf.write(f, os.path.basename(f))
            return len(items)
        if method == "merge":
            sheet_items = []
            for g in items:
                if g.get("png") and os.path.exists(g["png"]):
                    shutil.copy2(g["png"], dest)
                    sheet_items.append((g["title"],
                                        os.path.join(dest, os.path.basename(g["png"]))))
            cfg = self._make_cfg({k: v.get() for k, v in self.params.items()})
            sheet = os.path.join(dest, f"{genba}_図面まとめ.dxf")
            lp.merge_image_sheet(sheet_items, sheet, cfg=cfg)
            return len(items)
        # individual
        for g in items:
            for f in (g["dxf"], g.get("png")):
                if f and os.path.exists(f):
                    shutil.copy2(f, dest)
        return len(items)

    # ---------------- 処理本体（別スレッド）----------------
    def _make_cfg(self, fields):
        cfg = Config.load(CONFIG_PATH)
        m = {
            "elev_res": ("elevation_resolution", "tunnel_resolution"),
            "plan_res": ("plan_resolution",),
            "plan_grid": ("plan_grid",),
            "v_scale": ("profile_v_scale",),
            "cross_th": ("cross_section_thickness",),
            "cross_pitch": ("cross_section_pitch",),
            "trim_ends": ("dev_trim_ends_m",),
        }
        for key, attrs in m.items():
            val = fields.get(key, "").strip()
            if val == "":
                continue
            try:
                fv = float(val)
            except ValueError:
                continue
            for a in attrs:
                setattr(cfg, a, fv)
        return cfg

    def _worker(self, p):
        try:
            cfg = self._make_cfg(p["fields"])
            cfg.project_name = p.get("project", "")
            if p.get("scale"):
                cfg.drawing_scale = p["scale"]
            outdir = p["outdir"]
            os.makedirs(outdir, exist_ok=True)
            typ = p["type"]
            prefix = {"wall": "擁壁", "tunnel": "トンネル", "channel": "水路"}.get(typ, "")
            las_paths = p.get("las_paths") or [p["las"]]
            if len(las_paths) == 1:
                self._log(f"読み込み中: {las_paths[0]}")
                pts = lp.load_points(las_paths[0])
            else:
                self._log(f"複数ファイル結合読み込み: {len(las_paths)} 件")
                pts_list = []
                for i, fp in enumerate(las_paths):
                    self._log(f"  [{i + 1}/{len(las_paths)}] {os.path.basename(fp)}")
                    pts_list.append(lp.load_points(fp))
                pts = lp.merge_points(pts_list)
                self._log(f"結合完了: 合計 {pts['count']:,} 点")
            self._log(f"点数 {pts['count']:,}  "
                      f"X{tuple(round(v,2) for v in pts['x_range'])} "
                      f"Y{tuple(round(v,2) for v in pts['y_range'])} "
                      f"Z{tuple(round(v,2) for v in pts['z_range'])}")

            # トンネル展開は内部で独自に整列＋弧長計算するため、整列前の元点群を保持
            # （reorient で弦長に潰すと曲がり区間の距離が圧縮されてしまう）
            pts_raw = pts
            # アライメント自動整列：中心線(延長)をXに揃える
            align = p.get("align", True)
            if align:
                pts = lp.reorient_by_alignment(pts)
                axis_use = "x"
                self._log("アライメント整列: 延長方向をXに整列しました")
            else:
                axis_use = p["axis"]
            mirror = p.get("mirror", False)
            if mirror:
                self._log("左右ミラー: 画像を左右反転し、縦断図の左壁・右壁を入れ替えて保存します")
            first_png = None
            generated = []   # {title, dxf, png}

            if p["do_elev"]:
                # 出力する展開図の一覧を作る [(title, img, meta, kind, note)]
                dev_items = []
                if typ == "wall":
                    wallside = p.get("wallside", "全体")
                    if wallside == "全体":
                        img, meta = lp.generate_elevation_wall(pts, axis=axis_use, cfg=cfg)
                        dev_items.append((f"{prefix}展開図", img, meta, "wall", ""))
                    else:
                        left, right, _c = lp.split_left_right(pts, station_axis=axis_use, cfg=cfg)
                        if wallside in ("左岸のみ", "左右両方"):
                            img, meta = lp.generate_elevation_wall(left, axis=axis_use, cfg=cfg)
                            dev_items.append((f"{prefix}左岸展開図", img, meta, "wall", "左岸"))
                        if wallside in ("右岸のみ", "左右両方"):
                            img, meta = lp.generate_elevation_wall(right, axis=axis_use, cfg=cfg)
                            dev_items.append((f"{prefix}右岸展開図", img, meta, "wall", "右岸"))
                else:
                    devmode = p.get("devmode", "全周")
                    if devmode in ("左壁のみ", "左右両方"):
                        img, meta = lp.generate_tunnel_wall_development(pts_raw, side="left", cfg=cfg)
                        dev_items.append((f"{prefix}左壁展開図", img, meta, "tunnel", "左壁・底基準"))
                    if devmode in ("右壁のみ", "左右両方"):
                        img, meta = lp.generate_tunnel_wall_development(pts_raw, side="right", cfg=cfg)
                        dev_items.append((f"{prefix}右壁展開図", img, meta, "tunnel", "右壁・底基準"))
                    if devmode == "全周":
                        img, meta = lp.generate_tunnel_development(pts_raw, cfg=cfg, seam=p.get("seam"))
                        note = "地面基準" if p.get("seam") == "invert" else "天井基準"
                        dev_items.append((f"{prefix}展開図", img, meta, "tunnel", note))
                for title, img, meta, kind, note in dev_items:
                    if mirror:
                        img = np.fliplr(img)
                    png = os.path.join(outdir, title + "_プレビュー.png")
                    self._save_png(img, png)
                    dxf = os.path.join(outdir, title + ".dxf")
                    if kind == "wall":
                        lp.export_developed_elevation_dxf(
                            img, meta, dxf, title=title, cfg=cfg, image_path=png)
                    else:
                        lp.export_elevation_dxf(img, meta, dxf, title=title, cfg=cfg, image_path=png)
                    first_png = first_png or png
                    generated.append({"title": title, "dxf": dxf, "png": png, "meta": meta})
                    emb = "（画像埋め込み）" if cfg.embed_raster else ""
                    kj = f"（{note}）" if note else ""
                    self._log(f"展開図 生成: {title}.dxf {emb}{kj}")

            if p["do_plan"]:
                img, meta = lp.generate_plan_view(pts, cfg=cfg)
                if mirror:
                    img = np.fliplr(img)
                title = f"{prefix}平面図"
                png = os.path.join(outdir, title + "_プレビュー.png")
                self._save_png(img, png)
                dxf = os.path.join(outdir, title + ".dxf")
                lp.export_plan_dxf(pts, meta, dxf, title=title, cfg=cfg, img=img, image_path=png)
                first_png = first_png or png
                generated.append({"title": title, "dxf": dxf, "png": png, "meta": meta})
                self._log(f"平面図 生成: {title}.dxf")

            if p["do_prof"]:
                title = f"{prefix}縦断図"
                prof = None
                src = ""
                if align:
                    prof = lp.generate_longitudinal(pts, cfg=cfg, swap_lr=mirror)
                    src = "alignment"
                else:
                    centerline, src = lp.get_centerline(pts)
                    if centerline is not None and len(centerline) >= 2:
                        prof = lp.generate_profile(pts, centerline, cfg=cfg, swap_lr=mirror)
                    else:
                        self._log(f"縦断図: センターライン取得失敗（{src}）、スキップします")
                if prof:
                    dxf = os.path.join(outdir, title + ".dxf")
                    lp.export_profile_dxf(prof, dxf, title=title, cfg=cfg)
                    png = self._render_dxf(dxf, outdir, title)
                    first_png = first_png or png
                    generated.append({"title": title, "dxf": dxf, "png": png})
                    self._log(f"縦断図 生成: {title}.dxf （{src}）")

                    if p.get("prof_split"):
                        for key in lp.PROFILE_LINE_KEYS:
                            if len(prof.get(key, [])) < 2:
                                continue
                            label = lp.PROFILE_LINE_LABELS[key]
                            item_title = f"{title}_{label}"
                            item_dxf = os.path.join(outdir, item_title + ".dxf")
                            lp.export_profile_dxf(prof, item_dxf, title=item_title, cfg=cfg, only=key)
                            item_png = self._render_dxf(item_dxf, outdir, item_title)
                            generated.append({"title": item_title, "dxf": item_dxf, "png": item_png})
                        self._log(f"縦断図 個別出力: 天端/左壁/右壁/地面/センターを{len(lp.PROFILE_LINE_KEYS)}本のDXFに分けて保存しました")

            if p["do_cross"]:
                mode = "wall" if typ == "wall" else "tunnel"
                axis = axis_use if align else (p["axis"] if typ == "wall" else "z")
                if p["cross_multi"]:
                    secs = lp.generate_cross_sections(pts, mode=mode, axis=axis, cfg=cfg)
                    title = f"{prefix}横断図_複数"
                    dxf = os.path.join(outdir, title + ".dxf")
                    lp.export_cross_sections_dxf(secs, dxf, cfg=cfg)
                    self._log(f"横断図 生成: {title}.dxf （{len(secs)}断面）")
                else:
                    sec = lp.generate_cross_section(pts, station=p["station"], mode=mode, axis=axis, cfg=cfg)
                    title = f"{prefix}横断図"
                    dxf = os.path.join(outdir, title + ".dxf")
                    lp.export_cross_section_dxf(sec, dxf, cfg=cfg)
                    self._log(f"横断図 生成: {title}.dxf （STA {p['station']:.2f}m, {sec['n_points']}点）")
                png = self._render_dxf(dxf, outdir, title)
                first_png = first_png or png
                generated.append({"title": title, "dxf": dxf, "png": png})

            self._log("作成完了 ✓  内容を確認し、問題なければ「保存…」で外部出力してください")
            self.msg_q.put(("result", {"generated": generated,
                                       "genba": p.get("genba", "未分類"),
                                       "workdir": outdir}))
            if first_png:
                self.msg_q.put(("preview", first_png))
        except Exception:
            self._log("エラー:\n" + traceback.format_exc())
        finally:
            self.msg_q.put(("done", None))

    # ---------------- ユーティリティ ----------------
    @staticmethod
    def _to_float(s, default):
        try:
            return float(s)
        except (ValueError, TypeError):
            return default

    def _save_png(self, img: np.ndarray, path: str):
        Image.fromarray(img).save(path)

    def _render_dxf(self, dxf_path: str, outdir: str, title: str):
        """線画DXF（縦断図・横断図）をPNG化してプレビュー用パスを返す。失敗時None。"""
        png = os.path.join(outdir, title + "_プレビュー.png")
        try:
            lp.render_dxf_to_png(dxf_path, png)
            return png
        except Exception as e:
            self._log(f"DXFプレビュー生成に失敗（{title}）: {e}")
            return None

    def _log(self, text):
        self.msg_q.put(("log", text))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_q.get_nowait()
                if kind == "log":
                    self.log.config(state="normal")
                    self.log.insert("end", payload + "\n")
                    self.log.see("end")
                    self.log.config(state="disabled")
                elif kind == "preview":
                    self._show_preview(payload)
                elif kind == "result":
                    self._generated = payload["generated"]
                    self._workdir = payload["workdir"]
                    if self._generated:
                        self._record_project(payload["genba"], payload["workdir"],
                                             self._generated)
                        self.btn_save.config(state="normal")
                        titles = [g["title"] for g in self._generated]
                        self.cmb_preview["values"] = titles
                        self._preview_paths = {g["title"]: g.get("png")
                                               for g in self._generated}
                        self._preview_metas = {g["title"]: g.get("meta")
                                               for g in self._generated}
                        if titles:
                            self.cmb_preview.set(titles[0])
                            self._current_meta = self._generated[0].get("meta")
                elif kind == "done":
                    self.btn_run.config(state="normal")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_queue)

    def _show_preview(self, path):
        try:
            self._pil_full = Image.open(path).convert("RGB")
        except Exception:
            self._log("プレビュー表示に失敗: " + path)
            return
        if self._hint_id is not None:
            self.preview.delete(self._hint_id)
            self._hint_id = None
        self._angle = 0.0
        self.var_angle.set("0")
        self.angle_label.config(text="0°")
        # 既定は「原寸（あるがまま）」：縦横とも固定せず、はみ出しは縦横スクロールで対応
        self._zoom_reset()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)

    def _on_preview_select(self, _e=None):
        title = self.var_preview_sel.get()
        path = self._preview_paths.get(title)
        if path and os.path.exists(path):
            self._show_preview(path)
        self._current_meta = self._preview_metas.get(title)
        self._measure_clear()

    def _render_preview(self):
        """現在の倍率・回転で画像を描き直す。"""
        if self._pil_full is None:
            return
        base = self._pil_full
        if self._angle % 360 != 0:
            base = base.rotate(-self._angle, expand=True, fillcolor=(32, 32, 32))
        w = max(1, int(base.width * self._scale))
        h = max(1, int(base.height * self._scale))
        resample = Image.NEAREST if self._scale > 1 else Image.LANCZOS
        im = base.resize((w, h), resample)
        self._tkimg = ImageTk.PhotoImage(im)
        if self._img_id is None:
            self._img_id = self.preview.create_image(0, 0, anchor="nw", image=self._tkimg)
        else:
            self.preview.itemconfigure(self._img_id, image=self._tkimg)
        self.preview.configure(scrollregion=(0, 0, w, h))
        self.zoom_label.config(text=f"{self._scale*100:.0f}%")

    def _zoom(self, factor):
        if self._pil_full is None:
            return
        self._scale = min(max(self._scale * factor, 0.05), 20.0)
        self._render_preview()

    def _zoom_reset(self):
        if self._pil_full is None:
            return
        self._scale = 1.0
        self._render_preview()

    def _zoom_fit(self):
        if self._pil_full is None:
            return
        # 回転後の見かけサイズに合わせる
        bw, bh = self._pil_full.width, self._pil_full.height
        if self._angle % 180 != 0:
            bw, bh = bh, bw
        cw = self.preview.winfo_width() or 520
        ch = self.preview.winfo_height() or 460
        self._scale = min(cw / bw, ch / bh)
        self._scale = min(max(self._scale, 0.05), 20.0)
        self._render_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)

    def _zoom_fit_height(self):
        """高さをウィンドウに合わせる。横長の図面は横にはみ出し横スクロールで対応。"""
        if self._pil_full is None:
            return
        bh = self._pil_full.height
        if self._angle % 180 != 0:
            bh = self._pil_full.width
        ch = self.preview.winfo_height() or 460
        self._scale = min(max((ch - 4) / bh, 0.05), 20.0)
        self._render_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)

    def _zoom_fit_width(self):
        """幅をウィンドウに合わせる。縦長の図面は下にはみ出し縦スクロールで対応。"""
        if self._pil_full is None:
            return
        bw = self._pil_full.width
        if self._angle % 180 != 0:
            bw = self._pil_full.height
        cw = self.preview.winfo_width() or 520
        self._scale = min(max((cw - 4) / bw, 0.05), 20.0)
        self._render_preview()
        self.preview.xview_moveto(0)
        self.preview.yview_moveto(0)

    def _on_preview_wheel(self, e):
        self._zoom(1.1 if e.delta > 0 else 0.9)

    def _rotate(self, delta):
        if self._pil_full is None:
            return
        self._angle = (self._angle + delta) % 360
        self.var_angle.set(f"{self._angle:g}")
        self.angle_label.config(text=f"{self._angle:g}°")
        self._zoom_fit()

    def _apply_angle(self):
        try:
            self._angle = float(self.var_angle.get()) % 360
        except ValueError:
            return
        self.angle_label.config(text=f"{self._angle:g}°")
        if self._pil_full is not None:
            self._zoom_fit()

    # ──────────────────────────────────
    # 2D計測（プレビュー上でクリック計測）
    # ──────────────────────────────────
    def _on_preview_click(self, e):
        if self._measure_mode and self._current_meta:
            self._measure_add_point(e)
        else:
            self.preview.scan_mark(e.x, e.y)

    def _on_preview_drag(self, e):
        if not self._measure_mode:
            self.preview.scan_dragto(e.x, e.y, gain=1)

    def _toggle_measure(self):
        self._measure_mode = self.var_measure.get()
        if not self._measure_mode:
            self._measure_clear()
        else:
            meta = self._current_meta
            if meta:
                self._log(
                    "計測モード ON  "
                    f"[クリック=計測点追加 / 解像度 {meta.get('resolution', 0):.4f} m/px]"
                )
            else:
                self._log("計測モード ON  ※この図面はスケール情報なし（ピクセル単位）")

    def _measure_clear(self):
        for item in self._measure_items:
            self.preview.delete(item)
        self._measure_items.clear()
        self._measure_pts_canvas.clear()
        self._measure_pts_world.clear()
        if hasattr(self, "measure_label"):
            self.measure_label.config(text="")

    def _measure_add_point(self, e):
        cx = self.preview.canvasx(e.x)
        cy = self.preview.canvasy(e.y)
        meta = self._current_meta
        res = meta.get("resolution", 1.0) if meta else 1.0
        # キャンバス座標 → 元画像ピクセル → 実寸(m)
        wx = cx / self._scale * res
        wy = cy / self._scale * res

        self._measure_pts_canvas.append((cx, cy))
        self._measure_pts_world.append((wx, wy))
        n = len(self._measure_pts_canvas)

        # 点マーカー
        r = 5
        dot = self.preview.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill="#ffff00", outline="#ff8800", width=2)
        lbl = self.preview.create_text(
            cx + 8, cy - 8, text=str(n),
            fill="#ffff00", font=("Arial", 9, "bold"), anchor="nw")
        self._measure_items.extend([dot, lbl])

        if n >= 2:
            pcx, pcy = self._measure_pts_canvas[-2]
            pwx, pwy = self._measure_pts_world[-2]
            # 結線
            line = self.preview.create_line(
                pcx, pcy, cx, cy,
                fill="#ff5500", width=2, dash=(6, 3))
            # 区間距離ラベル
            d = math.hypot(wx - pwx, wy - pwy)
            unit = "m" if meta else "px"
            dlbl = self.preview.create_text(
                (pcx + cx) / 2, (pcy + cy) / 2 - 10,
                text=f"{d:.3f}{unit}",
                fill="#00ffcc", font=("Arial", 9, "bold"), anchor="center")
            self._measure_items.extend([line, dlbl])

        self._update_measure_label()

    def _update_measure_label(self):
        pts = self._measure_pts_world
        meta = self._current_meta
        unit = "m" if meta else "px"
        n = len(pts)
        if n < 2:
            self.measure_label.config(text=f"点 {n}")
            return
        total = sum(
            math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
            for i in range(n - 1))
        text = f"延長: {total:.3f}{unit}"
        if n >= 3:
            area = abs(sum(
                pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
                for i in range(n))) / 2.0
            text += f"   面積: {area:.3f}{unit}²"
        self.measure_label.config(text=text)
        self._log(text)

    # ──────────────────────────────────

    def _save_cfg(self):
        cfg = self._make_cfg({k: v.get() for k, v in self.params.items()})
        cfg.save(CONFIG_PATH)
        self._log("設定を保存しました: config.json")

    def _load_cfg(self):
        self.cfg = Config.load(CONFIG_PATH)
        mapping = {
            "elev_res": self.cfg.elevation_resolution,
            "plan_res": self.cfg.plan_resolution,
            "plan_grid": self.cfg.plan_grid,
            "v_scale": self.cfg.profile_v_scale,
            "cross_th": self.cfg.cross_section_thickness,
            "cross_pitch": self.cfg.cross_section_pitch,
            "trim_ends": self.cfg.dev_trim_ends_m,
        }
        for k, val in mapping.items():
            self.params[k].set(str(val))
        self._log("設定を読み込みました: config.json")


def main():
    # 起動.bat から起動された場合、背後のCMDウィンドウを最小化する
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 2)  # SW_MINIMIZE
    except Exception:
        pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
