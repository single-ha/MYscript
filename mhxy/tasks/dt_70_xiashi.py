# -*- coding: utf-8 -*-
"""副本·70级侠士（侠士区）。逻辑通用自 dungeon_base；进副本后需轮询各号点「确认」。"""

from .dungeon_base import DungeonBaseTask, register


@register
class Dt70XiashiTask(DungeonBaseTask):
    name = "dt_70_xiashi"
    title = "70级侠士"
    description = "副本·70级侠士：组队后由队长跑流程，进副本后轮询各号点确认，跑一遍即停"
    cat = "xiashi"
