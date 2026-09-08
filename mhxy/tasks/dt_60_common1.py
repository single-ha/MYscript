# -*- coding: utf-8 -*-
"""副本·60级普通1（普通区）。逻辑与蹈海去基本相同，通用自 dungeon_base。"""

from .dungeon_base import DungeonBaseTask, register


@register
class Dt60Common1Task(DungeonBaseTask):
    name = "dt_60_common1"
    title = "60级普通1"
    description = "副本·60级普通1：组队后由队长跑流程，跑一遍即停"
    cat = "common"
