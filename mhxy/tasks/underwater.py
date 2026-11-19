# -*- coding: utf-8 -*-
"""
海底世界任务（周常）。

薄子类：全部逻辑在 WeeklyBaseTask（mhxy/tasks/weekly_base.py）：
多人任务先自动组队、组好后只驱动队长窗口，循环跑「领任务 → 做任务 → 判完成 → 再开新一轮」。
本类只写名称/标题——「参加活动」NPC 对话框按钮等模板在页面标定向导里各自框选。

海底世界专属（user 2026-11-19）：点完 NPC「参加活动」后会弹「选择玩法」界面，须先点玩法、
再点公共「确认」按钮才真正进任务场景。故本子类比其它周常多一项必标模板 `select_play`
（「选择玩法」界面里要点的那个玩法），并开启 NEEDS_SELECT_PLAY 开关走这两步；「确认」复用
全局共享 gg_next（「任务配置」页「全局共享标定」卡里标的「确定按钮」，与抓鬼同款）。
"""

from .weekly_base import WeeklyBaseTask, without_templates, with_templates
from .base import register  # noqa: F401


@register
class UnderwaterTask(WeeklyBaseTask):
    name = "underwater"
    title = "海底世界"
    description = "周常：海底世界（多人组队，队长跑循环；参加活动后选玩法+确认，漫无目标时自动中止）"
    # 本活动点完「参加活动」就进场景，没有「领取任务」这一步 → 向导里不列该项；
    # 但要列海底世界专属的「选择玩法」（必标），点完它再点公共确认按钮才进场景。
    CALIBRATION = with_templates(
        without_templates(WeeklyBaseTask.CALIBRATION, "claim_task"),
        ("select_play", "选择玩法",
         "点完 NPC「参加活动」后弹出的「选择玩法」界面里，要点选的那个玩法按钮/图标（框按钮本身）。"
         "脚本点了它之后会再点公共标定的「确定按钮」确认，才真正进任务场景。必标。"),
    )
    NEEDS_SELECT_PLAY = True
