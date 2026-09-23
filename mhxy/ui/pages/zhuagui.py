# -*- coding: utf-8 -*-
"""抓鬼「配置/标定」卡：参数改动即保存，标定/重新加载即点即做。
运行唯一入口在「日常」页（tasks.daily 多人组·集体屏障）。由 ConfigPage（任务配置页）统一组装。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...tasks import get_task
from ..common import (Card, bind_wraplength, open_calibrate, param_entry, teaming_ns, teaming_ready,
                      calib_status, required_templates, optional_templates)


class ZhuaguiConfig(ctk.CTkFrame):
    TASK_NAME = "zhuagui"
    LOG_SOURCE = "抓鬼"

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
        ctk.CTkLabel(txt, text="抓鬼", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text="参加→领任务→点条目寻路→自动战斗→领下一轮，队长跑满轮数即停", font=self.fonts["small"],
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

        self.var_rounds, _ = param_entry(card, self.fonts, "抓鬼轮数（领任务次数，跑满即停）", "2", self._on_max_rounds,
                                         tooltip="队长连续领取并完成多少次抓鬼任务就停")

        ctk.CTkFrame(card, fg_color="transparent", height=14).pack()

        self.refresh()

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loopc = tc.get("loop", {})
        self.var_rounds.set(str(loopc.get("max_rounds", 2)))

        team_tc = teaming_ns(self.app.cfg)
        skip_team = team_tc.get("skip_team", False)
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        need_t = required_templates(spec)
        opt_tpl = optional_templates(spec)
        self_ok, calib_txt, _calib_color = calib_status(regions=tc.get("regions", {}), templates=templates,
                                                        task_name=self.TASK_NAME, tpl_keys=need_t,
                                                        tpl_opt_keys=opt_tpl, label="抓鬼标定")
        if skip_team:
            ready = self_ok
            team_line = "已组队：跳过组队（无需组队标定）\n"
        else:
            team_ok = teaming_ready(team_tc)
            ready = self_ok and team_ok
            team_line = "组队标定：" + ("齐全 ✓\n" if team_ok else "区域内/模板未标齐\n")
        self.lbl_calib.configure(text=team_line + calib_txt,
                                 text_color=T.SUCCESS if ready else T.WARN)

    # ---- 参数保存（改动即保存）----
    def _on_max_rounds(self, raw):
        try:
            val = max(1, int(float(raw)))
        except (TypeError, ValueError):
            val = None
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        loopc = tc.setdefault("loop", {})
        if val is None:
            loopc.setdefault("max_rounds", 2)
            val = loopc["max_rounds"]
        else:
            loopc["max_rounds"] = val
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        try:
            self.var_rounds.set(str(val))
        except Exception:
            pass

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")