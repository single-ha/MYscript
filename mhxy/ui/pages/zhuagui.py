# -*- coding: utf-8 -*-
"""抓鬼页（组队可跳过，队长跑 N 轮抓鬼循环）。独立页面类，由 MultiPage tab 内嵌。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ...tasks import get_task
from ..common import Card, bind_wraplength, teaming_ns, teaming_ready, calib_status, required_regions, required_templates, optional_templates


class ZhuaguiPage(ctk.CTkFrame):
    TASK_NAME = "zhuagui"
    LOG_SOURCE = "抓鬼"
    RUN_LABEL = "▶  开始抓鬼"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="抓鬼", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="参加→领取抓鬼任务→点任务条目寻路→自动战斗→点领下一轮，由队长跑满设定轮数即停（可跳过组队/支持多开）",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="抓鬼设置", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        # 轮数（领任务次数）
        cnt = ctk.CTkFrame(left, fg_color="transparent")
        cnt.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(cnt, text="抓鬼轮数（领任务次数，跑满即停）", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_rounds = ctk.StringVar(value="2")
        ctk.CTkEntry(cnt, textvariable=self.var_rounds, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        # 队长与「已组队/跑完解散」统一在「通用」页设置（所有多人任务共用，存 tasks.teaming）
        hint = ctk.CTkLabel(left, text="队长与「已组队/跑完解散」都在「通用」页统一设置（其余号自动当队员），跑完 N 轮即停。\n"
                               "所有多人任务共用同一份组队设置（存 tasks.teaming）。\n"
                               "「已组队」勾上=已自行组好队，直接由队长开抓、不再组队（此时不需要组队标定）。\n"
                               "组队的模板/区域在「通用」页统一标定；抓鬼自身模板用本页「标定」按钮。\n"
                               "鼠标甩到屏幕角落可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loopc = tc.get("loop", {})
        self.var_rounds.set(str(loopc.get("max_rounds", 2)))

        team_tc = teaming_ns(self.app.cfg)
        skip_team = team_tc.get("skip_team", False)
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        need_r = required_regions(spec)   # 抓鬼自身无区域；公共区域（活动列表）在「通用」页统一标定
        need_t = required_templates(spec)
        opt_tpl = optional_templates(spec)
        self_ok, calib_txt, _calib_color = calib_status(regions=regions, templates=templates,
                                                        task_name=self.TASK_NAME, region_keys=need_r, tpl_keys=need_t,
                                                        tpl_opt_keys=opt_tpl, label="抓鬼标定")
        if skip_team:
            ready = self_ok
            team_line = "已组队：本次跳过组队（无需组队标定）\n"
        else:
            team_ok = teaming_ready(team_tc)
            ready = self_ok and team_ok
            team_line = "组队标定：" + (
                "齐全 ✓" if team_ok else "区域内/模板未标齐（去「通用」页标定组队）") + "\n"
        self.lbl_calib.configure(text=team_line + calib_txt,
                                 text_color=T.SUCCESS if ready else T.WARN)

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        self._apply_params()
        self.app.cfg = cfg_mod.load_config()
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _apply_params(self):
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        try:
            loopc["max_rounds"] = max(1, int(float(self.var_rounds.get())))
        except (TypeError, ValueError):
            pass
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_runner_finished(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
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

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()