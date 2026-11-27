# -*- coding: utf-8 -*-
"""抢非珍品页。独立页面类，挂在「工具 → 商城」分类下（与抢关注并列的卡片入口）。

与抢关注页的区别：本页没有监控清单，改为填「目标限价 + 购买个数 + 演练开关」，
标定走 CalibrateDialog（价格列区域 + 坊/摊刷新标识 + 确认按钮；读价用 OCR，无需框数字）。
"""

from datetime import datetime, timedelta

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ..common import Card, bind_wraplength, Tooltip


class SnipeNonpremiumPage(ctk.CTkFrame):
    TASK_NAME = "snipe_nonpremium"
    LOG_SOURCE = "抢非珍品"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._wait_until = None      # 延迟执行：晚于该时刻才真正启动（None=不在等待）
        self._starting = False       # preflight 后台进行中标志（防重复点击起多个启动线程）
        self._cal_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bar, text="抢非珍品", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(
            bar,
            text="先在游戏里手动打开要抢物品的商品页；脚本循环刷新，价格低于限价就秒买，买够个数自动停止。",
            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：开始按钮 + 工具 + 延迟执行 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text="▶  开始抢非珍品", font=self.fonts["btn"],
                                     height=46, width=220, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="标定 / 价格列", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        # 延迟执行（与抢关注同款）
        sch = ctk.CTkFrame(card, fg_color="transparent")
        sch.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        sched_lbl = ctk.CTkLabel(sch, text="延迟执行", font=self.fonts["body"], text_color=T.TEXT)
        sched_lbl.pack(side="left")
        self.var_delay_on = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(sch, text="", variable=self.var_delay_on, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_delay_toggle).pack(side="left", padx=(8, 0))
        self.var_delay_hours = ctk.StringVar(value="0")
        ctk.CTkEntry(sch, textvariable=self.var_delay_hours, width=52,
                     font=self.fonts["body"], fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(sch, text="小时", font=self.fonts["body"], text_color=T.TEXT_DIM).pack(side="left", padx=(4, 0))
        self.var_delay_minutes = ctk.StringVar(value="0")
        ctk.CTkEntry(sch, textvariable=self.var_delay_minutes, width=52,
                     font=self.fonts["body"], fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(6, 0))
        ctk.CTkLabel(sch, text="分钟", font=self.fonts["body"], text_color=T.TEXT_DIM).pack(side="left", padx=(4, 0))
        Tooltip(sched_lbl, "开启后，点「开始」不会立即执行，而是先等设定时长，到点才开始。等待中再点一次按钮可取消。",
                self.fonts)

    # ---- 主体：参数卡（限价/个数/演练）+ 标定提示 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        param = Card(body)
        param.grid(row=0, column=0, sticky="ew", padx=(0, 7))
        param.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(param, text="抢货参数", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 8))

        self.var_max_price = ctk.StringVar(value="0")
        self.var_target_count = ctk.StringVar(value="1")
        self.var_dry_run = ctk.BooleanVar(value=False)

        self._param_row(param, 1, "目标限价", self.var_max_price,
                        "刷出的价格【严格低于】此值才下单（游戏内货币整数）。例如填 50000，则只买 49999 及以下。")
        self._param_row(param, 2, "购买个数", self.var_target_count,
                        "成功下单（点到确认购买）累计达到这个数就自动停止。")

        # 演练开关
        sw_row = ctk.CTkFrame(param, fg_color="transparent")
        sw_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=16, pady=(8, 6))
        ctk.CTkLabel(sw_row, text="演练模式", font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        ctk.CTkSwitch(sw_row, text="", variable=self.var_dry_run, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(sw_row, text="开=只读价、打日志，不真正下单（先验证识别准不准）",
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(side="left", padx=(8, 0))

        # 提示卡
        tip = Card(body)
        tip.grid(row=1, column=0, sticky="nsew", padx=(0, 7), pady=(14, 0))
        tip.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tip, text="使用步骤", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))
        tips = (
            "1. 在侧栏「选择窗口」里选好号，并指定【队长】单选：本任务只在队长号执行（不是号1），"
            "不会多开轮转。\n"
            "2. 游戏里打开要抢物品的【商品在售列表页】（本任务不自动进商城、不切页）。\n"
            "3. 点「标定 / 价格列」：框好价格数字所在的一整列，顺手标好「坊/摊」刷新标识与（可选的）"
            "确认购买按钮。读价已改用 OCR，无需再逐个框选数字 0~9。\n"
            "4. 填目标限价和购买个数，建议先开演练模式跑几轮，看日志读出的价格对不对；"
            "读不出时日志会给出 OCR 诊断图路径。\n"
            "5. 确认无误后关掉演练，点开始：脚本按「坊→摊」循环刷新，发现低于限价的最低价那行就秒买。"
        )
        lbl = ctk.CTkLabel(tip, text=tips, font=self.fonts["body"], text_color=T.TEXT_DIM,
                           justify="left", anchor="w")
        lbl.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 14))
        bind_wraplength(lbl)

    def _param_row(self, parent, row, label, var, tip):
        ctk.CTkLabel(parent, text=label, font=self.fonts["body"], text_color=T.TEXT).grid(
            row=row, column=0, sticky="w", padx=(16, 8), pady=8)
        entry = ctk.CTkEntry(parent, textvariable=var, width=140,
                             font=self.fonts["body"], fg_color=T.SURFACE_2, border_color=T.BORDER)
        entry.grid(row=row, column=1, sticky="w", pady=8)
        Tooltip(entry, tip, self.fonts)

    # ------------------------------------------------------------------
    # 数据刷新 / 参数保存
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loopc = tc.get("loop", {}) or {}
        if getattr(self, "var_max_price", None) is not None:
            self.var_max_price.set(str(loopc.get("max_price", 0) or 0))
            self.var_target_count.set(str(loopc.get("target_count", 1) or 1))
            self.var_dry_run.set(bool(loopc.get("dry_run", False)))
            dh = loopc.get("delay_hours", 0) or 0
            dm = loopc.get("delay_minutes", 0) or 0
            self.var_delay_on.set(bool(dh) or bool(dm))
            self.var_delay_hours.set(str(dh))
            self.var_delay_minutes.set(str(dm))

    def _save_params(self):
        """把页面上的限价/个数/演练/延迟写回 tasks.snipe_nonpremium.loop 并落盘。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})

        def _int(var, default, lo=0, hi=10 ** 9):
            try:
                return max(lo, min(hi, int(var.get() or default)))
            except (ValueError, TypeError):
                return default

        loopc["max_price"] = _int(self.var_max_price, 0)
        loopc["target_count"] = max(1, _int(self.var_target_count, 1, lo=1, hi=10 ** 6))
        loopc["dry_run"] = bool(self.var_dry_run.get())

        dh = _int(self.var_delay_hours, 0, 0, 720)
        dm = _int(self.var_delay_minutes, 0, 0, 59)
        if not self.var_delay_on.get():
            dh, dm = 0, 0
        loopc["delay_hours"], loopc["delay_minutes"] = dh, dm

        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_delay_toggle(self):
        self._save_params()
        if self.var_delay_on.get():
            self._log_line("已开启延迟执行：点开始会先等设定时长后才真正执行。", "warn")
        else:
            self._log_line("已关闭延迟执行：点开始立即执行。", "info")

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if getattr(self, "_starting", False):
            return
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        if self._wait_until is not None:
            self._wait_until = None
            self.btn_run.configure(text="▶  开始抢非珍品", fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER, state="normal")
            self._log_line("已取消延迟等待，本次不执行。", "warn")
            return
        self._save_params()
        cfg = self.app.cfg
        loopc = (cfg_mod.task_config(cfg, self.TASK_NAME).get("loop", {}) or {})
        total_min = (loopc.get("delay_hours", 0) or 0) * 60 + (loopc.get("delay_minutes", 0) or 0)
        if total_min > 0:
            dh, dm = loopc.get("delay_hours", 0), loopc.get("delay_minutes", 0)
            self._wait_until = datetime.now() + timedelta(minutes=total_min)
            self._log_line(f"已设延迟执行：{dh:.0f} 小时 {dm:.0f} 分钟（约到 {self._wait_until:%H:%M:%S}）后开始（再点一次可取消）。",
                           "warn")
            self._update_wait_button()
            return
        self._start_run()

    def _start_run(self):
        # 异步启动：preflight 含 select_windows/locate_all 的 Win32 窗口枚举，目标窗口无响应时
        # 可能阻塞，放后台线程做，结论经 ui_call 回主线程，避免冻住整窗（延迟到点卡死的根因）。
        if getattr(self, "_starting", False):
            return
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        self._starting = True
        self.btn_run.configure(text="启动中…", state="disabled")

        def on_done(ok, problems):
            self._starting = False
            if not ok:
                for p in problems:
                    self._log_line("无法启动：" + p, "error")
                self.runner = None
                self.btn_run.configure(text="▶  开始抢非珍品", fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, state="normal")
                return
            self.btn_run.configure(text="■  停止", fg_color=T.DANGER,
                                   hover_color=T.DANGER_HOVER, state="normal")

        self.runner.start_async(on_done, ui_marshal=self.app.ui_call)

    @staticmethod
    def _fmt_remaining(secs):
        secs = max(0, int(secs))
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}小时{m}分"
        if m:
            return f"{m}分{s}秒"
        return f"{s}秒"

    def _update_wait_button(self):
        if self._wait_until is None or self.runner and self.runner.is_running():
            return
        rem = (self._wait_until - datetime.now()).total_seconds()
        if rem <= 0:
            return
        text = f"延迟 {self._fmt_remaining(rem)} 后开始…（再点取消）"
        if self.btn_run.cget("text") != text:
            self.btn_run.configure(text=text, fg_color=T.WARN, hover_color=T.WARN_HOVER, state="normal")

    def _on_runner_finished(self):
        self.btn_run.configure(text="▶  开始抢非珍品", fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _open_calibrate(self):
        if getattr(self, "_cal_dialog", None) is not None:
            try:
                if self._cal_dialog.winfo_exists():
                    self._cal_dialog.lift()
                    self._cal_dialog.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            self._cal_dialog = None
            self.refresh()
            self._log_line("标定完成，配置已更新。", "info")

        try:
            self._cal_dialog = CalibrateDialog(self.app, task_name=self.TASK_NAME, on_done=_after)
        except Exception as e:
            self._cal_dialog = None
            self._log_line(f"打开标定向导失败：{e}", "error")

    # ------------------------------------------------------------------
    # 日志与状态（由 App 定时器驱动）
    # ------------------------------------------------------------------
    def pump(self):
        if self.runner:
            for level, msg, _src in self.runner.drain_logs():
                self._log_line(msg, level)
            if (not self.runner.is_running() and not self.runner.is_starting()
                    and self.btn_run.cget("text") != "▶  开始抢非珍品"):
                self._on_runner_finished()
        elif self._wait_until is not None:
            if datetime.now() >= self._wait_until:
                self._wait_until = None
                self._log_line("⏰ 延迟时间到，开始抢非珍品…", "warn")
                self._start_run()
            else:
                self._update_wait_button()

    def stop_pending(self):
        if self._wait_until is not None:
            self._wait_until = None
            self.btn_run.configure(text="▶  开始抢非珍品", fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER, state="normal")

    def _log_line(self, msg, level="info"):
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
