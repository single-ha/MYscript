# -*- coding: utf-8 -*-
"""整理背包页：跨任务共享能力的独立入口（原在「通用」页，随工具页启用后迁到这里）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...tasks.organize_bag import OrganizeBagTask
from ..common import Card, bind_wraplength

# 完成度统计用的模板清单 = 「标定（整理背包）」窗口展示的同一份 spec（唯一事实源，二者永不漂移）。
# 只统计【必需】模板（可选模板第4元=True，如「更多按钮」不参与分数线），避免出现「9/10 却已就绪」。
_CAL_TPLS = [(it[0], it[1], bool(len(it) > 3 and it[3])) for it in
             OrganizeBagTask.CALIBRATION.get("templates", [])]
_REQ_TPLS = [(k, n) for k, n, opt in _CAL_TPLS if not opt]


class OrganizeBagPage(ctk.CTkFrame):
    """整理背包：跨任务共享能力，任何任务流程都可穿插调用；这里作为工具可单独一键运行。
    标定/物品/参数存共享命名空间 tasks.organize_bag。"""

    TASK_NAME = "organize_bag"
    LOG_SOURCE = "整理背包"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._ob_cal_dialog = None        # 「标定（整理背包）」去重槽
        self.btn_action = None            # 「一键整理」按钮（build_body 后填充）

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
        ctk.CTkLabel(bar, text="整理背包", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="翻包裹找到标定的物品，逐个使用 / 丢弃 / 出售；是跨任务共享能力，任何任务流程"
                                     "都可穿插调用，这里可单独一键运行。配置、标定、物品清单都存这份共享命名空间。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：运行按钮 + 工具（选择窗口/刷新），与其它任务页一致 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text="▶  一键整理", font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_organize_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="管理物品", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_organize_items).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

    # ---- 主体：完成度 + 运行说明 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        c = Card(body)
        c.grid(row=0, column=0, sticky="nsew")
        c.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="ew")
        self.lbl_ready = ctk.CTkLabel(txt, text="", font=self.fonts["body"], text_color=T.TEXT_DIM, anchor="w")
        self.lbl_ready.pack(fill="x", anchor="w", pady=(4, 0))
        bind_wraplength(self.lbl_ready)
        sub = ctk.CTkLabel(txt, text="完成度只统计「标定」窗口里列出的模板（不含物品清单）——还没加物品时不会显示"
                                     " 0/0 误以为已就绪。点「管理物品」框选要整理的道具并设动作，点「标定」框选按钮。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", anchor="w", pady=(2, 0))
        bind_wraplength(sub)

    # ------------------------------------------------------------------
    # 刷新 / 渲染
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        ob_tc = cfg_mod.task_config(self.app.cfg, "organize_bag")
        items = ob_tc.get("items", []) or []
        tpl = ob_tc.get("templates", {}) or {}

        # 完成度 = 运行真正需要的【必需】模板已标定的数（可选模板不卡就绪，但单独提示还有几项可标）。
        done = sum(1 for k, _n in _REQ_TPLS if tpl.get(k))
        total = len(_REQ_TPLS)
        missing = [n for _k, n in _REQ_TPLS if not tpl.get(_k)]
        ready = not missing
        opt_missing = [n for k, n, o in _CAL_TPLS if o and not tpl.get(k)]
        if ready:
            tail = "　✓ 已就绪"
            if opt_missing:
                tail = f"（还有 {len(opt_missing)} 项可选项待标定）　✓ 已就绪"
        else:
            tail = "　（还需标定：" + "、".join(missing) + "）"
        self.lbl_ready.configure(
            text=f"物品 {len(items)} 件；动作按钮 {done}/{total} 已标定" + tail,
            text_color=T.SUCCESS if ready else T.WARN)

    def _open_organize_calibrate(self):
        """打开整理背包标定（共享命名空间 organize_bag），按 _ob_cal_dialog 去重。"""
        existing = self._ob_cal_dialog
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
            self._ob_cal_dialog = None
            self.refresh()

        try:
            self._ob_cal_dialog = CalibrateDialog(self.app, task_name="organize_bag", on_done=_after)
        except Exception as e:
            self._ob_cal_dialog = None
            self.app.toast(f"打开整理背包标定失败：{e}")

    def _open_organize_items(self):
        """打开「管理物品」弹窗：增删物品、改每件的动作（使用/丢弃/出售）。关闭后刷新完成度。"""
        from ..inventory_items_dialog import InventoryItemsDialog
        try:
            InventoryItemsDialog(self.app, on_done=self.refresh)
        except Exception as e:
            self.app.toast(f"打开物品管理失败：{e}")

    # ---- 运行控制 ----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止整理…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        # 只读最新配置开跑，运行时直接整理。
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        task_cls = get_task(self.TASK_NAME)
        if task_cls is None:
            self._log_line("找不到整理背包任务。", "error")
            return
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始整理：" + p, "error")
            self.runner = None
            return
        self._log_line("开始一键整理背包…", "hit")
        self.btn_run.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def pump(self):
        """被 App._tick 周期调用：抽干日志队列、检测运行结束。"""
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  一键整理":
                self.btn_run.configure(text="▶  一键整理", fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, state="normal")

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))