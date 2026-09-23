# -*- coding: utf-8 -*-
"""GUI 页面层：每个独立页面一个文件，App 从这统一导入（见 App.PAGE_CLASSES）。"""

from .category import CategoryPage
from .config_page import ConfigPage
from .tools import ToolsPage
from .weekly import WeeklyPage
from .settings import SettingsPage
from .about import AboutPage
from .general import GeneralPage
from .daily import DailyPage
from .organize_bag import OrganizeBagPage
from .tuoying import TuoyingPage
from .sect_gate import SectGatePage
from .underwater import UnderwaterPage
from .maze_tower import MazeTowerPage

# 任务「配置/标定」卡（由 ConfigPage 统一组装，无运行入口）
from .treasure_map import TreasureMapConfig
from .secret_realm import SecretRealmConfig
from .appreciation import AppreciationConfig
from .sanjie import SanjieConfig
from .escort import EscortConfig
from .guild_checkin import GuildCheckinConfig
from .activity_reward import ActivityRewardConfig
from .dungeon import DungeonConfig
from .zhuagui import ZhuaguiConfig

__all__ = [
    "CategoryPage", "ConfigPage", "ToolsPage", "WeeklyPage",
    "SettingsPage", "AboutPage", "GeneralPage", "DailyPage", "OrganizeBagPage",
    "TuoyingPage", "SectGatePage", "UnderwaterPage", "MazeTowerPage",
    "TreasureMapConfig", "SecretRealmConfig", "AppreciationConfig", "SanjieConfig",
    "EscortConfig", "GuildCheckinConfig", "ActivityRewardConfig", "DungeonConfig",
    "ZhuaguiConfig",
]