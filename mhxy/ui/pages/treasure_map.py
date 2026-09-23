# -*- coding: utf-8 -*-
"""宝图「配置/标定」卡：参数改动即保存，标定/重新加载即点即做。
运行唯一入口在「日常」页（tasks.daily 个人组）。由 ConfigPage（任务配置页）统一组装。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...tasks import get_task
from ..common import (Card, bind_wraplength, open_calibrate, param_entry,
                      calib_status, required_regions, required_templates, optional_templates)


class TreasureMapConfig(ctk.CTkFrame):
    TASK_NAME = "treasure_map"
    LOG_SOURCE = "宝图"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cal_dialog = None

        card = Card(self)
        card.pack(fill="x", padx=2)

        # 标题 + 操作按钮
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(txt, text="宝图", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text="开活动→收图→挖宝→领奖", font=self.fonts["small"],
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

        self.var_time, _ = param_entry(card, self.fonts, "时间上限（分钟）", "30", self._on_time_limit,
                                       tooltip="跑满这么久即收尾停（到点自动领完当前一张图）。0=不限时")
        self.var_still, _ = param_entry(card, self.fonts, "静止判定（秒）", "8", self._on_still_diff,
                                        tooltip="挖宝后画面静止超过这么多秒判定挖完（模板总是能认，此值保守点即可）")

        ctk.CTkFrame(card, fg_color="transparent", height=14).pack()

        self.refresh()

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loopc = tc.get("loop", {})
        self.var_time.set(str(loopc.get("time_limit_min", 30)))
        self.var_still.set(str(loopc.get("still_diff", 8)))
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        need_r = required_regions(spec)
        need_t = required_templates(spec)
        opt_tpl = optional_templates(spec)
        _ready, txt, color = calib_status(regions=regions, templates=templates, task_name=self.TASK_NAME,
                                          region_keys=need_r, tpl_keys=need_t, tpl_opt_keys=opt_tpl, label="宝图标定")
        self.lbl_calib.configure(text=txt, text_color=color)

    # ---- 参数保存（改动即保存）----
    def _on_time_limit(self, raw):
        self._save_loop(raw, "time_limit_min", lambda v: max(0, int(float(v))), "30", self.var_time)

    def _on_still_diff(self, raw):
        self._save_loop(raw, "still_diff", lambda v: max(0.5, float(v)), "8", self.var_still)

    def _save_loop(self, raw, key, clamp, default, var):
        try:
            val = clamp(raw)
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