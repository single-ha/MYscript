# -*- coding: utf-8 -*-
"""
迷魂塔任务（周常）。

薄子类：全部逻辑在 WeeklyBaseTask（mhxy/tasks/weekly_base.py）：
多人任务先自动组队、组好后只驱动队长窗口，循环跑「领任务 → 做任务 → 判完成 → 再开新一轮」。
本类只写名称/标题——「参加活动」NPC 对话框按钮等模板在页面标定向导里各自框选。
"""

from ..core import vision
from .weekly_base import WeeklyBaseTask, without_templates
from .base import register  # noqa: F401


@register
class MazeTowerTask(WeeklyBaseTask):
    name = "maze_tower"
    title = "迷魂塔"
    description = "周常：迷魂塔（多人组队，队长跑循环，漫无目标时自动中止）"
    NEEDS_CLAIM_TASK = False

    # 基于父类去掉 claim_task，再追加 leave_maze_tower（仅迷魂塔用）
    _BASE_WO_CLAIM = without_templates(WeeklyBaseTask.CALIBRATION, "claim_task")
    CALIBRATION = dict(_BASE_WO_CLAIM)
    CALIBRATION["templates"] = list(_BASE_WO_CLAIM.get("templates", [])) + [
        ("leave_maze_tower", "离开迷魂塔", "迷魂塔场景内的「离开迷魂塔/退出」按钮。"
         "一般在迷魂塔一轮完成、结算或退出界面出现时弹出，框按钮本身、要独特。"),
    ]

    def _load_flags(self, tc):
        d = super()._load_flags(tc)
        t = tc.get("templates", {})
        d["leave_maze_tower"] = vision.load_template(t.get("leave_maze_tower")) if t.get("leave_maze_tower") else None
        return d
