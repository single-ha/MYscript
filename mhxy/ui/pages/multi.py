# -*- coding: utf-8 -*-
"""多人任务分类页：内嵌 刷副本枢纽 / 抓鬼 等多人玩法页。
顶层放一张「组队设置」卡（已组队/跑完解散，所有多人任务共用一份，存共享 tasks.teaming）。"""

import customtkinter as ctk

from .category import CategoryPage
from .dungeon import DungeonPage
from .zhuagui import ZhuaguiPage
from .. import theme as T
from ..common import Card, TeamSettingsCard, bind_wraplength


class MultiPage(CategoryPage):
    LOG_SOURCE = "多人任务"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("刷副本", DungeonPage),
                  ("抓鬼", ZhuaguiPage)],
            default=0, title="多人任务")
        self._build_shared_settings()

    def _build_shared_settings(self):
        """重排：标题「多人任务」留最顶上(row0)；共用「组队设置」卡(row1)；子标签 seg 移到设置卡下边(row2)；
        body 内容在最下(row3)。seg 不能跨容器重挂（Tk 限制），故在原标题 bar 里新建一个同款、改为挂在页面 row2。"""
        self.body.grid_forget()
        self.top_bar.grid_forget()
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=0)
        self.grid_rowconfigure(2, weight=0)
        self.grid_rowconfigure(3, weight=1)

        # 标题 bar 留着仅放「多人任务」标题（隐藏原 seg，另建一个挂到页面 row2）
        try:
            self._seg.grid_forget()
        except Exception:
            pass
        self.top_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 6))

        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 10))
        card.grid_columnconfigure(1, weight=1)
        lbl = ctk.CTkLabel(card, text="已组队/跑完解散（所有多人任务共用）", font=self.fonts["small"],
                           text_color=T.TEXT_DIM, justify="left")
        lbl.grid(row=0, column=0, sticky="w", padx=16, pady=(10, 4))
        bind_wraplength(lbl)
        self.team_settings = TeamSettingsCard(card, self.app, self.fonts, on_change=self._on_settings_changed)
        self.team_settings.grid(row=1, column=0, sticky="w", padx=16, pady=(0, 10))

        # 子标签「刷副本 / 抓鬼」单独放设置卡下边（右对齐）
        new_seg = ctk.CTkSegmentedButton(
            self, values=self._titles, command=self._switch,
            font=self.fonts["body_b"], corner_radius=T.RADIUS_SM,
            fg_color=T.SURFACE_2, selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER, unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE, text_color=T.TEXT_DIM,
            text_color_disabled=T.TEXT_DIM)
        new_seg.grid(row=2, column=0, sticky="e", padx=4, pady=(0, 10))
        self._seg = new_seg
        # 让新 seg 与当前已显示的 tab 对齐（super 初始化时画的是旧 seg）
        try:
            self._seg.set(self._current_title)
        except Exception:
            pass
        self._paint_seg_selection(self._current_title)
        self.body.grid(row=3, column=0, sticky="nsew")

    def _on_settings_changed(self):
        # 共用设置变了，切当前子页刷新标定状态即可；其余已建子页下次可见再刷。
        for p in self._sub_pages.values():
            if hasattr(p, "refresh"):
                try:
                    p.refresh()
                except Exception:
                    pass

    def refresh(self):
        # 先回灌共用卡（配置可能在别处被改）+ 让已建子页刷新
        try:
            self.team_settings.refresh()
        except Exception:
            pass
        super().refresh()
