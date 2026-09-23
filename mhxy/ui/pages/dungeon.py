# -*- coding: utf-8 -*-
"""刷副本「配置/标定」卡：副本共用一套标定（区域/模板/loop 全存共享 tasks.dungeon），
参数改动即保存。要刷哪些副本在「日常」页「刷副本」步勾选（共享 tasks.dungeon.selected），
运行也只在「日常」页触发（tasks.daily 多人组·集体屏障）。由 ConfigPage（任务配置页）统一组装。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...tasks import get_task
from ..common import (Card, bind_wraplength, open_calibrate,
                      teaming_ns, teaming_ready, _row_optional)


class DungeonConfig(ctk.CTkFrame):
    TASK_NAME = "dungeon"
    LOG_SOURCE = "刷副本"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cal_dialog = None
        self._windows_pending = False
        self._windows_tokens = 0

        card = Card(self)
        card.pack(fill="x", padx=2)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(txt, text="刷副本", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text="副本共用一套标定；勾选与排队运行在「日常」页", font=self.fonts["small"],
                     text_color=T.TEXT_DIM).pack(anchor="w", pady=(2, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        ctk.CTkButton(btns, text="标定（副本共用）", font=self.fonts["body"], height=36, width=132,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(0, 2))

        self.lbl_wins = ctk.CTkLabel(card, text="🎮 已选中 ——", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                     justify="left")
        self.lbl_wins.pack(fill="x", padx=16, pady=(8, 0))
        bind_wraplength(self.lbl_wins)

        self.lbl_ready = ctk.CTkLabel(card, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_ready.pack(fill="x", padx=16, pady=(2, 0))
        bind_wraplength(self.lbl_ready)

        hint = ctk.CTkLabel(card, text="多副本按勾选顺序一一刷完；某副本异常自动跳过继续下一个。"
                                       "「已组队」在顶部组队设置里勾（勾上=跳过组队直接由队长开刷，无需组队标定）。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", padx=16, pady=(6, 0))
        bind_wraplength(hint)

        ctk.CTkFrame(card, fg_color="transparent", height=12).pack()

        self.refresh()

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        self._refresh_ready()
        self._kick_count_windows(quiet=True)

    def _required_counts(self):
        """共享 tasks.dungeon 的必标区域/模板计数 (rdone, rtot, tdone, ttot)。"""
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        spec = getattr(get_task(self.TASK_NAME), "CALIBRATION", None) or {}
        rkey = [t[0] for t in spec.get("regions", []) if not _row_optional(t)]
        tkey = [t[0] for t in spec.get("templates", []) if not _row_optional(t)]
        rdone = sum(1 for k in rkey if regions.get(k))
        tdone = sum(1 for k in tkey if templates.get(k))
        return rdone, len(rkey), tdone, len(tkey)

    def _refresh_ready(self):
        rdone, rtot, tdone, ttot = self._required_counts()
        regions_ok = rdone == rtot
        templates_ok = tdone == ttot
        team_tc = teaming_ns(self.app.cfg)
        skip_team = team_tc.get("skip_team", False)
        line = f"标定：区域 {rdone}/{rtot}，模板 {tdone}/{ttot}"
        if skip_team:
            line += "　✓ 就绪（已组队，跳过组队）" if (regions_ok and templates_ok) else "　（还需标定）"
            ready = regions_ok and templates_ok
        else:
            team_ok = teaming_ready(team_tc)
            line += "；组队标定：" + ("齐全 ✓" if team_ok else "未齐（去「通用」页标定组队）")
            ready = regions_ok and templates_ok and team_ok
            line += "　✓ 就绪" if ready else "　（还需标定）"
        self.lbl_ready.configure(text=line, text_color=T.SUCCESS if ready else T.WARN)

    def _kick_count_windows(self, quiet=False):
        """后台线程按 targets 选窗并统计已选中号数（枚举走 core.window，不占主线程），供状态行显示。quiet: 刷新页签时静默。"""
        if self._windows_pending:
            return
        targets = self.app.cfg.get("targets", {})
        if not targets.get("multi"):
            self._set_wins_line(1 if targets else 0, quiet)
            return
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        self._windows_tokens += 1
        token = self._windows_tokens
        self._windows_pending = True

        def work():
            try:
                wins = win_mod.resolve_targets(title, offset, targets)
                out = []
                for w in wins:
                    w._multi_target = True
                    if w.locate():
                        out.append(w)
                return out
            except Exception:
                return []

        def done(results):
            if token != self._windows_tokens:
                return
            self._windows_pending = False
            wins = [r for r in results if r is not None]
            try:
                self.app.game_win.multi_windows = wins
            except Exception:
                pass
            self._set_wins_line(len(wins), quiet)

        threading.Thread(target=lambda: done(work()), daemon=True).start()

    def _set_wins_line(self, n, quiet):
        try:
            txt = f"🎮 已选中 {n} 个号"
            if n == 0 and not quiet:
                txt += "（去「通用」页「选择窗口/队长」勾选多开）"
            self.lbl_wins.configure(text=txt)
        except Exception:
            pass

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")