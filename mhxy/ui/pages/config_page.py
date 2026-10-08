# -*- coding: utf-8 -*-
"""任务配置页：单人/多人全部任务的「参数 + 标定」收拢到一个界面，无任何运行入口。
运行唯一入口在「日常」页。页面可滚动：单人任务区(7卡) + 多人任务区(刷副本/抓鬼 2卡)。
组队设置（已组队 skip_team / 跑完解散 auto_disband）在「日常」页设置区（存共享 tasks.teaming）；
「已组队」开关另在「周常」页组队设置卡（同一份配置，用户拍板保留）。
参数改动即保存（输入框失焦/回车即写回 config），标定/刷新即点即做。"""

import customtkinter as ctk

from .. import theme as T
from ..common import bind_wraplength
from ...core.config import SINGLE_TASK_ORDER
from .treasure_map import TreasureMapConfig
from .secret_realm import SecretRealmConfig
from .appreciation import AppreciationConfig
from .sanjie import SanjieConfig
from .escort import EscortConfig
from .guild_checkin import GuildCheckinConfig
from .activity_reward import ActivityRewardConfig
from .dungeon import DungeonConfig
from .zhuagui import ZhuaguiConfig
from .login import LoginConfig

# name -> 单人任务配置卡；顺序由 core.config.SINGLE_TASK_ORDER 决定（与「日常」个人组同源）
_SINGLE_CARDS = {
    "treasure_map": TreasureMapConfig,
    "secret_realm": SecretRealmConfig,
    "appreciation": AppreciationConfig,
    "sanjie": SanjieConfig,
    "escort": EscortConfig,
    "guild_checkin": GuildCheckinConfig,
    "activity_reward": ActivityRewardConfig,
}

_MULTI_CARDS = (DungeonConfig, ZhuaguiConfig)

# 「跳转定位」把目标卡滚到视口里、距顶留这么多像素呼吸
_REVEAL_PAD = 24
_REVEAL_TRIES = 3      # 内容还在长时的重试上限（见 _scroll_reveal 坑 3）


