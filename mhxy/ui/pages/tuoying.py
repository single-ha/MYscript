# -*- coding: utf-8 -*-
"""拓印页：全局共享临摹能力的独立入口（原标定在「通用」页「公共区域」，随工具页启用后迁到这里）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.config import TUOYING_TPL_KEYS
from ...core.runner import TaskRunner
from ...tasks import get_task
from ..common import Card, bind_wraplength


class TuoyingPage(ctk.CTkFrame):
    """拓印：刷副本点「进入」偶发的「拓印」临摹弹窗的自动描摹能力，这里可单独标定/演练。
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
                                     "描一遍再点「上传」。这里标一次、所有副本共用；也可以单独「演练」看描摹效果。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    # ---- 控制区：演练按钮 + 工具（标定/刷新配置）----
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
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

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
        sub = ctk.CTkLabel(txt, text="就绪要求：框选好「拓印描摹绘制区」即可自动描摹；「界面标题」「上传按钮」"
                                     "用于自动识别弹窗与收尾，不标=遇拓印弹窗只能转手动临摹。"
"演练只对所选窗口里的第一个号操作：把拓印临摹界面调到前台再点「开始拓印」，"
                                      "脚本会沿图案描一遍（不点上传）。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", anchor="w", pady=(2, 0))
        bind_wraplength(sub)

        ctk.CTkFrame(c, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))

        tip = ctk.CTkLabel(c, text="说明：拓印只由刷副本的队长窗口触发，队员不弹；自动临摹会认图案、描两遍并点上传，"
                                   "是全部副本共用的共享能力。它和整理背包一样是「共享能力」，平时不用特意来这个页——"
                                   "只有首次标定、或想验证描摹手感时才需要这里。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        tip.pack(fill="x", padx=16, pady=(0, 14))
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
        done = sum(1 for k in TUOYING_TPL_KEYS if tpl.get(k))
        ready = area_ok and done == len(TUOYING_TPL_KEYS)
        self.lbl_ready.configure(
            text=f"绘制区：{'● 已框选' if area_ok else '○ 未标定'}；模板 {done}/{len(TUOYING_TPL_KEYS)}"
                 + ("　✓ 已就绪" if ready else "　（需框选绘制区 + 模板）"),
            text_color=T.SUCCESS if ready else T.WARN)

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
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != "▶  开始拓印":
                self.btn_run.configure(text="▶  开始拓印", fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, state="normal")

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))