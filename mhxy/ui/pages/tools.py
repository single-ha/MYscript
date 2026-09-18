# -*- coding: utf-8 -*-
"""工具分类页：内嵌 秒装备、整理背包、拓印 三页；后续其它工具汇总到这里。"""

from .category import CategoryPage
from .sniper import SniperPage
from .organize_bag import OrganizeBagPage
from .tuoying import TuoyingPage


class ToolsPage(CategoryPage):
    LOG_SOURCE = "工具"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("秒装备", SniperPage), ("整理背包", OrganizeBagPage), ("拓印", TuoyingPage)],
            default=0, title="工具")
