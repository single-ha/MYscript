# -*- coding: utf-8 -*-
"""工具分类页：目前内嵌 秒装备 一页；后续其它工具汇总到这里。"""

from .category import CategoryPage
from .sniper import SniperPage


class ToolsPage(CategoryPage):
    LOG_SOURCE = "工具"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("秒装备", SniperPage)],
            default=0, title="工具")
