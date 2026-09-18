# -*- coding: utf-8 -*-
"""GUI 页面层：每个独立页面一个文件，App 从这统一导入（见 App.PAGE_CLASSES）。"""

from .category import CategoryPage
from .single import SinglePage
from .multi import MultiPage
from .tools import ToolsPage
from .treasure_map import TreasureMapPage
from .escort import EscortPage
from .dungeon import DungeonPage
from .secret_realm import SecretRealmPage
from .settings import SettingsPage
from .about import AboutPage
from .general import GeneralPage
from .daily import DailyPage
from .organize_bag import OrganizeBagPage
from .tuoying import TuoyingPage

__all__ = [
    "CategoryPage", "SinglePage", "MultiPage", "ToolsPage",
    "TreasureMapPage", "EscortPage", "DungeonPage", "SecretRealmPage",
    "SettingsPage", "AboutPage", "GeneralPage", "DailyPage", "OrganizeBagPage",
    "TuoyingPage",
]
