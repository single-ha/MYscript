# -*- coding: utf-8 -*-
"""日常一条龙页：只做「勾选 + 排序」——各任务设置全在各任务页。任务按区展示，
每区可折叠，区内左键拖动手柄排序。两区分组固定：多人任务组在前、单人任务组在后（不支持两区互换）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

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
from ..common import (Card, Tooltip, bind_wraplength, required_regions, required_templates,
                      teaming_ns)
from ..dungeon_picker import DungeonPicker

# 分区集合。两区分组固定：多人组在前、个人组在后（不提供互换）；GROUP_OF 里出现过的区都保留（以后加新区自动跟上）。
_GROUPS = ["multi"] + [g for g in dict.fromkeys(GROUP_OF.values()) if g != "multi"]


class DailyPage(ctk.CTkFrame):
    """日常一条龙：只做串联——勾选哪些任务、按什么顺序跑，存 tasks.daily.steps（全局有序=执行顺序）。
    两区分组固定：多人组（集体组队跑）在前、个人组（每号独立跑）在后，不提供两区互换/挪动。
    单人任务组在本趟流程中时，「跑完多人任务后解散队伍」开关会被强制打开并锁定（单人步在队伍外更干净）。
    多开/单开与各任务的标定、参数全部沿用各自任务页，本页不另设这些开关。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "一条龙"
    RUN_LABEL = "▶  开始一条龙"

    # 各子任务「就绪」比对的必需键全部从各自 CALIBRATION spec 按「可选标记」自动取（见
    # common.required_regions/required_templates），不再手写清单。

    _GROUP_DESC = {"single": "每号独立跑", "multi": "集体组队跑"}
    _GROUP_EMOJI = {"single": "👤", "multi": "👥"}

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._wait_until = None      # 定时延迟执行：晚于该时刻才真正启动（None=不在等待）
        self._steps = []          # [{"task","enabled"}]，全局有序=执行顺序（按区顺序拼段）
        self._group_order = list(_GROUPS)
        self._rows = {g: [] for g in _GROUPS}   # 各区的行控件 [{"frame","name","badge","group","idx","step"}]
        self._collapsed = {g: False for g in _GROUPS}   # 各区是否折叠
        self._flat = []           # 渲染顺序（区头/行交错），与 list_frame 的 grid 行对齐
        self._dun_pick_open = True   # 「刷副本」卡内的副本勾选区是否展开（跨重建保留）
        self._lbl_count = {}      # group -> 计数标签
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
        title = ctk.CTkLabel(bar, text="日常一条龙", font=self.fonts["title"], text_color=T.TEXT)
        title.grid(row=0, column=0, sticky="w")
        Tooltip(title, "单人任务（每号独立跑）与多人任务（刷副本/抓鬼，集体组队跑）分区勾选调序；"
                        "点区题可折叠/展开，区右上 ▲▼ 移动整区顺序。多开/单开与各任务的"
                        "标定、参数全部沿用各自任务页设置，本页只有勾选与排序。", self.fonts)

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

        # 跑完多人任务后是否解散队伍（存共享 tasks.teaming.auto_disband，原在「多人任务」页组队设置里）
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

        # 定时延迟执行：点「开始一条龙」后先看有没有设置定时，有则等到该时刻才真正启动
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
            Tooltip(w, "开启后，点「开始一条龙」不会立即执行，而是等到设定时刻才真正开始；"
                       "若设定时刻已过（如定时 10:00、下午点开始）则立即执行。等待中再点一次按钮可取消。", self.fonts)

        # «自动整理背包»（从「工具 › 整理背包」页移到这里的控制区，因为它影响运镖/宝图/秘境/副本
        # 等一条龙任务运行中的行为；配置仍存共享 tasks.organize_bag.auto_organize）。
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
        self._steps = self._normalize(tc.get("steps", []), self._group_order)
        self.var_limit.set(str(tc.get("loop", {}).get("time_limit_min", 0)))
        self.var_shutdown.set(bool(tc.get("loop", {}).get("shutdown_after", False)))
        sched = tc.get("loop", {}).get("schedule", "") or ""
        self.var_schedule_on.set(bool(sched))
        if sched and ":" in sched:
            self.var_schedule_time.set(sched)
        self.var_disband.set(bool(teaming_ns(self.app.cfg).get("auto_disband", False)))
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
            # 具体刷哪些副本在「刷副本」页勾选，这里只显示一步「刷副本」，不挂具体副本名。
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
        sig = (tuple(self._group_order),
               tuple((g, self._group_on.get(g, True)) for g in _GROUPS),
               [(s["task"], s["enabled"], self._task_status(s["task"])) for s in self._steps])
        if sig == getattr(self, "_steps_sig", None):
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._rows = {"single": [], "multi": []}
        self._drag = None
        self._flat = []
        for g in self._group_order:
            hdr = self._build_section_header(g)
            self._flat.append(("header", g, hdr))
            for step in self._steps_by_group(g):
                self._rows[g].append(self._build_row(g, step))
                self._flat.append(("row", g, len(self._rows[g]) - 1))
        self._grid_all()

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
        左键按住手柄上下拖动即可排序（仅本区内，见 _drag_*）。"""
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

        # 中：标题（可换行）+ 就绪提示同排一行（卡片更矮），独占可伸展列
        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=5)
        mid.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(mid, text=self._task_title(name), font=self.fonts["body_b"],
                             text_color=T.TEXT, anchor="w")
        title.grid(row=0, column=0, sticky="ew")
        bind_wraplength(title)

        # 状态三态：先看标定（未标定=需标定，条件不变）；标定好了再看勾选——
        # 未勾选=「未选中」，勾选了才显示「已就绪」（用户拍板 2026-09-22）。
        if not self._task_status(name):
            st_text, st_color = "⚠ 还需标定", T.WARN
        elif not step["enabled"]:
            st_text, st_color = "未选中", T.TEXT_DIM
        else:
            st_text, st_color = "✓ 已就绪", T.SUCCESS
        ready_lbl = ctk.CTkLabel(mid, text=st_text, font=self.fonts["small"],
                                 text_color=st_color)
        ready_lbl.grid(row=0, column=1, sticky="e", padx=(10, 0))

        # 右：启用 / 停用开关（按任务名定位，拖动后下标会变，故 _toggle_step 用 name 不用 idx）。
        # 整组停用时行级开关仍保留（组重开后行勾选恢复），仅视觉置灰。
        var = ctk.BooleanVar(value=step["enabled"])
        sw = ctk.CTkSwitch(row, text="", variable=var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                           command=lambda nm=name, v=var: self._toggle_step(nm, v))
        sw.grid(row=0, column=3, rowspan=2, padx=(8, 14), pady=5)
        # 「刷副本」步：卡片下内嵌副本勾选（共享组件，与「刷副本」页同一份 tasks.dungeon.selected）。
        if name == "dungeon":
            self._build_dungeon_picker(row)
        if not self._group_on.get(g, True):
            sw.configure(state="disabled")
            title.configure(text_color=T.TEXT_DIM)
            for lbl in mid.winfo_children():
                if isinstance(lbl, ctk.CTkLabel):
                    lbl.configure(text_color=T.TEXT_DIM)

        return {"frame": row, "name": name, "badge": badge, "group": g, "idx": len(self._rows[g]), "step": step}

    def _build_dungeon_picker(self, row):
        """「刷副本」行卡片下方内嵌副本勾选：用共享组件 DungeonPicker，读写与「刷副本」页同一份
        tasks.dungeon.selected —— 一处点选、两页同步（杜绝双份设置分歧）。标题可点击折叠勾选区，
        折叠状态存 self._dun_pick_open，整表重建后仍保持。"""
        sep = ctk.CTkFrame(row, fg_color=T.BORDER, height=1)
        sep.grid(row=2, column=0, columnspan=4, sticky="ew", padx=(14, 14), pady=(4, 6))
        pick = DungeonPicker(row, app=self.app, fonts=self.fonts, on_change=self._on_dungeon_pick,
                             caption="要刷的副本（与「刷副本」页同步）", collapsible=True,
                             default_open=self._dun_pick_open, on_open_change=self._on_dun_pick_open)
        pick.grid(row=3, column=0, columnspan=4, sticky="ew", padx=(18, 16), pady=(0, 10))
        pick.sync()          # 从配置回填当前勾选，别让刚建的组件显示成全未勾
        self._dungeon_picker = pick

    def _on_dun_pick_open(self, open):
        """副本勾选区折叠/展开状态（组件里点了标题）：存下，下次重建照着展开/收起。"""
        self._dun_pick_open = bool(open)

    def _on_dungeon_pick(self, name, checked, sel):
        """副本勾选变化：config 已由组件写入；「刷副本」步就绪（至少要勾一个副本）可能翻转，
        整表重绘一次刷新状态（重建的 picker 会从配置重新同步，勾选不丢）。"""
        self._steps_sig = None
        try:
            self.app.after_idle(self._render_steps)
        except Exception:
            self._render_steps()

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
        for s in self._steps:
            if s["task"] == name:
                s["enabled"] = bool(var.get())
                break
        self._save()
        self._sync_disband_lock()   # 单人任务勾选变化 → 解散开关联动（含单人任务时必须开）
        self._render_steps()

    def _toggle_group(self, g, var):
        """整组启用开关：只改组级开关并重画（行级 enabled 独立保留，组关了再开回来行勾选仍在）。"""
        on = bool(var.get())
        self._group_on[g] = on
        self._group_vars[g] = var
        self._save()
        self._sync_disband_lock()   # 单人整组停用/启用 → 解散开关联动
        self._render_steps()
        n = len(self._steps_by_group(g))
        if on:
            self._log_line(f"已启用「{GROUP_TITLES.get(g, g)}」整组（{n} 个任务回归一条龙流程）。", "info")
        else:
            self._log_line(f"已停用「{GROUP_TITLES.get(g, g)}」整组：一条龙跳过这 {n} 个任务"
                           "（行级勾选保留，重新启用整组即恢复）。", "warn")

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
            self._log_line(f"已开启定时延后执行：点「开始一条龙」会等到 {self.var_schedule_time.get()} 才真正执行（若该时刻已过则立即执行）。",
                           "warn")
        else:
            self._log_line("已关闭定时延后执行：点「开始一条龙」立即执行。", "info")

    def _on_disband_toggle(self):
        """「跑完多人任务后解散队伍」开关：存共享 tasks.teaming.auto_disband（原在「多人任务」页组队设置卡）。
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

    def _toggle_auto_organize(self):
        """「自动整理背包」开关：存共享 tasks.organize_bag.auto_organize（任何一条龙任务检测到背包满自动整理）。"""
        on = bool(self.switch_auto_organize.get())
        cfg = cfg_mod.load_config()
        ob_tc = cfg_mod.task_config(cfg, "organize_bag")
        ob_tc["auto_organize"] = on
        cfg_mod.set_task_config(cfg, "organize_bag", ob_tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        if on:
            tpl_ok = bool((ob_tc.get("templates", {}) or {}).get("bag_full_icon"))
            self._log_line("已开启「自动整理背包」：一条龙任务运行中检测到背包满会自动整理。"
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
        """真正启动一条龙 runner（供：立即执行 / 定时到点执行）。"""
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
                self._log_line("⏰ 定时时刻到，开始一条龙…", "warn")
                self._start_runner()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()