class ConfigPage(ctk.CTkFrame):
    LOG_SOURCE = "任务配置"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cards = []
        self._card_by_name = {}      # 任务 name -> 配置卡（「日常」页「还需标定」按钮跳转定位用）
        self._sections = []          # [{key, open, widgets, chev, title_lbl}]：区折叠（点区题展开/收起整区）
        self._collapsed_section = set()   # 已折叠的区 key（内存态，与「日常」页 _collapsed 一致）

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_body()
        self._apply_sections_open()
        self.refresh()

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 12))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="任务配置 / 标定", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="单人/多人任务的全部参数与标定都收在这里，改动即保存、无需运行按钮；"
                                     "运行唯一入口在「日常」页。公共区域/组队模板请到「通用」页统一标定。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

    def _build_body(self):
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.grid(row=1, column=0, sticky="nsew", padx=2)
        scroll.grid_columnconfigure(0, weight=1)
        try:
            T.tune_scroll_speed(scroll)
        except Exception:
            pass
        self.body = scroll

        row = 0

        # 登录游戏区（自己起客户端，不在日常任务链里，故单列一区放最上面）
        row = self._section(scroll, row, "login", "🚀 登录游戏 · 逐号启动客户端",
                            "标定客户端路径与切号按钮；账号本身在「账号库」里标定（最多 10 个），"
                            "并挑最多 5 个去登录（按挑的先后依次登录）——账号库在「日常」页顶部登录区。")
        c = LoginConfig(scroll, self.app)
        c.grid(row=row, column=0, sticky="ew", padx=2, pady=(0, 12))
        self._cards.append(c)
        self._card_by_name[c.TASK_NAME] = c
        self._sections[-1]["widgets"].append(c)
        row += 1

        # 单人任务区
        row = self._section(scroll, row, "single", "👤 单人任务 · 每号独立跑",
                            "参数改动即保存；标定完成后回到这里即可看到就绪状态。")
        for name in SINGLE_TASK_ORDER:
            cls = _SINGLE_CARDS.get(name)
            if cls is None:
                continue
            c = cls(scroll, self.app)
            c.grid(row=row, column=0, sticky="ew", padx=2, pady=(0, 12))
            self._cards.append(c)
            self._card_by_name[getattr(c, "TASK_NAME", name)] = c
            self._sections[-1]["widgets"].append(c)
            row += 1

        # 多人任务区
        row = self._section(scroll, row, "multi", "👥 多人任务 · 集体组队跑",
                            "组队设置（已组队 / 跑完解散）在「日常」页设置区；副本的勾选与排队"
                            "在该页「刷副本」步。")
        for cls in _MULTI_CARDS:
            c = cls(scroll, self.app)
            c.grid(row=row, column=0, sticky="ew", padx=2, pady=(0, 12))
            self._cards.append(c)
            self._card_by_name[getattr(c, "TASK_NAME", getattr(c, "LOG_SOURCE", ""))] = c
            self._sections[-1]["widgets"].append(c)
            row += 1

    def _section(self, scroll, row, key, title, desc):
        """区头：标题行可点击折叠/展开整区（chevron ▾/▸），desc 保留在区头。返回下一可用 grid 行号。"""
        f = ctk.CTkFrame(scroll, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=8, pady=(4, 10))
        f.grid_columnconfigure(1, weight=1)
        chev = ctk.CTkLabel(f, text="▾", font=self.fonts["h2"], text_color=T.TEXT_DIM,
                            cursor="hand2", anchor="w")
        chev.grid(row=0, column=0, sticky="w", padx=(0, 4))
        title_lbl = ctk.CTkLabel(f, text=title, font=self.fonts["h2"], text_color=T.TEXT,
                                 cursor="hand2", anchor="w")
        title_lbl.grid(row=0, column=1, sticky="w")
        dc = ctk.CTkLabel(f, text=desc, font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        dc.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        bind_wraplength(dc)
        sec = {"key": key, "open": True, "widgets": [], "chev": chev, "title_lbl": title_lbl}
        for w in (chev, title_lbl):
            w.bind("<Button-1>", lambda e, s=sec: self._toggle_section(s))
        self._sections.append(sec)
        return row + 1

    def _toggle_section(self, sec):
        """点区题：展开/收起整个区（只隐藏该区配置卡，区头保留）。"""
        widgets = sec["widgets"]
        if sec["open"]:
            sec["open"] = False
            self._collapsed_section.add(sec["key"])
            for w in widgets:
                w.grid_remove()
            sec["chev"].configure(text="▸")
            sec["title_lbl"].configure(text_color=T.TEXT_DIM)
        else:
            sec["open"] = True
            self._collapsed_section.discard(sec["key"])
            for w in widgets:
                w.grid()
            sec["chev"].configure(text="▾")
            sec["title_lbl"].configure(text_color=T.TEXT)

    def _apply_sections_open(self):
        """按上次折叠状态收一次区（grid_remove 会保留各 widget 的 grid 选项，展开时 w.grid() 无参即恢复）。"""
        for sec in self._sections:
            if sec["key"] in self._collapsed_section:
                sec["open"] = True
                self._toggle_section(sec)

    # ---- 刷新 ----
    def refresh(self):
        for c in self._cards:
            try:
                c.refresh()
            except Exception:
                pass

    def reveal_card(self, task_name):
        """跳转定位：把「任务配置」页滚动到指定任务的配置卡（必要时先展开其所在区）。
        「日常」页的「⚠ 还需标定」按钮点击后调用。"""
        card = self._card_by_name.get(task_name)
        if card is None:
            return
        sec = next((s for s in self._sections if card in s["widgets"]), None)
        if sec is not None and not sec["open"]:
            self._toggle_section(sec)
        try:
            self.app.after_idle(self._scroll_reveal, card)
        except Exception:
            pass

    def _scroll_reveal(self, card, _try=0):
        """滚动到让目标卡顶部出现在滚动区偏上位置（留 _REVEAL_PAD 呼吸）。

        ⚠ 三个坑（都踩过，缺一个就又变成「一律顶到最下边」）：
        1) 分母必须是**内容总高**（canvas scrollregion 全高），不是视口高。用视口高算出的
           比例会成倍偏大，直接被 clamp 到 1.0 → 每次都滚到最下边。
        2) after_idle 触发时页面往往**还没完成布局**——各配置卡的说明文字/自动换行还在陆续
           定高，此时量到的 card.winfo_y() 和内容总高都是半成品值，算出来的比例偏大。故每次
           都先 update_idletasks() 把几何刷到最新再量。
        3) 内容可能还在继续长（长到 1905px 要好几拍），所以量完回头校验目标卡是否真落在视口里，
           没落进去就再等一拍重量（封顶 _REVEAL_TRIES 次，绝不无限重排）。
        """
        try:
            self.app.update_idletasks()          # 先把几何刷到最新（坑 2）
            canvas = self.body._parent_canvas
            box = canvas.bbox("all")
            total = box[3] if box else 0        # 内容总高（坑 1）
            y = card.winfo_y()                   # 卡在滚动内容里的纵向偏移（与 body 同坐标系）
            if total > 0:
                canvas.yview_moveto(max(0.0, min(1.0, (y - _REVEAL_PAD) / total)))
            if _try < _REVEAL_TRIES:            # 校验 + 重试（坑 3）
                top = canvas.yview()[0] * total
                if not (-1 <= (y - top) < canvas.winfo_height()):
                    self.app.after_idle(self._scroll_reveal, card, _try + 1)
        except Exception:
            pass
