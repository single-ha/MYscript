# -*- coding: utf-8 -*-
"""商城分类页：内嵌商城相关工具页（「抢关注」秒装备、「抢非珍品」按限价读价抢买）。
用卡片导航入口（nav_style="cards"），后续商城类工具继续加进 tabs 即可。"""

from .category import CategoryPage
from .sniper import SniperPage
from .snipe_nonpremium import SnipeNonpremiumPage


class MallPage(CategoryPage):
    LOG_SOURCE = "商城"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("抢关注", SniperPage, "🛒"),
                  ("抢非珍品", SnipeNonpremiumPage, "💴")],
            default=0, title="商城", nav_style="cards")
