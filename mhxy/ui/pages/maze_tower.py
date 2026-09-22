# -*- coding: utf-8 -*-
"""迷魂塔页（周常）。薄子类：运行/标定/参数/状态全部在 WeeklyTabPage（mhxy/ui/pages/weekly_tab.py）。"""

from .weekly_tab import WeeklyTabPage


class MazeTowerPage(WeeklyTabPage):
    TASK_NAME = "maze_tower"
    LOG_SOURCE = "迷魂塔"
    RUN_LABEL = "▶  开始迷魂塔"
    PAGE_TITLE = "迷魂塔"
    PAGE_SUB = "周常 · 多人（自动组队，队长跑循环）· 漫无目标时自动中止，判完成自动开新一轮"