# -*- coding: utf-8 -*-
"""刮刮乐「配置/标定」卡：参数改动即保存，标定/重新加载即点即做。
运行唯一入口在「日常」页（tasks.daily 个人组）。由 ConfigPage（任务配置页）统一组装。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...tasks import get_task
from ..common import (Card, bind_wraplength, open_calibrate, param_entry,
                      calib_status, required_templates, optional_templates)


class GuagualeConfig(ctk.CTkFrame):
    TASK_NAME = "guaguale"
    LOG_SOURCE = "刮刮乐"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cal_dialog = None

        card = Card(self)
        card.pack(fill="x", padx=2)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(txt, text="刮刮乐", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text="福利 → 打开刮刮乐 → 刮奖区域刮开 → 有节日奖励就顺手领", font=self.fonts["small"],
                     text_color=T.TEXT_DIM).pack(anchor="w", pady=(2, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(0, 2))

        self.lbl_calib = ctk.CTkLabel(card, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.pack(fill="x", padx=16, pady=(8, 0))
        bind_wraplength(self.lbl_calib)

        self.var_timeout, _ = param_entry(card, self.fonts, "等待超时（秒）", "20", self._on_timeout,
                                          tooltip="等「福利」/「打开刮刮乐」按钮超过这么久就没认到（会跳过该号）")
        self.var_passes, _ = param_entry(card, self.fonts, "刮奖遍数", "2", self._on_passes,
                                         tooltip="刮奖区域来回扫几遍；刮不干净就调大（每多一遍多几秒）")

        ctk.CTkFrame(card, fg_color="transparent", height=14).pack()

        self.refresh()

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loop = tc.get("loop", {})
        self.var_timeout.set(str(loop.get("step_timeout_sec", 20)))
        self.var_passes.set(str(loop.get("scratch_passes", 2)))
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        need_t = required_templates(spec)
        opt_tpl = optional_templates(spec)
        _ready, txt, color = calib_status(regions=tc.get("regions", {}), templates=templates,
                                          task_name=self.TASK_NAME, tpl_keys=need_t, tpl_opt_keys=opt_tpl)
        self.lbl_calib.configure(text=txt, text_color=color)

    # ---- 参数保存（改动即保存）----
    def _on_timeout(self, raw):
        self._save_loop("step_timeout_sec", raw, default=20, minimum=1, cast=round, var=self.var_timeout)

    def _on_passes(self, raw):
        self._save_loop("scratch_passes", raw, default=2, minimum=1, cast=round, var=self.var_passes)

    def _save_loop(self, key, raw, default, minimum, cast, var):
        try:
            val = max(minimum, cast(float(raw)))
        except (TypeError, ValueError):
            val = None
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        if val is None:
            loopc.setdefault(key, default)
            val = loopc[key]
        else:
            loopc[key] = val
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        try:
            var.set(str(val))
        except Exception:
            pass

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")
