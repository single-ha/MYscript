# -*- coding: utf-8 -*-
"""副本·70级普通（普通区）。逻辑与蹈海去基本相同，通用自 dungeon_base。"""

from .dungeon_base import DungeonBaseTask, register


@register
class Dt70CommonTask(DungeonBaseTask):
    name = "dt_70_common"
    title = "70级普通"
    description = "副本·70级普通：组队后由队长跑流程，跑一遍即停"
    cat = "common"
