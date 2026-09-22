# -*- coding: utf-8 -*-
"""
门派闯关任务（周常）。

薄子类：全部逻辑在 WeeklyBaseTask（mhxy/tasks/weekly_base.py）：
多人任务先自动组队、组好后只驱动队长窗口，循环跑「领任务 → 做任务 → 判完成 → 再开新一轮」。
本类只写名称/标题——「参加活动」NPC 对话框按钮等模板在页面标定向导里各自框选。
"""

from .weekly_base import WeeklyBaseTask
from .base import register  # noqa: F401


@register
class SectGateTask(WeeklyBaseTask):
    name = "sect_gate"
    title = "门派闯关"
    description = "周常：门派闯关（多人组队，队长跑循环，漫无目标时自动中止）"