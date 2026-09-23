# -*- coding: utf-8 -*-
"""帮派签到「配置/标定」卡：参数改动即保存，标定/重新加载即点即做。
运行唯一入口在「日常」页（tasks.daily 个人组）。由 ConfigPage（任务配置页）统一组装。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...tasks import get_task
from ..common import (Card, bind_wraplength, open_calibrate, param_entry,
                      calib_status, required_templates, optional_templates)


class GuildCheckinConfig(ctk.CTkFrame):
    TASK_NAME = "guild_checkin"
    LOG_SOURCE = "帮派签到"

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
        ctk.CTkLabel(txt, text="帮派签到", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text="打开帮派→福利页签→签到", font=self.fonts["small"],
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

        self.var_timeout, _ = param_entry(card, self.fonts, "等待超时（秒）", "30", self._on_timeout,
                                          tooltip="等「福利」页签/「签到」按钮超过这么久就跳过当前号继续下一个")

        ctk.CTkFrame(card, fg_color="transparent", height=14).pack()

        self.refresh()

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        self.var_timeout.set(str(tc.get("loop", {}).get("step_timeout_sec", 30)))
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        need_t = required_templates(spec)
        opt_tpl = optional_templates(spec)
        _ready, txt, color = calib_status(regions=tc.get("regions", {}), templates=templates,
                                          task_name=self.TASK_NAME, tpl_keys=need_t, tpl_opt_keys=opt_tpl)
        self.lbl_calib.configure(text=txt, text_color=color)

    # ---- 参数保存（改动即保存）----
    def _on_timeout(self, raw):
        try:
            val = max(1, round(float(raw)))
        except (TypeError, ValueError):
            val = None
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        if val is None:
            loopc.setdefault("step_timeout_sec", 30)
            val = loopc["step_timeout_sec"]
        else:
            loopc["step_timeout_sec"] = val
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        try:
            self.var_timeout.set(str(val))
        except Exception:
            pass

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")