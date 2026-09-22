# -*- coding: utf-8 -*-
"""单人任务分类页：内嵌 七个单人玩法页。页签顺序 = core.config.SINGLE_TASK_ORDER（与
日常一条龙「单人任务组」默认顺序同源——改页签/默认序只改那一处，这里自动跟随）。"""

from .category import CategoryPage
from .treasure_map import TreasureMapPage
from .secret_realm import SecretRealmPage
from .appreciation import AppreciationPage
from .sanjie import SanjiePage
from .escort import EscortPage
from .guild_checkin import GuildCheckinPage
from .activity_reward import ActivityRewardPage
from ...core.config import SINGLE_TASK_ORDER

# name -> (页签标题, 页面类)；SINGLE_TASK_ORDER 决定排列顺序
_PAGE_OF = {
    "treasure_map": ("宝图", TreasureMapPage),
    "secret_realm": ("秘境降妖", SecretRealmPage),
    "appreciation": ("趣味鉴赏", AppreciationPage),
    "sanjie": ("三界奇缘", SanjiePage),
    "escort": ("运镖", EscortPage),
    "guild_checkin": ("帮派签到", GuildCheckinPage),
    "activity_reward": ("活跃度奖励", ActivityRewardPage),
}


class SinglePage(CategoryPage):
    LOG_SOURCE = "单人任务"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[_PAGE_OF[n] for n in SINGLE_TASK_ORDER if n in _PAGE_OF],
            default=0, title="单人任务")
