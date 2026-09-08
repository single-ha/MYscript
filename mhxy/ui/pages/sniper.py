# -*- coding: utf-8 -*-
"""秒装备页。独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ..common import Card, Pill, load_thumb, bind_wraplength


class SniperPage(ctk.CTkFrame):
    TASK_NAME = "sniper"
    LOG_SOURCE = "秒装备"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._cal_dialog = None
        self._thumbs = []  # 防止缩略图被 GC

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部：标题 + 状态 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(bar, text="秒装备", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")

        sub = ctk.CTkLabel(bar, text="盯市场列表，目标装备一出现立刻秒下单",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：三段式（运行按钮 + 横排工具 / 分隔线 / 模式开关），与其它任务页一致 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        # 第一行：开始按钮（左） + 工具按钮（右，横排）
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text="▶  开始秒装备", font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="标定 / 加装备", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

    # ---- 主体：监控清单（铺满；日志已移到全局右栏）----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
        body.grid_rowconfigure(0, weight=1)

        # 监控清单卡片
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="监控清单", font=self.fonts["h2"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_count = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.lbl_count.grid(row=0, column=1, sticky="e")
        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

    # ------------------------------------------------------------------
    # 数据刷新
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        # 清单
        self._render_watchlist(tc.get("watchlist", []))

    def _render_watchlist(self, items):
        # 内容没变就别重建：切页时 refresh 会反复调到这里，整段拆了重画是「切页卡顿」的来源之一。
        sig = [(it.get("name"), it.get("template"), it.get("max_price")) for it in items]
        if sig == getattr(self, "_wl_sig", None):
            return
        self._wl_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._thumbs.clear()
        self.lbl_count.configure(text=f"{len(items)} 件")

        if not items:
            empty = ctk.CTkLabel(self.list_frame, text="还没有要抢的装备。\n点上方「标定 / 加装备」添加。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=20)
            bind_wraplength(empty)
            return

        for i, it in enumerate(items):
            row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
            row.grid_columnconfigure(1, weight=1)

            thumb = self._load_thumb(it.get("template"))
            if thumb is not None:
                ctk.CTkLabel(row, text="", image=thumb).grid(row=0, column=0, padx=(10, 8), pady=8)
            else:
                ctk.CTkLabel(row, text="🗡", font=self.fonts["h2"]).grid(row=0, column=0, padx=(10, 8), pady=8)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.grid(row=0, column=1, sticky="w")
            ctk.CTkLabel(info, text=it.get("name", "?"), font=self.fonts["body_b"],
                         text_color=T.TEXT).pack(anchor="w")
            price = it.get("max_price")
            ptxt = "不限价（命中即抢）" if price is None else f"参考价 ≤ {price}"
            ctk.CTkLabel(info, text=ptxt, font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w")

            ctk.CTkButton(row, text="删除", font=self.fonts["small"], height=28, width=52,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda idx=i: self._delete_item(idx)).grid(row=0, column=2, padx=10)

    def _load_thumb(self, template_rel):
        # 逻辑已抽成模块级 load_thumb（多页共享）；保留本方法名兼容现有调用。
        return load_thumb(template_rel, self._thumbs)

    def _delete_item(self, idx):
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        wl = tc.get("watchlist", [])
        if 0 <= idx < len(wl):
            removed = wl.pop(idx)
            cfg_mod.set_task_config(self.app.cfg, self.TASK_NAME, tc)
            cfg_mod.save_config(self.app.cfg)
            self._log_line(f"已删除监控：{removed.get('name','?')}", "info")
            self._render_watchlist(wl)

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 启动
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

    def _on_runner_finished(self):
        self.btn_run.configure(text="▶  开始秒装备", fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    def _open_calibrate(self):
        # 全程在 GUI 内完成，不再开黑窗子进程
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
    # 日志与状态（由 App 的定时器驱动）
    # ------------------------------------------------------------------
    def pump(self):
        """被 App._tick 周期调用：抽干日志队列、检测运行结束、刷新游戏连接药丸。"""
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  开始秒装备":
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
