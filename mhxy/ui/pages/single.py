# -*- coding: utf-8 -*-
"""单人任务分类页：内嵌 宝图 / 运镖 / 秘境降妖 / 三界奇缘 / 帮派签到 / 活跃度奖励 六个单人玩法页。"""

from .category import CategoryPage
from .treasure_map import TreasureMapPage
from .escort import EscortPage
from .secret_realm import SecretRealmPage
from .sanjie import SanjiePage
from .guild_checkin import GuildCheckinPage
from .activity_reward import ActivityRewardPage


class SinglePage(CategoryPage):
    LOG_SOURCE = "单人任务"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("宝图", TreasureMapPage),
                  ("运镖", EscortPage),
                  ("秘境降妖", SecretRealmPage),
                  ("三界奇缘", SanjiePage),
                  ("帮派签到", GuildCheckinPage),
                  ("活跃度奖励", ActivityRewardPage)],
            default=0, title="单人任务")
