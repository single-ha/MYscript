# -*- coding: utf-8 -*-
"""拓印页：全局共享临摹能力的独立入口（随工具页启用后迁到这里）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ..common import Card, bind_wraplength, param_entry


class TuoyingPage(ctk.CTkFrame):
    """拓印：刷副本点「进入」偶发的「拓印」临摹弹窗的自动描摹能力，这里可单独标定 / 跑一遍完整临摹。
    标定资产存共享命名空间 tasks.tuoying（所有副本共用，只读这份）。"""

    TASK_NAME = "tuoying"
    LOG_SOURCE = "拓印"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._tuo_cal_dialog = None   # 「标定（拓印）」去重槽
        self.lbl_ready = None         # 就绪状态标签
        self.var_rounds = None        # (已废弃)「临摹多少遍」输入框 StringVar

        self.grid_columnconfigure(0, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="拓印", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="刷副本点「进入」后，队长窗口偶发弹「拓印」临摹界面：需按住鼠标沿随机图案"
                                     "描一遍再点「上传」。这里标一次、所有副本共用；也可以单独跑一遍完整临摹"
                                     "（描一遍+自动点上传）验证手感。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：运行按钮 + 工具（标定/刷新配置）----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text="▶  开始拓印", font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_tuoying_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="离线预览描摹…", font=self.fonts["body"], height=36, width=136,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_trace_preview).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        self._build_humanize_row(card)

    def _build_humanize_row(self, card):
        """拟人化强度滑动条（tasks.tuoying.loop.tuoying_humanize，0~1，改动即保存）。
        command 只刷数值标签，<ButtonRelease-1> 才落盘，避免拖动过程中反复写 config.json。"""
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        box.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(box, text="拟人化程度", font=self.fonts["body_b"], text_color=T.TEXT,
                     width=110, anchor="w").grid(row=0, column=0, sticky="w")
        self.var_humanize = ctk.DoubleVar(value=self._get_humanize())
        self.lbl_humanize = ctk.CTkLabel(box, text=f"{self.var_humanize.get():.2f}",
                                         font=self.fonts["body_b"], text_color=T.ACCENT,
                                         width=48, anchor="e")
        self.lbl_humanize.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self.slider_humanize = ctk.CTkSlider(
            box, from_=0.0, to=1.0, number_of_steps=20, variable=self.var_humanize,
            command=self._on_humanize_move, progress_color=T.ACCENT,
            button_color=T.ACCENT, button_hover_color=T.ACCENT_HOVER)
        self.slider_humanize.grid(row=0, column=1, sticky="ew", padx=10)
        self.slider_humanize.bind("<ButtonRelease-1>", lambda _e: self._save_humanize())
        hint = ctk.CTkLabel(
            box, text="每次自动临摹的笔顺 / 起笔方向 / 采样步长 / 停顿都会随机变化，"
                      "不再每次都一模一样；只改走法，不影响图案覆盖率与「完成」判定。"
                      "0 = 固定不变（旧行为），1 = 变化最大。",
            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        bind_wraplength(hint)

    def _get_humanize(self):
        cfg = getattr(self.app, "cfg", None)
        if not cfg:
            cfg = cfg_mod.load_config()
        # 直接读本任务原始配置（不走 task_config 合并，避免把共享标定叠加产物读进来）
        raw = ((cfg.get("tasks", {}) or {}).get(self.TASK_NAME, {}) or {})
        try:
            v = float((raw.get("loop") or {}).get("tuoying_humanize", 0.6))
        except (TypeError, ValueError):
            v = 0.6
        return max(0.0, min(1.0, v))

    def _on_humanize_move(self, val):
        self.lbl_humanize.configure(text=f"{float(val):.2f}")

    def _save_humanize(self):
        cfg = cfg_mod.load_config()
        # 只写本任务自己的 loop 键：不调 task_config（它会叠加共享 regions/templates，
        # 整体回写会把共享标定污染进 tasks.tuoying）
        loop = (cfg.setdefault("tasks", {}).setdefault(self.TASK_NAME, {})
                   .setdefault("loop", {}))
        loop["tuoying_humanize"] = round(max(0.0, min(1.0, float(self.var_humanize.get()))), 2)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._log_line(f"拟人化程度已保存：{loop['tuoying_humanize']:.2f}", "info")

    # ---- 主体：完成度 + 说明 ----
    def _build_body(self):
        c = Card(self)
        c.grid(row=2, column=0, sticky="ew", padx=4)
        c.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="ew")
        self.lbl_ready = ctk.CTkLabel(txt, text="", font=self.fonts["body"], text_color=T.TEXT_DIM, anchor="w")
        self.lbl_ready.pack(fill="x", anchor="w", pady=(4, 0))
        sub = ctk.CTkLabel(txt, text="就绪要求：框选好「拓印描摹绘制区」+ 标定「上传」按钮 + 标定「拓印」界面标题即可全自动"
                                     "（描→上传→轮询「未完成态/标题」判定续描或完成）。强烈建议再标一个「未完成状态按钮」"
                                     "（临摹未完成时按钮的样子；描完会变「完成」态）：点「上传」后拓印弹窗通常"
                                     "不会关，靠这个按钮能准确判定「新图案已生成、要描下一遍」。"
                                     "对所选窗口里的第一个号操作：把拓印临摹界面调到前台再点「开始拓印」。"
                                     "绘制区要**尽量贴着图案所在的纸面框**（别把对话框边框/分割线框进来）。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", anchor="w", pady=(2, 0))
        bind_wraplength(sub)

        ctk.CTkFrame(c, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))

        tip = ctk.CTkLabel(c, text="说明：拓印只由刷副本的队长窗口触发，队员不弹；自动临摹会认图案、沿骨架描并自动点「上传」，"
                                   "点完上传后轮询「未完成状态按钮」和「拓印标题」判定——见「未完成态」就继续描，"
                                   "两者都不见（持续短暂确认后）就判完成。是全部副本共用的共享能力。"
                                   "它和整理背包一样是「共享能力」，平时不用特意来这个页——"
                                   "只有首次标定、或想验证描摹手感时才需要这里。"
                                   "认不出图案时会把调试图（frame/mask/skel/strokes/trace）自动存到 "
                                   "captures_debug/拓印描摹/，也可点「离线预览描摹…」拿任意截图先离线验证。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        tip.pack(fill="x", padx=16, pady=(14, 14))
        bind_wraplength(tip)

    # ------------------------------------------------------------------
    # 刷新 / 渲染
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tuo_tc = cfg_mod.task_config(self.app.cfg, "tuoying")
        tpl = (tuo_tc.get("templates") or {}) or {}
        reg = (tuo_tc.get("regions") or {}) or {}
        area_ok = bool(reg.get("tuoying_area"))
        up_ok = bool(tpl.get("tuoying_upload"))
        title_ok = bool(tpl.get("tuoying_title"))
        todo_ok = bool(tpl.get("tuoying_todo"))
        ready = area_ok and up_ok and title_ok
        self.lbl_ready.configure(
            text=f"绘制区：{'● 已框选' if area_ok else '○ 未标定'}；上传按钮：{'● 已标定' if up_ok else '○ 未标定'}"
                 + f"；标题模板：{'●' if title_ok else '○'}（判定完成所必需）"
                 + f"；未完成状态按钮：{'●' if todo_ok else '○'}（可选建议）"
                 + ("　✓ 可全自动临摹" if ready else "　（需框选绘制区 + 上传按钮 + 标题）"),
            text_color=T.SUCCESS if ready else T.WARN)
        if getattr(self, "var_humanize", None) is not None:
            v = self._get_humanize()
            self.var_humanize.set(v)
            self.lbl_humanize.configure(text=f"{v:.2f}")



    def _open_trace_preview(self):
        """离线预览描摹：选一张拓印界面截图，跑一遍图案提取、把诊断图落 captures_debug（按日期），
        控制台/日志只给摘要（ASCII 缩略图在命令行工具 `python -m mhxy.tools.trace_preview` 里看）。"""
        import os
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            title="选择一张拓印临摹界面截图",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if not path:
            return
        self._log_line("开始离线预览描摹（不驱动鼠标）…", "info")

        def _work():
            import io
            try:
                from ...tools import trace_preview
                frame, src = trace_preview.resolve_client_frame()
                buf = io.StringIO()
                summary = trace_preview.run(path, ascii_width=0, stream=buf, client_frame=frame)
                lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
                lines.append(f"已补偿窗口边框（{src}）：客户区偏移 -{int(frame[0])},-{int(frame[1])}"
                             f"（截图不含标题栏/边框时用；游戏窗口在跑会按实测值）。")
                files = summary.get("files") or []
                if files:
                    d = os.path.dirname(files[0])
                    lines.append(f"预览完成：识别到 {summary['strokes']} 条笔画。"
                                 f"诊断图已写入：{d}")
                else:
                    lines.append(f"预览完成：识别到 {summary['strokes']} 条笔画（未写出诊断图）。")
                lines.append("打开看：*_mask(前景) / *_skel(骨架) / *_strokes(识别出的笔画) / *_trace(实际走笔)。"
                             "若不对，把 *_strokes 的包围盒或哪几笔错了告诉开发者调阈值。")
                level = "hit" if summary["strokes"] else "warn"
                self.app.after(0, lambda: [self._log_line(m, level if m.startswith("预览完成") else "info")
                                           for m in lines])
            except Exception as e:
                self.app.after(0, lambda: self._log_line(f"离线预览失败：{e}", "error"))

        import threading
        threading.Thread(target=_work, daemon=True).start()

    def _open_tuoying_calibrate(self):
        """打开拓印标定（共享命名空间 tuoying），按 _tuo_cal_dialog 去重。"""
        existing = self._tuo_cal_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            self._tuo_cal_dialog = None
            self.refresh()

        try:
            self._tuo_cal_dialog = CalibrateDialog(self.app, task_name="tuoying", on_done=_after)
        except Exception as e:
            self._tuo_cal_dialog = None
            self.app.toast(f"打开拓印标定失败：{e}")

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止拓印…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        task_cls = get_task(self.TASK_NAME)
        if task_cls is None:
            self._log_line("找不到拓印任务。", "error")
            return
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始拓印：" + p, "error")
            self.runner = None
            return
        self._log_line("开始拓印…（请确认拓印临摹界面已在所选窗口一号里弹出）", "warn")
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def pump(self):
        """被 App._tick 周期调用：抽干日志队列、检测运行结束。"""
        if self.runner:
            for level, msg, _src in self.runner.drain_logs():
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  开始拓印":
                self.btn_run.configure(text="▶  开始拓印", fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, state="normal")

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
