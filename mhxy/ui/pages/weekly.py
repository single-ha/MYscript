# -*- coding: utf-8 -*-
"""周常任务分类页：内嵌 门派闯关 / 海底世界 / 迷魂塔 三个周常玩法页。
顶层放一张「组队设置」卡（已组队开关，与「多人任务」页同一份，存共享 tasks.teaming；队长在「通用」页选）。"""

import customtkinter as ctk

from .category import CategoryPage
from .sect_gate import SectGatePage
from .underwater import UnderwaterPage
from .maze_tower import MazeTowerPage
from .. import theme as T
from ..common import Card, TeamSettingsCard, bind_wraplength


class WeeklyPage(CategoryPage):
    LOG_SOURCE = "周常"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("门派闯关", SectGatePage),
                  ("海底世界", UnderwaterPage),
                  ("迷魂塔", MazeTowerPage)],
            default=0, title="周常")
        self._build_shared_settings()

    def _build_shared_settings(self):
        """重排：标题「周常」留最顶上(row0)；共用「组队设置」卡(row1)；子标签 seg 移到设置卡下边(row2)；
        body 内容在最下(row3)。照「多人任务」页同款布局。"""
        self.body.grid_forget()
        self.top_bar.grid_forget()
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=1)

        try:
            self._seg.grid_forget()
        except Exception:
            pass
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 6))

        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 10))
        card.grid_columnconfigure(1, weight=1)
        lbl = ctk.CTkLabel(card, text="组队设置（与「多人任务」页共用一份）· 队长/队长ID 在「通用」页「选择窗口」里选",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        lbl.grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))
        bind_wraplength(lbl)
        self.team_settings = TeamSettingsCard(card, self.app, self.fonts, on_change=self._on_settings_changed)
        self.team_settings.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        new_seg = ctk.CTkSegmentedButton(
            self, values=self._titles, command=self._switch,
            font=self.fonts["body_b"], corner_radius=T.RADIUS_SM,
            fg_color=T.SURFACE_2, selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER, unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE, text_color=T.TEXT_DIM,
            text_color_disabled=T.TEXT_DIM)
        new_seg.grid(row=2, column=0, sticky="e", padx=4, pady=(0, 10))
        self._seg = new_seg
        try:
            self._seg.set(self._current_title)
        except Exception:
            pass
        self._paint_seg_selection(self._current_title)
        self.body.grid(row=3, column=0, sticky="nsew")

    def _on_settings_changed(self):
        for p in self._sub_pages.values():
            if hasattr(p, "refresh"):
                try:
                    p.refresh()
                except Exception:
                    pass

    def refresh(self):
        try:
            self.team_settings.refresh()
        except Exception:
            pass
        super().refresh()