# -*- coding: utf-8 -*-
"""海底世界页（周常）。薄子类：运行/标定/参数/状态全部在 WeeklyTabPage（mhxy/ui/pages/weekly_tab.py）。"""

from .weekly_tab import WeeklyTabPage


class UnderwaterPage(WeeklyTabPage):
    TASK_NAME = "underwater"
    LOG_SOURCE = "海底世界"
    RUN_LABEL = "▶  开始海底世界"
    PAGE_TITLE = "海底世界"
    PAGE_SUB = "周常 · 多人（自动组队，队长跑循环）· 漫无目标时自动中止，判完成自动开新一轮"