# -*- coding: utf-8 -*-
"""商城分类页：内嵌商城相关工具页（当前为「抢关注」，即原秒装备任务）。
用卡片导航入口（nav_style="cards"），后续商城类工具继续加进 tabs 即可。"""

from .category import CategoryPage
from .sniper import SniperPage


class MallPage(CategoryPage):
    LOG_SOURCE = "商城"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("抢关注", SniperPage, "🛒")],
            default=0, title="商城", nav_style="cards")
