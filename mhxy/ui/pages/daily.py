# -*- coding: utf-8 -*-
"""日常页：只做「勾选 + 排序 + 副本勾选」——任务配置与标定全在「任务配置」页。任务按区展示，
每区可折叠，区内左键拖动手柄排序。两区分组固定：多人任务组在前、单人任务组在后（不支持两区互换）。
每个【任务名】本身即「单跑」按钮：点它=只跑那一个任务（daily 引擎 + 内存覆盖 steps，配置不落盘）；
刷副本勾选区里每个【副本名】同样是按钮：点它=只刷那一本。独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import copy

import customtkinter as ctk
from datetime import datetime, timedelta

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...tasks.base import dungeon_tasks
from ...tasks.daily import CHAINABLE, GROUP_OF, GROUP_TITLES, MULTI_BARRIER
from ...tasks.dungeon_base import DUNGEON_CALIBRATION
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ..common import (Card, Tooltip, required_regions, required_templates,
                      teaming_ns)
from ..dungeon_picker import DungeonPicker

# 分区集合。两区分组固定：多人组在前、个人组在后（不提供互换）；GROUP_OF 里出现过的区都保留（以后加新区自动跟上）。
_GROUPS = ["multi"] + [g for g in dict.fromkeys(GROUP_OF.values()) if g != "multi"]


class DailyPage(ctk.CTkFrame):
    """日常：只做串联——勾选哪些任务、按什么顺序跑，存 tasks.daily.steps（全局有序=执行顺序）。
    两区分组固定：多人组（集体组队跑）在前、个人组（每号独立跑）在后，不提供两区互换/挪动。
    单人任务组在本趟流程中时，「跑完多人任务后解散队伍」开关会被强制打开并锁定（单人步在队伍外更干净）。
    多开/单开与各任务的标定、参数全部在「任务配置」页设置，本页不另设这些开关（本页只多一处副本勾选）。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "日常"
    RUN_LABEL = "▶  开始日常"

    # 单跑日志的来源短标签（每任务一行；没列出的回退本页 LOG_SOURCE）
    _SINGLE_SOURCE = {
        "treasure_map": "宝图", "secret_realm": "秘境", "appreciation": "趣味鉴赏",
        "sanjie": "奇缘", "escort": "运镖", "guild_checkin": "帮派签到",
        "activity_reward": "活跃度", "dungeon": "刷副本", "zhuagui": "抓鬼",
    }

    # 各任务名的专用 Tooltip（各不相同；点任务名=单跑那一行在 Tooltip 里统一提示）
    _TITLE_TOOLTIPS = {
        "treasure_map": "挖宝图：自动判断是否已有宝图，没有就领取，然后收图、挖宝、领奖。",
        "secret_realm": "秘境降妖：挑战秘境并连跑多轮（次数到「任务配置」页设置）。",
        "appreciation": "趣味鉴赏：在图文列表里匹配心形图案并点击，点满次数或超时即停。",
        "sanjie": "三界奇缘：多开时逐号顺序完成（一个号先做完再轮下一个号）。",
        "escort": "运镖：押送普通镖银，循环押满设定次数。",
        "guild_checkin": "帮派签到：进帮派签到领奖励。",
        "activity_reward": "活跃度奖励：领取今日活跃度礼包。",
        "dungeon": "刷副本：集体组队，把勾选的副本按顺序刷完（勾选区点「副本名」可只刷那一本）。",
        "zhuagui": "抓鬼：集体组队，抓完一轮鬼。",
    }

    # 各子任务「就绪」比对的必需键全部从各自 CALIBRATION spec 按「可选标记」自动取（见
    # common.required_regions/required_templates），不再手写清单。

    _GROUP_DESC = {"single": "每号独立跑", "multi": "集体组队跑"}
    _GROUP_EMOJI = {"single": "👤", "multi": "👥"}

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self.single_runner = None     # 「单跑」专用 runner（与整条龙互斥，由全局唯一运行锁把关）
        self._single_name = None      # 正在单跑的任务名
        self._single_label = None     # 日志里的显示名（如「刷副本」「副本「xxx」」）
        self._single_tag = None       # 单跑日志来源短标签（None=回退 LOG_SOURCE）
        self._single_row = None       # 单跑中的行记录（用于复位就绪标签；行被重建后按 name 现找）
        self._wait_until = None      # 定时延迟执行：晚于该时刻才真正启动（None=不在等待）
        self._steps = []          # [{"task","enabled"}]，全局有序=执行顺序（按区顺序拼段）
        self._group_order = list(_GROUPS)
        self._rows = {g: [] for g in _GROUPS}   # 各区的行控件 [{"frame","name","badge","group","idx","step"}]
        self._collapsed = {g: False for g in _GROUPS}   # 各区是否折叠
        self._flat = []           # 渲染顺序（区头/行交错），与 list_frame 的 grid 行对齐
        self._dun_pick_open = True   # 「刷副本」卡内的副本勾选区是否展开（跨重建保留）
        self._lbl_count = {}      # group -> 计数标签
        self._lbl_title = {}      # group -> 区头标题标签（整组启停时只就地改颜色，不重建）
        self._lbl_chevron = {}    # group -> 折叠箭头标签
        self._drag = None         # 拖动中的状态 {"group": 区, "idx": 区内下标}
        self._group_vars = {}     # group -> 整组启用开关 BooleanVar
        self._group_on = {g: True for g in _GROUPS}   # group -> 整组启用（未存配置默认全开）
        self.switch_auto_organize = None      # 「自动整理背包」开关（任何任务检测到背包满自动整理）

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 头部 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(bar, text="日常", font=self.fonts["title"], text_color=T.TEXT)
        title.grid(row=0, column=0, sticky="w")
        Tooltip(title, "单人任务（每号独立跑）与多人任务（刷副本/抓鬼，集体组队跑）分区勾选调序；"
                        "点区题可折叠/展开，区右上 ▲▼ 移动整区顺序。本页只有勾选、排序与副本勾选；"
                        "各任务的标定、参数都请到「任务配置」页设置。点「任务名」=只跑那一个任务"
                        "（一次性、不落盘）；副本勾选区点「副本名」=只刷那一本。", self.fonts)

    # ---- 控制区：运行按钮 + 工具（选择窗口/刷新），无标定/无模式开关 ----
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="调整窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._adjust_windows).pack(side="left")
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left", padx=(8, 0))

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        # 设置项两列排布：左列=时间上限 / 跑完关机 / 自动整理背包，右列=解散队伍 / 定时延后执行
        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 10))
        opts.grid_columnconfigure(0, weight=1)
        opts.grid_columnconfigure(1, weight=1)

        def _opt_row(r, c):
            f = ctk.CTkFrame(opts, fg_color="transparent")
            f.grid(row=r, column=c, sticky="w", padx=(0, 26), pady=(10, 0))
            return f

        # 时间上限（整条龙的安全网）
        lim = _opt_row(0, 0)
        lim_lbl = ctk.CTkLabel(lim, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"],
                               text_color=T.TEXT)
        lim_lbl.pack(side="left")
        self.var_limit = ctk.StringVar(value="0")
        self.lim_ent = ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                                    fg_color=T.SURFACE_2, border_color=T.BORDER)
        self.lim_ent.pack(side="left", padx=(8, 0))
        for w in (lim_lbl, self.lim_ent):
            Tooltip(w, "只是安全网：正常会按各子任务自身条件跑完。未就绪（缺标定/缺窗口）的任务会自动跳过。", self.fonts)

        # 跑完多人任务后是否解散队伍（存共享 tasks.teaming.auto_disband，原在「多人任务」页组队设置里，
        # 组队设置现与任务参数一起收在「任务配置」页顶部）
        dis = _opt_row(0, 1)
        dis_lbl = ctk.CTkLabel(dis, text="跑完多人任务后解散队伍", font=self.fonts["body"], text_color=T.TEXT)
        dis_lbl.pack(side="left")
        self.var_disband = ctk.BooleanVar(value=False)
        self.sw_disband = ctk.CTkSwitch(dis, text="", variable=self.var_disband, width=44,
                                        progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                                        command=self._on_disband_toggle)
        self.sw_disband.pack(side="left", padx=(8, 0))
        Tooltip(dis_lbl, "跑完「多人任务」整段后自动让所有号退队，再转后续任务。"
                         "步骤里包含「单人任务」组时，此开关会被强制打开并锁定（单人步要在队伍外跑）；"
                         "只有多人任务时可按需开关。", self.fonts)

        # 跑完关机（谨慎）：整条龙全部跑完且有实跑任务才触发，延迟倒计时可 shutdown -a 取消
        shut = _opt_row(1, 0)
        shut_lbl = ctk.CTkLabel(shut, text="跑完关机", font=self.fonts["body"], text_color=T.TEXT)
        shut_lbl.pack(side="left")
        self.var_shutdown = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(shut, text="", variable=self.var_shutdown, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_shutdown_toggle).pack(side="left", padx=(8, 0))
        Tooltip(shut_lbl, "整条龙跑完后自动关机（先进入关机倒计时，期间按「停止」或急停热键即可取消）", self.fonts)

        # 定时延迟执行：点「开始日常」后先看有没有设置定时，有则等到该时刻才真正启动
        sched = _opt_row(1, 1)
        sched_lbl = ctk.CTkLabel(sched, text="定时延后执行", font=self.fonts["body"], text_color=T.TEXT)
        sched_lbl.pack(side="left")
        self.var_schedule_on = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(sched, text="", variable=self.var_schedule_on, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_schedule_toggle).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(sched, text="执行时刻(时:分)", font=self.fonts["body"], text_color=T.TEXT_DIM).pack(side="left", padx=(4, 0))
        self.var_schedule_time = ctk.StringVar(value="10:00")
        time_ent = ctk.CTkEntry(sched, textvariable=self.var_schedule_time, width=64,
                                font=self.fonts["body"], fg_color=T.SURFACE_2, border_color=T.BORDER)
        time_ent.pack(side="left", padx=(6, 0))
        for w in (sched_lbl, time_ent):
            Tooltip(w, "开启后，点「开始日常」不会立即执行，而是等到设定时刻才真正开始；"
                       "若设定时刻已过（如定时 10:00、下午点开始）则立即执行。等待中再点一次按钮可取消。", self.fonts)

        # «自动整理背包»（从「工具 › 整理背包」页移到这里的控制区，因为它影响运镖/宝图/秘境/副本
        # 等日常任务运行中的行为；配置仍存共享 tasks.organize_bag.auto_organize）。
        auto = _opt_row(2, 0)
        auto_lbl = ctk.CTkLabel(auto, text="自动整理背包", font=self.fonts["body"], text_color=T.TEXT)
        auto_lbl.pack(side="left")
        self.switch_auto_organize = ctk.CTkSwitch(
            auto, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
            command=self._toggle_auto_organize)
        self.switch_auto_organize.pack(side="left", padx=(8, 0))
        Tooltip(auto_lbl, "开启后，运镖 / 宝图 / 秘境 / 副本等任务运行中会每隔一会儿检测一次背包"
                          "「满」图标，满了就自动整理一遍 —— 需先在「工具 › 整理背包」页「标定」里"
                          "框选『背包满图标』，否则无从判断、不会触发。", self.fonts)

        # «已组队»（原在「任务配置」页顶部组队设置卡 / 周常页共用卡，随「跑完解散」一并迁到本页设置区；
        # 存共享 tasks.teaming.skip_team）。勾上=号已在游戏里自行组好队：刷副本/抓鬼等多人步跳过自动组队、
        # 直接由队长开跑，组件队标定与多开≥2 的 preflight 也随之放宽。
        skip = _opt_row(2, 1)
        skip_lbl = ctk.CTkLabel(skip, text="已组队（跳过自动组队）", font=self.fonts["body"], text_color=T.TEXT)
        skip_lbl.pack(side="left")
        self.var_skip_team = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(skip, text="", variable=self.var_skip_team, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_skip_team_toggle).pack(side="left", padx=(8, 0))
        Tooltip(skip_lbl, "已在游戏里自己组好队就勾上：刷副本 / 抓鬼等多人步会跳过脚本自动组队，"
                          "直接由队长的号开跑（此时只需队长那个号能定位即可，不要求多开≥2 和组队标定）。"
                          "存共享 tasks.teaming.skip_team，各多人任务读到同一份。", self.fonts)

    # ---- 主体：分区任务清单（日志已移到全局右栏）----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        head_title = ctk.CTkLabel(head, text="任务清单", font=self.fonts["h2"],
                                  text_color=T.TEXT)
        head_title.grid(row=0, column=0, sticky="w")
        Tooltip(head_title, "点区题折叠/展开　·　每区左侧 ⠿ 上下拖动排序（区内）　·　右侧开关启用/停用　·　"
                            "序号即全局执行先后　·　两区分组固定：多人任务组在前、单人任务组在后", self.fonts)
        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

    # ---- 工具按钮 ----
    def _adjust_windows(self):
        """「调整窗口」：把所选窗口（最多 5 个）调到基准尺寸并按 2列×2行 排布（原在通用页，随按钮迁到本页）。"""
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        win_mod.arrange_windows(cfg, self.app)
        self.refresh()

    # ------------------------------------------------------------------
    # 刷新 / 渲染
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        self._group_order = list(_GROUPS)      # 两区分组固定（多人在前、单人在后），不支持互换/挪动
        self._group_on = {g: bool((tc.get("group_enabled") or {}).get(g, True)) for g in _GROUPS}
        steps = self._normalize(tc.get("steps", []), self._group_order)
        # 只有内容/就绪状态真变了才换 self._steps：签名没变时行里的 step dict 还指着旧列表，
        # 若照常覆盖 self._steps，就地更新（_toggle_step 等）改的是新 dict、显示却是旧 dict —— 错位。
        if self._steps_signature_of(steps) != getattr(self, "_steps_sig", None):
            self._steps = steps
        self.var_limit.set(str(tc.get("loop", {}).get("time_limit_min", 0)))
        self.var_shutdown.set(bool(tc.get("loop", {}).get("shutdown_after", False)))
        sched = tc.get("loop", {}).get("schedule", "") or ""
        self.var_schedule_on.set(bool(sched))
        if sched and ":" in sched:
            self.var_schedule_time.set(sched)
        self.var_disband.set(bool(teaming_ns(self.app.cfg).get("auto_disband", False)))
        self.var_skip_team.set(bool(teaming_ns(self.app.cfg).get("skip_team", False)))
        ob = cfg_mod.task_config(self.app.cfg, "organize_bag")
        if self.switch_auto_organize is not None:
            if ob.get("auto_organize"):
                self.switch_auto_organize.select()
            else:
                self.switch_auto_organize.deselect()
        self._render_steps()
        self._sync_disband_lock()

    def _has_single_selected(self):
        """是否把「单人任务」组选进了本趟流程：整组启用 且 至少勾了一个单人任务。"""
        if not self._group_on.get("single", True):
            return False
        return any(s["enabled"] for s in self._steps if GROUP_OF[s["task"]] == "single")

    def _sync_disband_lock(self):
        """「跑完多人任务后解散队伍」开关联动：只要单人任务组在本趟流程中，就必须是开——
        强制打开并锁定（置灰不可点），同时把共享配置 tasks.teaming.auto_disband 写死 True
        （引擎据此解散）；没有单人任务时恢复用户自由开关，显示配置里的值。"""
        locked = self._has_single_selected()
        if locked:
            self.var_disband.set(True)
        self.sw_disband.configure(state="disabled" if locked else "normal")
        if locked and not bool(teaming_ns(self.app.cfg).get("auto_disband", False)):
            cfg = cfg_mod.load_config()
            cfg["tasks"].setdefault("teaming", {})["auto_disband"] = True
            cfg_mod.save_config(cfg)
            self.app.cfg = cfg

    @staticmethod
    def _normalize(stored, group_order):
        """把存储的 steps 规整成「含全部可串联任务、保留已存顺序、缺的补到末尾」，再按当前区顺序拼段
        （段内保持相对顺序=执行顺序）。"""
        out, seen = [], []
        for s in stored or []:
            if isinstance(s, dict) and s.get("task") in CHAINABLE and s["task"] not in seen:
                out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
                seen.append(s["task"])
        for t in CHAINABLE:
            if t not in seen:
                out.append({"task": t, "enabled": True})
                seen.append(t)
        seg = {g: [s for s in out if GROUP_OF[s["task"]] == g] for g in _GROUPS}
        return [s for g in group_order for s in seg.get(g, [])]

    def _selected_dungeon(self):
        """副本中枢勾选的首个有效副本名（tasks.dungeon.selected 是列表）；非法/缺失退回第一个已收录副本。"""
        names = [c.name for c in dungeon_tasks()]
        if not names:
            return None
        sel = cfg_mod.task_config(self.app.cfg, "dungeon").get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if isinstance(sel, list):
            for s in sel:
                if s in names:
                    return s
        return names[0]

    def _task_title(self, name):
        if name == "dungeon":
            # 具体刷哪些副本在本页「刷副本」步勾选，这里只显示一步「刷副本」，不挂具体副本名。
            return "刷副本"
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        """返回该步是否已就绪（必需区域+模板都已标定）。
        多人步（刷副本/抓鬼）额外查（未勾「已组队」时的）组队标定。"""
        if name == "dungeon":
            return self._dungeon_step_status()
        if name in MULTI_BARRIER:
            return self._barrier_step_status(name)
        sub = cfg_mod.task_config(self.app.cfg, name)
        return self._calib_done(name, sub)

    def _calib_done(self, name, sub):
        spec = getattr(get_task(name), "CALIBRATION", None) or {}
        # 每个任务自己的 spec 之外，还要查公共区域/公共模板（tasks.shared）对该任务的必需项
        # （活动列表/背包列表、战斗标识等），统一在「通用」页「标定（公共区域）」标定。
        need_r = required_regions(spec) + list(cfg_mod.TASK_SHARED_REQ.get(name, ()))
        need_t = required_templates(spec) + list(cfg_mod.TASK_SHARED_TPL_REQ.get(name, ()))
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        return all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)

    def _barrier_step_status(self, name):
        """多人步（如抓鬼）的就绪：自身标定 +（未勾「已组队」时）组队标定。"""
        sub = cfg_mod.task_config(self.app.cfg, name)
        self_ok = self._calib_done(name, sub)
        team = cfg_mod.task_config(self.app.cfg, "teaming")
        if team.get("skip_team", False):     # 已勾「已组队」：不查组队标定
            return self_ok
        treg, ttpl = team.get("regions", {}), team.get("templates", {})
        team_ok = (all(treg.get(k) for k in TEAM_REQUIRED_REGIONS)
                   and all(ttpl.get(k) for k in TEAM_REQUIRED_TEMPLATES))
        return self_ok and team_ok

    def _dungeon_step_status(self):
        """「刷副本」步的就绪：标定看共享 tasks.dungeon；多开≥2 是运行期条件，留给链内 preflight。"""
        if not self._selected_dungeon():
            return False
        sub = cfg_mod.task_config(self.app.cfg, "dungeon")
        spec = DUNGEON_CALIBRATION
        need_r = required_regions(spec) + list(cfg_mod.TASK_SHARED_REQ.get("dungeon", ()))
        need_t = required_templates(spec) + list(cfg_mod.TASK_SHARED_TPL_REQ.get("dungeon", ()))
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        self_ok = all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)
        team = cfg_mod.task_config(self.app.cfg, "teaming")
        if team.get("skip_team", False):     # 已勾「已组队」：不查组队标定
            return self_ok
        treg, ttpl = team.get("regions", {}), team.get("templates", {})
        team_ok = (all(treg.get(k) for k in TEAM_REQUIRED_REGIONS)
                   and all(ttpl.get(k) for k in TEAM_REQUIRED_TEMPLATES))
        return self_ok and team_ok

    def _steps_by_group(self, g):
        return [s for s in self._steps if GROUP_OF[s["task"]] == g]

    def _render_steps(self):
        # 内容/各任务就绪状态没变就别重建：切页时 refresh 反复调到这里，整段重画是「切页卡顿」来源之一。
        # 区顺序变化、整组启用开关变化也纳入签名。
        sig = self._steps_signature()
        if sig == getattr(self, "_steps_sig", None):
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._rows = {"single": [], "multi": []}
        self._drag = None
        self._flat = []
        self._lbl_title = {}
        for g in self._group_order:
            hdr = self._build_section_header(g)
            self._flat.append(("header", g, hdr))
            for step in self._steps_by_group(g):
                self._rows[g].append(self._build_row(g, step))
                self._flat.append(("row", g, len(self._rows[g]) - 1))
        self._grid_all()

    def _steps_signature(self):
        """当前整表渲染签名：区顺序、整组开关、每步(任务,启用,就绪)。任一变化都等于「内容真变了」。"""
        return self._steps_signature_of(self._steps)

    def _steps_signature_of(self, steps):
        """按给定 steps 列表算渲染签名（refresh 用它判断要不要换自持列表，避免新旧 dict 错位）。"""
        return (tuple(self._group_order),
                tuple((g, self._group_on.get(g, True)) for g in _GROUPS),
                [(s["task"], s["enabled"], self._task_status(s["task"])) for s in steps])

    def _ready_meta(self, name, enabled):
        """行就绪三态 → (文本, 颜色, 可点=「还需标定」)。「还需标定」可点跳「任务配置」页。
        判定顺序（用户拍板 2026-09-23）：先看是否选中——未选中即「未选中」；
        选中了再看标定——未标定=「还需标定」，标好了=「已就绪」。"""
        if not enabled:
            return "未选中", T.TEXT_DIM, False
        if not self._task_status(name):
            return "⚠ 还需标定", T.WARN, True
        return "✓ 已就绪", T.SUCCESS, False

    def _style_ready(self, btn, name, text, color, can_open):
        """统一给行就绪按钮上样式：
        - 可点（⚠ 还需标定）：黄色填充胶囊 + 悬停加深 + 手型 + 点击跳「任务配置」页——醒目像按钮。
        - 不可点（未选中/已就绪/单跑/组关）：透明文本样式（hover 无色差），仅靠文字颜色表达状态。"""
        if can_open:
            btn.configure(text=text, text_color=T.WARN_ON,
                          fg_color=T.WARN, hover_color=T.WARN_HOVER,
                          border_color=T.WARN_ON, border_width=1,
                          cursor="hand2",
                          command=(lambda nm=name: self._open_task_config(nm)))
        else:
            btn.configure(text=text, text_color=color,
                          fg_color="transparent", hover_color=T.SURFACE_2,
                          border_color=T.WARN_ON, border_width=0,
                          cursor="arrow", command=None)

    def _open_task_config(self, name):
        """「⚠ 还需标定」按钮点击：打开「任务配置」页并滚动定位到该任务的配置卡。"""
        self.app._show("config")
        cp = self.app.pages.get("config")
        if cp is not None and hasattr(cp, "reveal_card"):
            try:
                cp.reveal_card(name)
            except Exception:
                pass

    def _set_row_status(self, rec):
        """就地刷新某行「就绪/未选中/还需标定」按钮与记录（不重建整表）。"""
        name = rec["name"]
        st_text, st_color, can_open = self._ready_meta(name, rec["step"]["enabled"])
        rec["ready_text"], rec["ready_color"] = st_text, st_color
        self._style_ready(rec["ready"], name, st_text, st_color, can_open)

    def _find_row(self, name):
        """按任务名找行记录（拖动后下标会变，统一用 name 定位）。"""
        for g in self._rows:
            for r in self._rows[g]:
                if r["name"] == name:
                    return r
        return None

    def _build_section_header(self, g):
        """区头：标题（点它折叠/展开）+ 整组启用开关 + 计数。折叠是按 group 记状态、
        仅隐藏该区行；整组开关=整区停用/启用（行级开关独立保留，组关了再开回来行勾选仍在）。
        两区分组固定（多人在前、单人在后），无 ▲▼ 挪动按钮。"""
        bar = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        bar.grid_columnconfigure(2, weight=1)
        collapsed = self._collapsed.get(g, False)
        group_on = self._group_on.get(g, True)
        chev = ctk.CTkLabel(bar, text="▸" if collapsed else "▾", font=self.fonts["body_b"],
                            text_color=T.TEXT_DIM, width=22, anchor="w")
        chev.grid(row=0, column=0, sticky="w")
        self._lbl_chevron[g] = chev
        title = ctk.CTkLabel(
            bar,
            text=f"{self._GROUP_EMOJI.get(g, '▪')} {GROUP_TITLES.get(g, g)}"
                 + (f" · {self._GROUP_DESC[g]}" if g in self._GROUP_DESC else ""),
            font=self.fonts["h2"], text_color=(T.TEXT if group_on else T.TEXT_DIM), anchor="w")
        title.grid(row=0, column=1, sticky="w", padx=(2, 0))
        self._lbl_title[g] = title
        for w in (chev, title):
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
            w.bind("<Button-1>", lambda e, gg=g: self._toggle_collapse(gg))
        # 布局：标题右侧紧跟「已勾选 x/n」计数，整组启用开关放最右侧（重量列 2 把开关顶到右缘）。
        var = ctk.BooleanVar(value=group_on)
        self._group_vars[g] = var
        sw = ctk.CTkSwitch(bar, text="", variable=var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                           command=lambda gg=g, v=var: self._toggle_group(gg, v))
        sw.grid(row=0, column=3, sticky="e", padx=(0, 6))
        lbl = ctk.CTkLabel(bar, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        lbl.grid(row=0, column=2, sticky="w", padx=(10, 0))
        self._lbl_count[g] = lbl
        return bar

    def _build_row(self, g, step):
        """一张任务卡：左=拖动手柄，左二=执行序号徽标，中=标题+状态，右=启用开关。
        标题（任务名）本身即「单跑」按钮：点它=只跑这一个任务，不落盘、不动勾选；
        单跑中再点=停止。左键按住手柄上下拖动即可排序（仅本区内，见 _drag_*）。"""
        name = step["task"]
        row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid_columnconfigure(2, weight=1)

        # 左：拖动手柄（按住左键上下拖动整行排序）。光标设成移动样式（失败不致命）。
        grip = ctk.CTkLabel(row, text="⠿", font=self.fonts["h2"], text_color=T.TEXT_DIM, width=22)
        grip.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(10, 2), pady=5)
        try:
            grip.configure(cursor="fleur")
        except Exception:
            pass
        grip.bind("<Button-1>", lambda e, r=row: self._drag_start(e, r))
        grip.bind("<B1-Motion>", self._drag_motion)
        grip.bind("<ButtonRelease-1>", self._drag_end)

        # 左二：执行序号圆形徽标（启用=蓝底数字按全局先后编号；停用=灰底圆点，_renumber 里填）
        badge = ctk.CTkLabel(row, text="", font=self.fonts["body_b"], width=26, height=26,
                             corner_radius=13, text_color=T.ON_ACCENT, fg_color=T.ACCENT)
        badge.grid(row=0, column=1, rowspan=2, padx=(2, 12), pady=5)

        # 中：标题做成「按钮胶囊」（任务名=单跑入口），与就绪提示同排一行（卡片更矮）。
        # 绿/蓝药丸质感：浅灰底圆角框，悬停/单跑中变主题蓝，一眼可点。
        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=5)
        mid.grid_columnconfigure(0, weight=0)
        mid.grid_columnconfigure(1, weight=1)
        base_on = self._group_on.get(g, True)
        chip_fg = T.BTN if base_on else T.SURFACE
        base_txt = T.TEXT if base_on else T.TEXT_DIM
        chip = ctk.CTkFrame(mid, fg_color=chip_fg, corner_radius=T.RADIUS_SM)
        title = ctk.CTkLabel(chip, text=self._task_title(name), font=self.fonts["body_b"],
                             text_color=base_txt, cursor="hand2", anchor="w")
        title._chip_base = chip_fg          # 悬停还原基准色（整组启停/单跑高亮会就地更新它）
        title._txt_base = base_txt
        title.grid(row=0, column=0, padx=12, pady=4)
        chip.grid(row=0, column=0, sticky="w")
        title.bind("<Button-1>", lambda e, nm=name: self._start_single(nm))
        title.bind("<Enter>",
                   lambda e, ch=chip, tl=title: (ch.configure(fg_color=T.ACCENT),
                                                 tl.configure(text_color=T.ON_ACCENT)))
        title.bind("<Leave>",
                   lambda e, ch=chip, tl=title:
                   (ch.configure(fg_color=getattr(tl, "_chip_base", T.BTN)),
                    tl.configure(text_color=getattr(tl, "_txt_base", T.TEXT))))
        desc = self._TITLE_TOOLTIPS.get(name)
        Tooltip(title, (f"{desc}\n" if desc else "") +
                "点任务名 = 单独跑这一个任务（一次性：不改动勾选/顺序、配置不落盘）；"
                "单跑中再点这里=停止。整条龙一起跑用上方「开始日常」。", self.fonts)

        # 状态三态（用户拍板 2026-09-23 调整顺序）：先看是否选中——未勾选=「未选中」；
        # 选中了再看标定——未标定=「⚠ 还需标定」（可点跳任务配置页），标好了=「✓ 已就绪」。
        st_text, st_color, can_open = self._ready_meta(name, step["enabled"])
        ready_lbl = ctk.CTkButton(mid, text="", font=self.fonts["small"], height=24,
                                  corner_radius=T.RADIUS_SM, border_width=0)
        self._style_ready(ready_lbl, name, st_text, st_color, can_open)
        ready_lbl.grid(row=0, column=1, sticky="e", padx=(10, 0))

        # 右：启用 / 停用开关（按任务名定位，拖动后下标会变，故 _toggle_step 用 name 不用 idx）。
        # 整组停用时行级开关仍保留（组重开后行勾选恢复），仅视觉置灰。
        var = ctk.BooleanVar(value=step["enabled"])
        sw = ctk.CTkSwitch(row, text="", variable=var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                           command=lambda nm=name, v=var: self._toggle_step(nm, v))
        sw.grid(row=0, column=3, rowspan=2, padx=(8, 14), pady=5)
        # 「刷副本」步：卡片下内嵌副本勾选（唯一入口在本页，任务配置页只做标定；存 tasks.dungeon.selected）。
        if name == "dungeon":
            self._build_dungeon_picker(row)
        if not self._group_on.get(g, True):
            sw.configure(state="disabled")
            title.configure(text_color=T.TEXT_DIM)
            self._style_ready(ready_lbl, None, st_text, T.TEXT_DIM, False)
            for lbl in mid.winfo_children():
                if isinstance(lbl, ctk.CTkLabel):
                    lbl.configure(text_color=T.TEXT_DIM)

        return {"frame": row, "name": name, "badge": badge, "ready": ready_lbl,
                "ready_text": st_text, "ready_color": st_color,
                "title": title, "chip": chip, "chip_fg": chip_fg, "title_fg": base_txt,
                "sw": sw, "group": g, "idx": len(self._rows[g]), "step": step}

    def _build_dungeon_picker(self, row):
        """「刷副本」行卡片下方内嵌副本勾选：唯一入口在本页（任务配置页只做副本共用标定，不含勾选）。
        读写共享 tasks.dungeon.selected；标题可点击折叠勾选区，折叠状态存 self._dun_pick_open，重建后保持。
        每个【副本名】即是「单跑」按钮（on_run）——点它只刷那一个副本。"""
        sep = ctk.CTkFrame(row, fg_color=T.BORDER, height=1)
        sep.grid(row=2, column=0, columnspan=4, sticky="ew", padx=(14, 14), pady=(4, 6))
        pick = DungeonPicker(row, app=self.app, fonts=self.fonts, on_change=self._on_dungeon_pick,
                             caption="要刷的副本（按勾选顺序刷完 · 点副本名=只刷那一个）",
                             collapsible=True,
                             default_open=self._dun_pick_open, on_open_change=self._on_dun_pick_open,
                             on_run=self._start_single_dungeon)
        pick.grid(row=3, column=0, columnspan=4, sticky="ew", padx=(18, 16), pady=(0, 10))
        pick.sync()          # 从配置回填当前勾选，别让刚建的组件显示成全未勾
        self._dungeon_picker = pick

    def _on_dun_pick_open(self, open):
        """副本勾选区折叠/展开状态（组件里点了标题）：存下，下次重建照着展开/收起。"""
        self._dun_pick_open = bool(open)

    def _on_dungeon_pick(self, name, checked, sel):
        """副本勾选变化：config 已由组件写入；只就地刷新「刷副本」行的就绪状态（首本变化可能让
        就绪翻转）。不重建整表——本来就是重建导致整表闪烁/丢滚动位置。"""
        rec = self._find_row("dungeon")
        if rec is not None:
            self._set_row_status(rec)
        self._steps_sig = self._steps_signature()

    def _grid_all(self):
        """按 _flat 顺序把区头与行落回 list_frame 的 grid 行位（不销毁控件），再刷新序号/计数。
        折叠区的行不占位（grid_forget），区头保留可随时再展开。"""
        for pos, entry in enumerate(self._flat):
            if entry[0] == "header":
                entry[2].grid(row=pos, column=0, sticky="ew", padx=4, pady=(12, 2))
            else:
                g, idx = entry[1], entry[2]
                f = self._rows[g][idx]["frame"]
                if self._collapsed.get(g, False):
                    f.grid_forget()
                else:
                    f.grid(row=pos, column=0, sticky="ew", pady=3, padx=4)
        self._renumber()

    def _renumber(self):
        """按全局执行顺序刷新各行序号徽标 + 各区「已勾选 x/y」计数。折叠区行也参与编号（仍会执行）。"""
        order = 0
        for entry in self._flat:
            if entry[0] != "row":
                continue
            g, idx = entry[1], entry[2]
            r = self._rows[g][idx]
            if not self._group_on.get(g, True):
                r["badge"].configure(text="·", fg_color=T.SURFACE, text_color=T.TEXT_DIM)
            elif r["step"]["enabled"]:
                order += 1
                r["badge"].configure(text=str(order), fg_color=T.ACCENT, text_color=T.ON_ACCENT)
            else:
                r["badge"].configure(text="·", fg_color=T.SURFACE, text_color=T.TEXT_DIM)
        for g in self._group_order:
            gsteps = self._steps_by_group(g)
            n_on = sum(1 for s in gsteps if s["enabled"])
            if g in self._lbl_count:
                if not self._group_on.get(g, True):
                    self._lbl_count[g].configure(text="整组停用 · 已勾选 %d/%d" % (n_on, len(gsteps)),
                                                 text_color=T.WARN)
                else:
                    self._lbl_count[g].configure(text="已勾选 %d/%d" % (n_on, len(gsteps)),
                                                 text_color=T.TEXT_DIM)

    # ------------------------------------------------------------------
    # 启用切换 / 左键拖动排序（区内）/ 两区互换 / 保存
    # ------------------------------------------------------------------
    def _toggle_step(self, name, var):
        """行级启用/停用：只就地刷新该行状态 + 重排序号，不重建整表（重建会整表闪烁/丢滚动位置）。"""
        for s in self._steps:
            if s["task"] == name:
                s["enabled"] = bool(var.get())
                break
        self._save()
        self._sync_disband_lock()   # 单人任务勾选变化 → 解散开关联动（含单人任务时必须开）
        rec = self._find_row(name)
        if rec is not None:
            self._set_row_status(rec)
            self._renumber()
        self._steps_sig = self._steps_signature()

    def _toggle_group(self, g, var):
        """整组启用开关：只改组级开关/区头/各行样式并重排（行级 enabled 独立保留，
        组关了再开回来行勾选仍在）。就地更新，不重建整表。"""
        on = bool(var.get())
        self._group_on[g] = on
        self._group_vars[g] = var
        self._save()
        self._sync_disband_lock()   # 单人整组停用/启用 → 解散开关联动
        self._apply_group_appearance(g)
        self._renumber()
        if g in self._lbl_title:
            self._lbl_title[g].configure(text_color=T.TEXT if on else T.TEXT_DIM)
        self._steps_sig = self._steps_signature()
        n = len(self._steps_by_group(g))
        if on:
            self._log_line(f"已启用「{GROUP_TITLES.get(g, g)}」整组（{n} 个任务回归日常流程）。", "info")
        else:
            self._log_line(f"已停用「{GROUP_TITLES.get(g, g)}」整组：日常跳过这 {n} 个任务"
                           "（行级勾选保留，重新启用整组即恢复）。", "warn")

    def _style_title(self, rec, chip_fg, txt):
        """就地换某行标题胶囊配色，并同步「悬停还原基准色」（避免悬停后变回旧色）。"""
        rec["chip"].configure(fg_color=chip_fg)
        rec["title"].configure(text_color=txt)
        rec["title"]._chip_base = chip_fg
        rec["title"]._txt_base = txt

    def _apply_group_appearance(self, g):
        """整组启/停用后，就地刷该区所有行的置灰/恢复（开关禁用、胶囊配色、状态文字）。
        单跑高亮的行保持高亮（不被打回普通灰）。"""
        on = self._group_on.get(g, True)
        for r in self._rows[g]:
            if r is self._single_row:      # 单跑中：保持主题蓝高亮不动
                r["sw"].configure(state="disabled" if not on else "normal")
                continue
            r["sw"].configure(state="disabled" if not on else "normal")
            if on:
                self._style_title(r, T.BTN, T.TEXT)
            else:
                self._style_title(r, T.SURFACE, T.TEXT_DIM)
            self._set_row_status(r)
            if not on:
                self._style_ready(r["ready"], None, r["ready_text"], T.TEXT_DIM, False)

    def _drag_start(self, event, frame):
        """按按住的手柄定位其所在区与该区下标，进入拖动。"""
        for g, rows in self._rows.items():
            for k, r in enumerate(rows):
                if r["frame"] is frame:
                    self._drag = {"group": g, "idx": k}
                    frame.configure(fg_color=T.BTN)   # 提起高亮
                    frame.tkraise()
                    return

    def _drag_motion(self, event):
        if not self._drag:
            return
        tgt = self._target_at_pointer()
        if tgt is None:
            return
        g, idx = tgt
        if g != self._drag["group"]:
            return                                    # 只能区内排序，禁止跨区
        if idx != self._drag["idx"]:
            self._reorder(g, self._drag["idx"], idx)
            self._drag["idx"] = idx

    def _drag_end(self, event):
        if not self._drag:
            return
        g, idx = self._drag["group"], self._drag["idx"]
        self._drag = None
        if 0 <= idx < len(self._rows[g]):
            self._rows[g][idx]["frame"].configure(fg_color=T.SURFACE_2)
        self._save()
        self._steps_sig = None      # 顺序已变，强制下次干净重建
        self._render_steps()

    def _target_at_pointer(self):
        """指针当前落在哪一行的行内位置（按行竖直中线判定）；命中返回 (区, 区内下标)，区头/越界/折叠区返回 None。
        不销毁控件，故拖动中 winfo 几何有效。"""
        py = self.list_frame.winfo_pointery()
        for entry in self._flat:
            if entry[0] != "row":
                continue
            g, idx = entry[1], entry[2]
            f = self._rows[g][idx]["frame"]
            if not f.winfo_ismapped():
                continue
            if py < f.winfo_rooty() + f.winfo_height() / 2:
                return (g, idx)
        return None

    def _reorder(self, g, frm, to):
        """把某区第 frm 行移到该区第 to 位：_steps（区内整段）/ _rows 同步搬动，再按 _flat 重排 grid 与序号。
        全程不销毁控件——被按住的手柄控件存活，隐式 grab 不丢，B1-Motion 才能持续触发。"""
        base = self._group_base(g)
        self._steps.insert(base + to, self._steps.pop(base + frm))
        self._rows[g].insert(to, self._rows[g].pop(frm))
        for i, r in enumerate(self._rows[g]):
            r["idx"] = i
        self._grid_all()

    def _group_base(self, g):
        """该区任务在 _steps 里的段起点（段内连续、由 _normalize 保证）。"""
        for i, s in enumerate(self._steps):
            if GROUP_OF[s["task"]] == g:
                return i
        return 0

    def _toggle_collapse(self, g):
        """折叠/展开分区：只改 group 级折叠状态 + 重排可见行（不销毁控件、不重建整页）。"""
        self._collapsed[g] = not self._collapsed.get(g, False)
        self._lbl_chevron[g].configure(text="▸" if self._collapsed[g] else "▾")
        self._grid_all()

    def _save(self):
        """把当前勾选/顺序/区顺序/时间上限/跑完关机写回配置（读盘再改，避免覆盖别处刚写入的配置）。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["steps"] = [{"task": s["task"], "enabled": bool(s["enabled"])} for s in self._steps]
        tc["group_enabled"] = {g: bool(self._group_on.get(g, True)) for g in _GROUPS}
        loopc = tc.setdefault("loop", {})
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        loopc["shutdown_after"] = bool(self.var_shutdown.get())
        sched = ""
        if self.var_schedule_on.get():
            raw = self.var_schedule_time.get().strip().replace("：", ":")
            try:
                hh, mm = raw.split(":")
                hh, mm = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
                sched = f"{hh:02d}:{mm:02d}"
            except (ValueError, TypeError):
                sched = ""
        loopc["schedule"] = sched
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _on_shutdown_toggle(self):
        self._save()
        if self.var_shutdown.get():
            self._log_line("已勾选「跑完关机」：整条龙全部跑完后进入关机倒计时，倒计时内可按「停止」/急停热键取消。",
                           "warn")
        else:
            self._log_line("已取消「跑完关机」。", "info")

    def _on_schedule_toggle(self):
        self._save()
        if self.var_schedule_on.get():
            self._log_line(f"已开启定时延后执行：点「开始日常」会等到 {self.var_schedule_time.get()} 才真正执行（若该时刻已过则立即执行）。",
                           "warn")
        else:
            self._log_line("已关闭定时延后执行：点「开始日常」立即执行。", "info")

    def _on_disband_toggle(self):
        """「跑完多人任务后解散队伍」开关：存共享 tasks.teaming.auto_disband（原在「多人任务」页组队设置卡，
        组队设置现收在「任务配置」页顶部，此开关因影响日常运行仍留在本页）。
        日常引擎跑完多人组后是否强制解散 = 该值；共享命名空间，副本页等别处也读到同一份。"""
        on = bool(self.var_disband.get())
        if self._has_single_selected():     # 单人任务组在本趟流程中：强制开（开关本已锁定，防逻辑缺口）
            on = True
            self.var_disband.set(True)
        cfg = cfg_mod.load_config()
        tc = cfg["tasks"].setdefault("teaming", {})
        tc["auto_disband"] = on
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._log_line(("已开启：跑完多人任务后自动解散队伍。" if on
                        else "已关闭：跑完多人任务后保留队伍（不主动解散）。"), "info")

    def _on_skip_team_toggle(self):
        """「已组队」开关：存共享 tasks.teaming.skip_team。多人步（刷副本/抓鬼）的就绪状态随它松紧
        （勾上=不再要求组队标定），就地刷新那两行、不重建整表。"""
        on = bool(self.var_skip_team.get())
        cfg = cfg_mod.load_config()
        tc = cfg["tasks"].setdefault("teaming", {})
        tc["skip_team"] = on
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        for nm in ("dungeon", "zhuagui"):
            rec = self._find_row(nm)
            if rec is not None:
                self._set_row_status(rec)
        self._steps_sig = self._steps_signature()
        self._log_line(("已开启：副本 / 抓鬼 视为已组好队，跳过自动组队、直接由队长跑。"
                        if on else "已关闭：多人任务运行前会先自动组队。"), "info")

    def _toggle_auto_organize(self):
        """「自动整理背包」开关：存共享 tasks.organize_bag.auto_organize（任何日常任务检测到背包满自动整理）。"""
        on = bool(self.switch_auto_organize.get())
        cfg = cfg_mod.load_config()
        ob_tc = cfg_mod.task_config(cfg, "organize_bag")
        ob_tc["auto_organize"] = on
        cfg_mod.set_task_config(cfg, "organize_bag", ob_tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        if on:
            tpl_ok = bool((ob_tc.get("templates", {}) or {}).get("bag_full_icon"))
            self._log_line("已开启「自动整理背包」：日常任务运行中检测到背包满会自动整理。"
                           + ("" if tpl_ok else " ⚠ 但还没标定『背包满图标』，请先去「工具 › 整理背包」页「标定」框选，否则不会触发。"),
                           "warn" if not tpl_ok else "info")
        else:
            self._log_line("已关闭「自动整理背包」。", "info")

    # ------------------------------------------------------------------
    # 定时延迟执行：点「开始」时若设了时刻则先生成等待截止点；到点/已过再真启动
    # ------------------------------------------------------------------
    @staticmethod
    def _sched_wait_target(sched, now):
        """把定时串 "HH:MM" 换算成「今天的该时刻 datetime」。非法/空=不等待(None)。
        由调用方判断已过与否（已过 → 立即执行）。"""
        if not sched or ":" not in sched:
            return None
        try:
            hh, mm = sched.split(":")
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError):
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return now.replace(hour=hh, minute=mm, second=0, microsecond=0)

    @staticmethod
    def _schedule_target_passed(target, now):
        """定时目标时刻是否已过（含恰好到点→视为已过=立即执行）。"""
        return target is not None and target <= now

    # ------------------------------------------------------------------
    # 运行控制
    # ------------------------------------------------------------------
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        if self._wait_until is not None:
            # 正在等待定时 → 再点一次=取消
            self._wait_until = None
            self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER, state="normal")
            self._log_line("已取消定时等待，本次不执行。", "warn")
            return
        # 正常启动路径：先看有没有设置定时延迟执行
        self._save()
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        sched = (tc.get("loop", {}) or {}).get("schedule", "") or ""
        target = self._sched_wait_target(sched, datetime.now())
        if target is not None and not self._schedule_target_passed(target, datetime.now()):
            # 设了定时且还没到 → 进入等待
            self._wait_until = target
            self.btn_run.configure(text=f"等待到 {target:%H:%M}…（再点取消）",
                                   fg_color=T.WARN, hover_color=T.WARN_HOVER, state="normal")
            self._log_line(f"已设定时延迟执行：将等到 {target:%H:%M} 才开始（再点一次按钮可取消）。", "warn")
            return
        if target is not None:
            self._log_line(f"定时时刻 {target:%H:%M} 已过，立即开始执行。", "warn")
        self._start_runner()

    def _start_runner(self):
        """真正启动日常 runner（供：立即执行 / 定时到点执行）。"""
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER, state="normal")
            return
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_runner_finished(self):
        self.runner = None
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    # ------------------------------------------------------------------
    # 单跑单个任务：daily 引擎 + 内存覆盖 steps（不落盘、不动勾选/顺序）
    # ------------------------------------------------------------------
    def _single_blocked(self):
        """整条龙在跑 / 定时等待中 → 单跑不可启动。返回 True=被挡（并已提示）。"""
        if self.runner and self.runner.is_running():
            self._log_line("整条日常正在跑，请先「停止」停掉再「单跑」单独任务。", "warn")
            return True
        if self._wait_until is not None:
            self._log_line("定时延后执行等待中，请先取消定时再「单跑」。", "warn")
            return True
        return False

    def _stop_single(self):
        if self.single_runner and self.single_runner.is_running():
            self.single_runner.stop()
            self._log_line("正在停止单跑…（线程收尾后自动复位）", "warn")

    @staticmethod
    def _single_build_cfg(cfg, name, dungeon_only=None):
        """单跑用运行配置：只改内存深拷贝，绝不触盘。steps=只含这一个任务（整步语义；
        刷副本=当前勾选全部副本）；group_enabled 全开（绕过组级停用）；
        副本级单跑额外把 tasks.dungeon.selected 覆盖成 [那一个]。
        同时忽略「日常」页的整链设置（用户拍板）：单跑只按这个任务自己的流程走——
        不套整体时间上限、绝不跑完关机、不做定时等待、不按「跑完解散」收尾解散、
        不触发「自动整理背包」；「已组队」是当前会话的组队条件、属于共享组队设置，不动。"""
        run_cfg = copy.deepcopy(cfg)
        daily = run_cfg.setdefault("tasks", {}).setdefault("daily", {})
        daily["steps"] = [{"task": name, "enabled": True}]
        daily["group_enabled"] = {"single": True, "multi": True}
        loop = daily.setdefault("loop", {})
        loop["time_limit_min"] = 0          # 不套整条龙的整体时间上限
        loop["shutdown_after"] = False      # 单跑绝对不触发「跑完关机」
        loop.pop("schedule", None)          # 不做定时等待
        run_cfg.setdefault("tasks", {}).setdefault("teaming", {})["auto_disband"] = False
        run_cfg.setdefault("tasks", {}).setdefault("organize_bag", {})["auto_organize"] = False
        if dungeon_only:
            run_cfg.setdefault("tasks", {}).setdefault("dungeon", {})["selected"] = [dungeon_only]
        return run_cfg

    def _start_single(self, name):
        """行级单跑：只跑这一个任务（与链内该步语义完全一致；刷副本=当前勾选全部副本）。
        单跑进行中再点任意「单跑」= 停止当前单跑。"""
        if self._single_blocked():
            return
        if self.single_runner and self.single_runner.is_running():
            self._stop_single()
            return
        title = self._task_title(name)
        if not self._task_status(name):
            self.app.toast(f"「{title}」还需标定，单跑未启动（去「任务配置」页标定后再试）。", T.WARN)
            self._log_line(f"「{title}」还需标定，单跑未启动。", "warn")
            return
        if name == "dungeon":
            sel = cfg_mod.task_config(self.app.cfg, "dungeon").get("selected")
            if isinstance(sel, str):
                sel = [sel]
            if not (isinstance(sel, list) and sel):
                self._log_line("未勾选任何副本，将按「首个已收录副本」跑（与日常内行为一致）。", "warn")
        self._fire_single(name, self._single_build_cfg(self.app.cfg, name), title)

    def _start_single_dungeon(self, dname):
        """副本级单跑：只刷这一个副本（同一 daily 引擎，覆盖 tasks.dungeon.selected=[dname]）。"""
        if self._single_blocked():
            return
        if self.single_runner and self.single_runner.is_running():
            self._stop_single()
            return
        cls = get_task(dname)
        title = cls.title if cls else dname
        if not self._task_status("dungeon"):
            self.app.toast(f"「{title}」还需标定，单跑未启动（副本共用标定在「任务配置 › 刷副本」完成）。",
                           T.WARN)
            self._log_line(f"「{title}」还需标定，单跑未启动。", "warn")
            return
        self._fire_single("dungeon", self._single_build_cfg(self.app.cfg, "dungeon", dname),
                          f"副本「{title}」", tag=title)

    def _fire_single(self, name, run_cfg, label, tag=None):
        """共用启动：DailyTask + 单跑覆盖配置塞进专用 runner。start 失败（preflight/全局锁/无窗口）
        记 problems 并复位。"""
        runner = TaskRunner(get_task(self.TASK_NAME)(), run_cfg)
        ok, problems = runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动单跑：" + p, "error")
            return
        self.single_runner = runner
        self._single_name = name
        self._single_label = label
        self._single_tag = tag or self._SINGLE_SOURCE.get(name)
        self._mark_single_row(name)
        self._log_line(f"★ 单跑「{label}」：只跑这一个，其余不动（不改勾选/顺序、配置不落盘）★", "warn")

    def _mark_single_row(self, name):
        """把正在单跑的任务行高亮：标题胶囊变主题蓝（文字反白）+ 就绪标签改「▶ 正在单跑」；记下行供复位。"""
        for g in self._rows:
            for r in self._rows[g]:
                if r["name"] == name:
                    self._single_row = r
                    self._style_title(r, T.ACCENT, T.ON_ACCENT)
                    self._style_ready(r["ready"], None, "▶ 正在单跑", T.ACCENT, False)
                    return
        self._single_row = None

    def _restore_single_row(self):
        """复位单跑行（标题胶囊/就绪标签回构建时原样）。（行可能已被重建：经记录引用还原，失败则静默。）"""
        r = self._single_row
        self._single_row = None
        if r is not None:
            try:
                self._style_title(r, r["chip_fg"], r["title_fg"])
                self._style_ready(r["ready"], r["name"], r["ready_text"], r["ready_color"],
                                  can_open=(r["ready_text"] == "⚠ 还需标定"))
            except Exception:
                pass

    def _single_finished(self):
        """单跑线程收尾：清状态、复位行标签、打结束日志。"""
        label = self._single_label
        self.single_runner = None
        self._single_name = None
        self._single_label = None
        self._single_tag = None
        self._restore_single_row()
        if label:
            self._log_line(f"───── 单跑「{label}」已结束 ─────", "hit")

    def stop_pending(self):
        """急停/全局停止钩子：取消正在进行的定时等待（尚未启动的任务也要能停）。"""
        if self._wait_until is not None:
            self._wait_until = None
            self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                                   hover_color=T.ACCENT_HOVER, state="normal")

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()
        elif self._wait_until is not None:
            # 定时等待中：到点才启动
            if datetime.now() >= self._wait_until:
                self._wait_until = None
                self._log_line("⏰ 定时时刻到，开始日常…", "warn")
                self._start_runner()
        if self.single_runner:
            q = self.single_runner.log_queue
            tag = self._single_tag or getattr(self, "LOG_SOURCE", None)
            while not q.empty():
                level, msg = q.get()
                self.app.log_line(msg, level, tag)
            if not self.single_runner.is_running():
                self._single_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()