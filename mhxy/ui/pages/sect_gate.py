# -*- coding: utf-8 -*-
"""门派闯关页（周常）。薄子类：运行/标定/参数/状态全部在 WeeklyTabPage（mhxy/ui/pages/weekly_tab.py）。"""

from .weekly_tab import WeeklyTabPage


class SectGatePage(WeeklyTabPage):
    TASK_NAME = "sect_gate"
    LOG_SOURCE = "门派闯关"
    RUN_LABEL = "▶  开始门派闯关"
    PAGE_TITLE = "门派闯关"
    PAGE_SUB = "周常 · 多人（自动组队，队长跑循环）· 漫无目标时自动中止，判完成自动开新一轮"