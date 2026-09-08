# -*- coding: utf-8 -*-
"""宝图页（开活动→收图→挖宝→领奖）。独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ..common import Card, bind_wraplength, shared_region_hint


class TreasureMapPage(ctk.CTkFrame):
    TASK_NAME = "treasure_map"
    LOG_SOURCE = "宝图"
    RUN_LABEL = "▶  开始刷宝图"

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
        ctk.CTkLabel(bar, text="宝图", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="自动开活动→收藏宝图→挖宝→领奖，战斗交给游戏自动（支持多开逐号轮转）",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        # 第一行：开始按钮（左） + 标定/刷新（右）
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

        # 分隔线
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
        body.grid_rowconfigure(0, weight=1)

        # 左：运行参数 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="运行参数", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        lim = ctk.CTkFrame(left, fg_color="transparent")
        lim.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(lim, text="时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_limit = ctk.StringVar(value="30")
        ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        sd = ctk.CTkFrame(left, fg_color="transparent")
        sd.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 6))
        ctk.CTkLabel(sd, text="静止判定阈值(收集/挖宝完成)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.var_still = ctk.StringVar(value="8")
        ctk.CTkEntry(sd, textvariable=self.var_still, width=70, font=self.fonts["body"],
                     fg_color=T.SURFACE_2, border_color=T.BORDER).pack(side="left", padx=(8, 0))

        hint = ctk.CTkLabel(left, text="主终止条件是背包藏宝图挖空；时间上限只是安全网。\n"
                               "“静止判定阈值”太小会一直判不到收集完成→看日志里的实时“帧差”，"
                               "把阈值设到“静止时帧差”之上、“走动时帧差”之下。\n"
                               "鼠标甩到屏幕左上角可紧急停止。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loopc = tc.get("loop", {})
        self.var_limit.set(str(loopc.get("time_limit_min", 30)))
        self.var_still.set(str(loopc.get("still_diff", 8.0)))
        # 标定完成度概览：是否已有宝图运行期自动判，故阶段A(入口/参加/听听无妨)与阶段B(下一张/藏宝图)都要
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        need_r = ["scene", "activity_list", "bag_list"]
        need_t = ["flag_treasure_entry", "flag_join", "flag_tingting",
                  "flag_next_map", "treasure_item"]
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        self.lbl_calib.configure(
            text=f"标定：必要区域 {rdone}/{len(need_r)}，必要模板 {tdone}/{len(need_t)}"
                   + ("　✓ 可运行" if rdone == len(need_r) and tdone == len(need_t) else "　（还需标定）")
                   + shared_region_hint(regions, self.TASK_NAME))

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动前把时间上限写回配置
        self._apply_time_limit()
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

    def _apply_time_limit(self):
        """启动前把「运行参数」里可调项（时间上限 / 静止判定阈值）写回配置。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        try:
            loopc["still_diff"] = max(0.5, round(float(self.var_still.get()), 1))
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
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
