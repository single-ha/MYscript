# -*- coding: utf-8 -*-
"""副本·70级侠士（侠士区）。侠士专属机制（切侠士区标签页/队员准备弹窗轮询）集中在 XiashiDungeonTask，
本类只声明身份；进副本后轮询各队员点「准备」（队长不点）的逻辑见 dungeon_base.XiashiDungeonTask。"""

from .dungeon_base import XiashiDungeonTask, register


@register
class Dt70XiashiTask(XiashiDungeonTask):
    name = "dt_70_xiashi"
    title = "70级侠士"
    description = "副本·70级侠士：组队后由队长跑流程，进副本后轮询各队员点确认（队长不点），跑一遍即停"
