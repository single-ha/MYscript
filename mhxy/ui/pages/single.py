# -*- coding: utf-8 -*-
"""单人任务分类页：内嵌 宝图 / 秘境降妖 / 趣味鉴赏 / 三界奇缘 / 运镖 / 帮派签到 / 活跃度奖励 七个单人玩法页。"""

from .category import CategoryPage
from .treasure_map import TreasureMapPage
from .secret_realm import SecretRealmPage
from .appreciation import AppreciationPage
from .sanjie import SanjiePage
from .escort import EscortPage
from .guild_checkin import GuildCheckinPage
from .activity_reward import ActivityRewardPage


class SinglePage(CategoryPage):
    LOG_SOURCE = "单人任务"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("宝图", TreasureMapPage),
                  ("秘境降妖", SecretRealmPage),
                  ("趣味鉴赏", AppreciationPage),
                  ("三界奇缘", SanjiePage),
                  ("运镖", EscortPage),
                  ("帮派签到", GuildCheckinPage),
                  ("活跃度奖励", ActivityRewardPage)],
            default=0, title="单人任务")
