# -*- coding: utf-8 -*-
"""刷副本「配置/标定」卡：副本共用一套标定（区域/模板/loop 全存共享 tasks.dungeon），
参数改动即保存。要刷哪些副本在「日常」页「刷副本」步勾选（共享 tasks.dungeon.selected），
运行也只在「日常」页触发（tasks.daily 多人组·集体屏障）。由 ConfigPage（任务配置页）统一组装。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...tasks.dungeon_base import DUNGEON_CALIBRATION
from ..common import (Card, bind_wraplength, open_calibrate,
                      teaming_ns, teaming_ready, _row_optional, calib_status,
                      required_regions, required_templates, optional_templates)


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

        self.lbl_team = ctk.CTkLabel(card, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_team.pack(fill="x", padx=16, pady=(0, 0))
        bind_wraplength(self.lbl_team)

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
        """共享 tasks.dungeon 的标定计数（一次标定所有副本）。
        返回 (rdone, rtot, ropt, tdone, ttot, topt_done, topt_total)：
        必标项 (done/total) + 可选项 (done/total)。
        「刷副本」的标定 spec 是共享 DUNGEON_CALIBRATION（一次标定所有副本），
        get_task("dungeon") 是仅组队的 DungeonTask、无标定 spec，不能用它。"""
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        spec = DUNGEON_CALIBRATION
        rrows = [(t[0], _row_optional(t)) for t in (spec.get("regions") or [])]
        trows = [(t[0], _row_optional(t)) for t in (spec.get("templates") or [])]

        def cat(raw, rows):
            done = sum(1 for k, opt in rows if not opt and raw.get(k))
            opt_done = sum(1 for k, opt in rows if opt and raw.get(k))
            opt_total = len([1 for k, opt in rows if opt])
            opt_miss = opt_total - opt_done
            return done, len([1 for k, opt in rows if not opt]), opt_done, opt_total

        rdone, rtot, ropt, _ = cat(regions, rrows)
        tdone, ttot, topt_done, topt_total = cat(templates, trows)
        return rdone, rtot, ropt, tdone, ttot, topt_done, topt_total

    @staticmethod
    def _calib_line(rdone, rtot, ropt, tdone, ttot, topt_done, topt_total):
        """Standard format for calibration status line (new format):
        "necessary_area: x/X, necessary_template: m/M, optional_template: n/N" (items with zero total not shown)"""
        parts = []
        if rtot:
            parts.append(f"必要区域：{rdone}/{rtot}")
        if ttot:
            parts.append(f"必要模板：{tdone}/{ttot}")
        if topt_total:
            parts.append(f"可选模板：{topt_done}/{topt_total}")
        return "，".join(parts) if parts else "无标定"

    def _refresh_ready(self):
        # 获取标定计数和所需变量
        rdone, rtot, ropt, tdone, ttot, topt_done, topt_total = self._required_counts()
        
        # 重新获取 regions、templates 等变量（用于 calib_status）
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})
        spec = DUNGEON_CALIBRATION
        req_regions = required_regions(DUNGEON_CALIBRATION)
        req_templates = required_templates(DUNGEON_CALIBRATION)

        regions_ok = rdone == rtot
        templates_ok = tdone == ttot
        team_tc = teaming_ns(self.app.cfg)
        skip_team = team_tc.get("skip_team", False)

        # 任务标定（与单人任务卡片格式一致:"刷副本标定：必要区域：x/X，必要模板：m/M，可选模板：n/N"）
        task_ready, task_text, task_color = calib_status(
            regions=tc.get("regions", {}),
            templates=tc.get("templates", {}),
            task_name=self.TASK_NAME,
            region_keys=req_regions,
            tpl_keys=req_templates,
            tpl_opt_keys=optional_templates(DUNGEON_CALIBRATION),
            label="刷副本标定"
        )
        self.lbl_ready.configure(text=task_text, text_color=task_color)

        # 隐藏组队标定标签
        self.lbl_team.configure(text="", text_color=T.TEXT_DIM)

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
                txt += "（点左下角连接状态按钮「未检测到目标窗口」选择多开）"
            self.lbl_wins.configure(text=txt)
        except Exception:
            pass

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")
