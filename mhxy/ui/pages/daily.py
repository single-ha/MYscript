# -*- coding: utf-8 -*-
"""日常页：纯只读的「日常任务清单」。

定位（用户拍板）：本页**不再管理日常流程**——没有开始/停止按钮、没有时间上限/关机/定时/
自动整理/已组队等设置开关、没有勾选、整组开关、拖拽排序、执行序号与执行链摘要。
这些分别在：
  · 整条龙怎么跑（开始日常、时间上限、跑完关机、定时、自动整理、已组队、跑完解散…）
        → 起始页「🐉 日常任务」卡（ui/daily_card.py 的 DailyRunCard）；
  · 流程怎么编排（哪些任务进流程、组队/解散、定时/整理/关机伪步骤、拖拽排序、增删改）
        → 起始页「🐉 日常任务」卡右上角「修改」打开的「日常流程控制」二级弹窗
        （pages/daily_flow.py 的 DailyFlowPage，CTkToplevel）。

本页只做两件事：
  · 按 多人任务 / 单人任务 分区，列出可串联的日常任务（多人组=5 个副本子任务
    60侠士/70侠士/60普通1/60普通2/70普通 + 抓鬼；单人组=8 个单人任务），每行显示任务名 +
    就绪状态（✓ 已就绪 / ⚠ 还需标定）+ 「⚙ 配置」入口（跳「任务配置」页标定/参数）。
    例外：抓鬼行的「抓鬼轮数」可在本页行内直接改（小输入框，失焦/回车即存 tasks.zhuagui
    .loop.max_rounds，与「⚙ 配置」里抓鬼卡同一写入路径/口径）；其它参数仍只读、去配置页改。
  · 【任务名】本身即「单跑」按钮：点它=只跑那一个任务（daily 引擎 + 内存覆盖 steps，
    配置不落盘）。单跑中再点=停止。

聚合「刷副本」步（按勾选顺序一次刷完多本）与它内嵌的「要刷的副本」勾选区**不在本页**：
本页只读清单只列 5 个副本子任务（点哪个刷哪个）；要编排「聚合刷副本」那一步去
起始页「修改」打开的「日常流程控制」弹窗（引擎仍认 dungeon 聚合步，本页不展示、不提供勾选）。

整条龙的 runner/定时等待/急停服务仍由本页单例在后台承载（起始页日常卡转调它），只是本页
不再画出任何可见的运行控件；被起始页日常卡用来镜像按钮文字的 ``btn_run`` 换成一个无 UI 的
``_RunButtonState``（``cget("text")`` / ``configure(...)`` 同形），daily_card 零改动。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import copy

import customtkinter as ctk
from datetime import datetime, timedelta

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...tasks.daily import (CHAINABLE, GROUP_OF, GROUPS, GROUP_TITLES, MULTI_BARRIER,
                            DUNGEON_SUB_TASKS)
from ...tasks.dungeon_base import DUNGEON_CALIBRATION
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ..common import (Card, Tooltip, required_regions, required_templates)

# 只读清单**不展示**聚合「刷副本」行（其勾选区已移除；要刷多本去流程控制弹窗），其余 CHAINABLE 全展示。
_HIDDEN_TASKS = {"dungeon"}
_LIST_TASKS = [t for t in CHAINABLE if t not in _HIDDEN_TASKS]

# 多人组在只读清单里的**固定展示顺序**（用户拍板）：5 个副本子任务按 60侠士/70侠士/
# 60普通1/60普通2/70普通 排列，抓鬼收尾；与「日常流程控制」弹窗的子任务顺序一致。
# 多人组是集体组队跑、展示序不代表引擎执行序（执行序由弹窗里 steps 决定），故固定即可。
_MULTI_ORDER = ["dt_60_xiashi", "dt_70_xiashi",
                "dt_60_common1", "dt_60_common2", "dt_70_common", "zhuagui"]

# 分区集合：与起始页日常卡/引擎同源（tasks.daily.GROUPS）。两区分组固定：多人组在前、
# 个人组在后；GROUP_OF 里出现过的区都保留（以后加新区自动跟上）。
_GROUPS = GROUPS


class _RunButtonState:
    """无 UI 的运行按钮状态替身。

    起始页「日常任务卡」（DailyRunCard）每 tick 读 ``page.btn_run.cget("text")`` 来镜像
    开始/停止/定时等待按钮；本页已不再画该按钮，但整条链 runner 服务仍在，故用这个只存
    「文字/配色」的轻对象顶替——接口与 CTkButton 的 ``cget``/``configure`` 同形，
    DailyRunCard 与历史代码无需改动。"""

    def __init__(self, text):
        self._text = text
        self._state = {"fg_color": T.ACCENT, "hover_color": T.ACCENT_HOVER,
                       "state": "normal"}

    def cget(self, key):
        if key == "text":
            return self._text
        return self._state.get(key)

    def configure(self, *args, **kw):
        if "text" in kw:
            self._text = kw.pop("text")
        self._state.update(kw)

    # 兼容个别历史位置的属性式读写
    config = configure

    def __setitem__(self, key, val):
        self._state[key] = val

    def __getitem__(self, key):
        return self._state.get(key)


class DailyPage(ctk.CTkFrame):
    """日常：只读任务清单 + 点任务名单跑。整条龙 runner 服务由本页单例在后台承载（无可见控件）。

    两区分组固定：多人组（集体组队跑）在前、个人组（每号独立跑）在后，区可折叠但不排序。
    多开/单开与各任务的标定、参数全部在「任务配置」页设置，本页不另设任何流程/设置开关。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "日常"
    RUN_LABEL = "▶  开始日常"

    # 单跑日志的来源短标签（每任务一行；没列出的回退本页 LOG_SOURCE）
    _SINGLE_SOURCE = {
        "treasure_map": "宝图", "secret_realm": "秘境", "appreciation": "趣味鉴赏",
        "sanjie": "奇缘", "escort": "运镖", "guild_checkin": "帮派签到",
        "activity_reward": "活跃度", "zhuagui": "抓鬼", "guaguale": "刮刮乐",
        "dt_60_xiashi": "60侠士", "dt_70_xiashi": "70侠士",
        "dt_60_common1": "60普通1", "dt_60_common2": "60普通2", "dt_70_common": "70普通",
        # 流程伪步骤：链中走到「自动整理背包」步时引擎把来源置为 ORGANIZE_STEP
        "organize": "整理",
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
        "guaguale": "刮刮乐：点福利打开刮刮乐，刮开奖区，有节日奖励就顺手领（领完点「使用」）。",
        "zhuagui": "抓鬼：集体组队，抓完一轮鬼。",
    }

    _GROUP_DESC = {"single": "每号独立跑", "multi": "集体组队跑"}
    _GROUP_EMOJI = {"single": "👤", "multi": "👥"}

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        # —— 整条龙 runner 服务（无可见控件；起始页日常卡转调）——
        self.runner = None
        self.btn_run = _RunButtonState(self.RUN_LABEL)
        self._wait_until = None      # 定时延迟执行：晚于该时刻才真正启动（None=不在等待）
        # —— 单跑 ——
        self.single_runner = None     # 「单跑」专用 runner（与整条龙互斥，由全局唯一运行锁把关）
        self._single_name = None      # 正在单跑的任务名
        self._single_label = None     # 日志里的显示名（如「刷副本」「副本「xxx」」）
        self._single_tag = None       # 单跑日志来源短标签（None=回退 LOG_SOURCE）
        self._single_row = None       # 单跑中的行记录（用于复位就绪标签；行被重建后按 name 现找）
        # —— 只读清单渲染 ——
        self._steps = []          # [{"task","enabled"}]，按区顺序拼段（只用于稳定展示序）
        self._rows = {g: [] for g in _GROUPS}   # 各区的行控件记录
        self._collapsed = {g: False for g in _GROUPS}   # 各区是否折叠
        self._flat = []           # 渲染顺序（区头/行交错），与 list_frame 的 grid 行对齐

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_body()
        self.refresh()

    # ---- 头部 ----
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(bar, text="日常", font=self.fonts["title"], text_color=T.TEXT)
        title.grid(row=0, column=0, sticky="w")
        Tooltip(title, "这里只【列出】日常任务并支持单跑：点「任务名」=只跑那一个任务"
                       "（一次性、不落盘）；副本以 5 个子任务列出（60侠士/70侠士/60普通1/"
                       "60普通2/70普通），点哪个刷哪个。\n"
                       "要编排整条龙（哪些进流程/顺序/组队解散/定时/整理/关机，含聚合「刷副本」"
                       "一次刷多本），去起始页「🐉 日常任务」卡右上角「修改」打开流程控制弹窗；\n"
                       "一键开始整条日常与各设置开关在起始页「🐉 日常任务」卡。\n"
                       "任务按多人组（集体组队跑）/ 单人组（每号独立跑）分区，点区题可折叠；"
                       "各任务的标定、参数请到「任务配置」页（行右侧「⚙ 配置」）。", self.fonts)

    # ---- 主体：只读分区任务清单 ----
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(0, weight=1)
        left.grid_columnconfigure(0, weight=1)

        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(12, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

    # ---- 工具（供起始页「日常任务」卡转调；本页不画按钮）----
    def _adjust_windows(self):
        """「调整窗口」：把所选窗口（最多 5 个）调到基准尺寸并按 2列×2行 排布。"""
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        win_mod.arrange_windows(cfg, self.app)
        self.refresh()

    # ------------------------------------------------------------------
    # 刷新 / 渲染（只读：不再读/写任何流程开关）
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        steps = self._normalize(cfg_mod.task_config(self.app.cfg, self.TASK_NAME).get("steps", []),
                                list(_GROUPS))
        # 内容/就绪签名没变就别重建（切页反复 refresh，整段重画是卡顿来源）。
        if self._steps_signature_of(steps) != getattr(self, "_steps_sig", None):
            self._steps = steps
            self._render_steps()
        self._refresh_step_hints()   # 参数提示（抓鬼轮数等）就地刷新

    @staticmethod
    def _normalize(stored, group_order):
        """把存储的 steps 规整成「含全部要展示的任务」，再按当前区顺序拼段。
        单人组段内保留存储相对顺序；多人组用固定展示序（见 _MULTI_ORDER：5 副本子任务→抓鬼）。
        聚合「刷副本」(dungeon) 不在只读清单展示（其勾选区已移除），故只取 _LIST_TASKS；
        不关心 enabled（只读清单）。"""
        out, seen = [], []
        for s in stored or []:
            if (isinstance(s, dict) and s.get("task") in _LIST_TASKS
                    and s["task"] not in seen):
                out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
                seen.append(s["task"])
        for t in _LIST_TASKS:
            if t not in seen:
                out.append({"task": t, "enabled": True})
                seen.append(t)
        seg = {g: [s for s in out if GROUP_OF[s["task"]] == g] for g in _GROUPS}
        # 多人组用固定展示顺序（5 副本子任务 → 抓鬼）；单人组保留存储相对顺序。
        rank = {t: i for i, t in enumerate(_MULTI_ORDER)}
        seg["multi"].sort(key=lambda s: rank.get(s["task"], len(rank)))
        return [s for g in group_order for s in seg.get(g, [])]

    def _task_title(self, name):
        """任务卡/单跑提示用的显示名：取任务类 title。"""
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        """返回该任务是否已就绪（必需区域+模板都已标定）。
        多人步（5 个副本子任务/抓鬼）额外查（未勾「已组队」时的）组队标定。"""
        if name in DUNGEON_SUB_TASKS:
            return self._dungeon_calib_ok()
        if name in MULTI_BARRIER:
            return self._barrier_step_status(name)
        sub = cfg_mod.task_config(self.app.cfg, name)
        return self._calib_done(name, sub)

    def _calib_done(self, name, sub):
        spec = getattr(get_task(name), "CALIBRATION", None) or {}
        # 每个任务自己的 spec 之外，还要查公共区域/公共模板（tasks.shared）对该任务的必需项
        # （活动列表/背包列表、战斗标识等），统一在「任务配置」页「全局共享标定」卡标定。
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

    def _dungeon_calib_ok(self):
        """副本标定侧就绪（共享 tasks.dungeon 的区域/模板 + 未勾「已组队」时的组队标定）。
        只读清单里的副本只有 5 个子任务行，各点各刷（不依赖聚合 selected），故只看标定。"""
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
        self._steps_sig = self._steps_signature()
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._rows = {"single": [], "multi": []}
        self._flat = []
        for g in list(_GROUPS):
            hdr = self._build_section_header(g)
            self._flat.append(("header", g, hdr))
            for step in self._steps_by_group(g):
                self._rows[g].append(self._build_row(g, step))
                self._flat.append(("row", g, len(self._rows[g]) - 1))
        self._grid_all()

    def _steps_signature(self):
        return self._steps_signature_of(self._steps)

    def _steps_signature_of(self, steps):
        """只读清单渲染签名：每步(任务,就绪)。就绪变化（如标定后切回本页）才重建。"""
        return tuple((s["task"], self._task_status(s["task"])) for s in steps)

    def _ready_meta(self, name):
        """行就绪状态 → (文本, 颜色)：只看标定（5 个副本子任务各刷各的，不依赖聚合勾选）。"""
        if not self._task_status(name):
            return "⚠ 还需标定", T.WARN
        return "✓ 已就绪", T.SUCCESS

    def _style_ready(self, btn, text, color):
        """统一给行就绪标签上样式：一律透明文字样式（非按钮、不可点），仅靠文字颜色表达状态。"""
        btn.configure(text=text, text_color=color,
                      fg_color="transparent", hover_color=T.SURFACE_2,
                      border_color=T.WARN_ON, border_width=0,
                      cursor="arrow", command=None)

    # 卡片里顺带显示的「当前关键参数」：任务名 → 参数描述。取值口径必须和实际生效处一致。
    # 末位 editable=True 的参数在本页行内直接可改（失焦/回车即存）；False/缺省=只读提示。
    _HINT_FROM = {
        # 抓鬼「单跑轮数」= tasks.zhuagui.loop.single_rounds（默认 2）：**只在本页点抓鬼名单独跑
        # 时生效**（_single_build_cfg 用它在内存覆盖 max_rounds，不落盘到整链）；整条龙跑抓鬼仍读
        # 任务配置页的 tasks.zhuagui.loop.max_rounds，两者是相互独立的两个参数（user 2026-11-01）。
        "zhuagui": ("loop", "single_rounds", 2, "轮",
                    "抓鬼单跑轮数（只在本页点「抓鬼」单独跑时生效，不影响整条龙；领任务次数，跑满即停）",
                    True),
    }

    def _step_hint(self, name):
        """该任务卡片要顺带显示的「当前参数」→ (文本, 悬停说明)；没有就 None。"""
        spec = self._HINT_FROM.get(name)
        if not spec:
            return None
        loop_key, key, default, unit, tip = spec[:5]
        val = (cfg_mod.task_config(self.app.cfg, name).get(loop_key) or {}).get(key, default)
        try:
            n = max(1, int(val))          # 与任务侧同口径的下限保护
        except (TypeError, ValueError):
            n = int(default)
        editable = bool(spec[5]) if len(spec) > 5 else False
        if editable:
            detail = f"{tip}（失焦/回车即保存）"
        else:
            detail = f"当前设置：{tip} = {n} {unit}（去右侧「⚙ 配置」里改）"
        return f"{n} {unit}", detail

    def _refresh_step_hints(self):
        """就地刷新各行卡片上的参数提示（不重建行）。"""
        for rows in self._rows.values():
            for r in rows:
                lbl = r.get("hint")
                if lbl is None:
                    continue
                got = self._step_hint(r["name"])
                if got is None:
                    lbl.configure(text="")
                    continue
                spec = self._HINT_FROM.get(r["name"])
                editable = bool(spec and len(spec) > 5 and spec[5])
                if editable:
                    # 可编辑输入框：只在其值与配置里规整后的值不一致时才回填，且不抢正在编辑的焦点，
                    # 避免把用户正在敲的内容/光标顶掉（正常路径 commit 后值已一致，这里通常不再 set）。
                    ent = lbl
                    try:
                        focused = str(self.focus_get()) == str(ent)
                    except Exception:
                        focused = False
                    want = got[0].split()[0]        # "N 轮" → "N"
                    if not focused and ent.get() != want:
                        ent.delete(0, "end")
                        ent.insert(0, want)
                else:
                    lbl.configure(text=got[0])
                tip = r.get("hint_tip")
                if tip is not None:
                    tip.text = got[1]      # Tooltip 每次弹出才读 .text，改它即可换悬停说明

    def _set_hint_param(self, name, raw):
        """行内可编辑参数「改动即保存」：规整后写入对应任务配置（独立键，改动即落盘）。

        目前只有抓鬼「单跑轮数」（tasks.zhuagui.loop.single_rounds，下限 1、非法回退已存值/默认 2）；
        它与任务配置页的 max_rounds 是两个独立参数，互不影响。返回规整后的整数（供 Entry 回填）。"""
        spec = self._HINT_FROM.get(name)
        if not spec or not (len(spec) > 5 and spec[5]):
            return None
        loop_key, key, default = spec[0], spec[1], spec[2]
        try:
            val = max(1, int(float(raw)))
        except (TypeError, ValueError):
            val = None
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, name)
        loopc = tc.setdefault(loop_key, {})
        if val is None:
            loopc.setdefault(key, default)
            val = loopc[key]
        else:
            loopc[key] = val
        cfg_mod.set_task_config(cfg, name, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        return val

    def _open_task_config(self, name):
        """「⚙ 配置」：打开「任务配置」页并滚动定位到该任务的配置卡。"""
        self.app._show("config")
        cp = self.app.pages.get("config")
        if cp is not None and hasattr(cp, "reveal_card"):
            try:
                cp.reveal_card(name)
            except Exception:
                pass

    def _build_section_header(self, g):
        """区头：折叠箭头 + 标题（点它折叠/展开）。纯只读：无整组开关、无勾选计数。"""
        bar = ctk.CTkFrame(self.list_frame, fg_color="transparent")
        bar.grid_columnconfigure(1, weight=1)
        collapsed = self._collapsed.get(g, False)
        chev = ctk.CTkLabel(bar, text="▸" if collapsed else "▾", font=self.fonts["body_b"],
                            text_color=T.TEXT_DIM, width=22, anchor="w")
        chev.grid(row=0, column=0, sticky="w")
        title = ctk.CTkLabel(
            bar,
            text=f"{self._GROUP_EMOJI.get(g, '▪')} {GROUP_TITLES.get(g, g)}"
                 + (f" · {self._GROUP_DESC[g]}" if g in self._GROUP_DESC else ""),
            font=self.fonts["h2"], text_color=T.TEXT, anchor="w")
        title.grid(row=0, column=1, sticky="w", padx=(2, 0))
        for w in (chev, title):
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass
            w.bind("<Button-1>", lambda e, gg=g: self._toggle_collapse(gg))
        Tooltip(title, "点标题折叠/展开本组。本组为只读清单，不能在这里勾选/排序；"
                       "流程编排去起始页「🐉 日常任务」卡右上角「修改」。", self.fonts)
        return bar

    def _build_row(self, g, step):
        """一张只读任务卡：左=占位（与旧版徽标等宽，对齐缩进），中=任务名(可点=单跑)+参数+就绪，
        右=「⚙ 配置」。无手柄、无序号、无启用开关；聚合「刷副本」不在本清单展示（故无副本勾选区）。"""
        name = step["task"]
        row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid_columnconfigure(2, weight=1)

        # 左：与旧版圆形徽标等宽的占位条，纯缩进对齐（只读清单不编号）。
        spacer = ctk.CTkLabel(row, text="", width=26, height=26)
        spacer.grid(row=0, column=0, rowspan=2, padx=(14, 12), pady=5)

        # 中：标题做成「按钮胶囊」（任务名=单跑入口），就绪提示同排。
        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=5)
        mid.grid_columnconfigure(0, weight=0)
        mid.grid_columnconfigure(1, weight=0)   # 参数提示（抓鬼轮数等），按内容自适应
        mid.grid_columnconfigure(2, weight=1)
        chip = ctk.CTkFrame(mid, fg_color=T.BTN, corner_radius=T.RADIUS_SM)
        title = ctk.CTkLabel(chip, text=self._task_title(name), font=self.fonts["body_b"],
                             text_color=T.TEXT, cursor="hand2", anchor="w")
        title._chip_base = T.BTN          # 悬停还原基准色（单跑高亮会就地更新它）
        title._txt_base = T.TEXT
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
                "点任务名 = 单独跑这一个任务（一次性：不改流程、配置不落盘）；单跑中再点这里=停止。",
                self.fonts)

        # 关键参数（如抓鬼轮数）：可编辑参数渲染成「输入框+单位」就地改（失焦/回车即存）；
        # 其余仍是只读文本提示。
        hint_lbl = hint_tip = None
        got = self._step_hint(name)
        if got is not None:
            spec = self._HINT_FROM.get(name)
            editable = bool(spec and len(spec) > 5 and spec[5])
            unit = spec[3] if spec else ""
            if editable:
                box = ctk.CTkFrame(mid, fg_color="transparent")
                box.grid(row=0, column=1, sticky="w", padx=(8, 0))
                hint_lbl = ctk.CTkEntry(box, width=46, font=self.fonts["small"],
                                        justify="center", fg_color=T.SURFACE_2,
                                        border_color=T.BORDER)
                hint_lbl.insert(0, got[0].split()[0])     # "N 轮" → "N"
                hint_lbl.pack(side="left")
                if unit:
                    ctk.CTkLabel(box, text=unit, font=self.fonts["small"],
                                 text_color=T.TEXT_DIM).pack(side="left", padx=(4, 0))
                def _commit(_e=None, nm=name, ent=hint_lbl):
                    val = self._set_hint_param(nm, ent.get())
                    if val is not None:
                        ent.delete(0, "end")
                        ent.insert(0, str(val))
                hint_lbl.bind("<FocusOut>", _commit)
                hint_lbl.bind("<Return>", _commit)
                hint_tip = Tooltip(hint_lbl, got[1], self.fonts)
            else:
                hint_lbl = ctk.CTkLabel(mid, text=got[0], font=self.fonts["small"],
                                        text_color=T.TEXT_DIM, anchor="w")
                hint_lbl.grid(row=0, column=1, sticky="w", padx=(8, 0))
                hint_tip = Tooltip(hint_lbl, got[1], self.fonts)

        # 就绪状态：✓ 已就绪 / ⚠ 还需标定（纯文字，不可点；标定走右侧⚙）。
        st_text, st_color = self._ready_meta(name)
        ready_lbl = ctk.CTkButton(mid, text="", font=self.fonts["small"], height=24,
                                  corner_radius=T.RADIUS_SM, border_width=0)
        self._style_ready(ready_lbl, st_text, st_color)
        ready_lbl.grid(row=0, column=2, sticky="e", padx=(10, 0))

        # 右：任务配置按钮——跳「任务配置」页定位到该任务配置卡（标定/参数，改动即保存）。
        cfg_btn = ctk.CTkButton(row, text="⚙ 配置", font=self.fonts["small"], height=24,
                                corner_radius=T.RADIUS_SM, border_width=0,
                                fg_color=T.BTN, text_color=T.TEXT, cursor="hand2",
                                command=lambda nm=name: self._open_task_config(nm))
        cfg_btn.grid(row=0, column=3, rowspan=2, padx=(4, 14), pady=5)
        cfg_btn.bind("<Enter>",
                     lambda e, b=cfg_btn: b.configure(fg_color=T.ACCENT, text_color=T.ON_ACCENT))
        cfg_btn.bind("<Leave>",
                     lambda e, b=cfg_btn: b.configure(fg_color=T.BTN, text_color=T.TEXT))
        Tooltip(cfg_btn, "打开该任务在「任务配置」页的配置卡（标定 / 参数），改动即保存。", self.fonts)

        return {"frame": row, "name": name, "ready": ready_lbl,
                "ready_text": st_text, "ready_color": st_color,
                "title": title, "chip": chip, "chip_fg": T.BTN, "title_fg": T.TEXT,
                "hint": hint_lbl, "hint_tip": hint_tip, "group": g, "step": step}

    def _grid_all(self):
        """按 _flat 顺序把区头与行落回 list_frame 的 grid 行位；折叠区的行 grid_forget 不占位。"""
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

    def _toggle_collapse(self, g):
        """折叠/展开分区：只改 group 级折叠状态 + 重排可见行（不销毁控件、不重建整页）。"""
        self._collapsed[g] = not self._collapsed.get(g, False)
        self._grid_all()

    # ------------------------------------------------------------------
    # 定时延迟（整条龙 runner 服务；本页无可见控件，起始页日常卡转调）
    # ------------------------------------------------------------------
    @staticmethod
    def _sched_wait_target(sched, now):
        """把定时串 "HH:MM" 换算成「下一次该时刻 datetime」：今天该时刻还没到就用今天的；
        已过（含恰好到点）顺延到明天该时刻。非法/空=不等待(None)。"""
        if not sched or ":" not in sched:
            return None
        try:
            hh, mm = sched.split(":")
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError):
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return target

    # ------------------------------------------------------------------
    # 整条龙运行控制（runner 服务；UI 在起始页日常卡，save=False 由该卡先自行落盘设置）
    # ------------------------------------------------------------------
    def _toggle_run(self, save=True):
        """开始/停止整条日常。save=False：调用方（起始页日常卡）已在点击瞬间自行落盘设置，
        这里不再落盘，避免用过期值回写覆盖。"""
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
        # 正常启动路径：先看有没有设置定时延迟执行（设置只由起始页日常卡落盘，本页不写）
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        sched = (tc.get("loop", {}) or {}).get("schedule", "") or ""
        target = self._sched_wait_target(sched, datetime.now())
        if target is not None:
            self._wait_until = target
            self.btn_run.configure(text=f"等待到 {target:%m-%d %H:%M}…（再点取消）",
                                   fg_color=T.WARN, hover_color=T.WARN_HOVER, state="normal")
            self._log_line(f"已设定时延迟执行：将等到 {target:%Y-%m-%d %H:%M} 才开始（再点一次按钮可取消）。", "warn")
            return
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
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER,
                               hover_color=T.DANGER_HOVER, state="normal")

    def _on_runner_finished(self):
        self.runner = None
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")

    # ------------------------------------------------------------------
    # 单跑单个任务：daily 引擎 + 内存覆盖 steps（不落盘、不动流程）
    # ------------------------------------------------------------------
    def _single_blocked(self):
        """整条龙在跑 / 定时等待中 → 单跑不可启动。返回 True=被挡（并已提示）。"""
        if self.runner and self.runner.is_running():
            self._log_line("整条日常正在跑，请先在起始页「日常任务」卡「停止」后再「单跑」单独任务。", "warn")
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
    def _single_build_cfg(cfg, name):
        """单跑用运行配置：只改内存深拷贝，绝不触盘。steps=只含这一个任务（整步语义；
        5 个副本子任务=只含该子任务名，引擎据此只集体刷那一本）。
        同时忽略整链设置：单跑只按这个任务自己的流程走——不套整体时间上限、绝不跑完关机、
        不做定时等待、不按「跑完解散」收尾解散、不触发「自动整理背包」；「已组队」是当前会话的
        组队条件、属于共享组队设置，不动。抓鬼单跑额外用日常页的「单跑轮数」single_rounds 在内存
        顶替 max_rounds（只影响这一趟单跑，不改整链抓鬼轮数）。"""
        run_cfg = copy.deepcopy(cfg)
        daily = run_cfg.setdefault("tasks", {}).setdefault("daily", {})
        daily["steps"] = [{"task": name, "enabled": True}]
        # 标记「steps 就是全部、别再补缺行」——否则引擎的缺行补齐会把其它 CHAINABLE 补回来，
        # 单跑会跑成整条日常链（只影响本内存覆盖，不落盘）。
        daily["explicit_steps"] = True
        loop = daily.setdefault("loop", {})
        loop["time_limit_min"] = 0          # 不套整条龙的整体时间上限
        loop["shutdown_after"] = False      # 单跑绝对不触发「跑完关机」
        loop.pop("schedule", None)          # 不做定时等待
        run_cfg.setdefault("tasks", {}).setdefault("teaming", {})["auto_disband"] = False
        run_cfg.setdefault("tasks", {}).setdefault("organize_bag", {})["auto_organize"] = False
        if name == "zhuagui":
            # 抓鬼单跑用「日常页单跑轮数」single_rounds（独立于整链的 max_rounds）：只在这份内存
            # 覆盖里把 ZhuaguiTask 读的 max_rounds 顶替掉，不落盘、不影响整条龙跑抓鬼（user 2026-11-01）。
            zg = run_cfg.setdefault("tasks", {}).setdefault("zhuagui", {})
            zloop = zg.setdefault("loop", {})
            try:
                zloop["max_rounds"] = max(1, int(zloop.get("single_rounds", 2)))
            except (TypeError, ValueError):
                zloop["max_rounds"] = max(1, int(2))
        return run_cfg

    def _start_single(self, name):
        """行级单跑：只跑这一个任务。单跑进行中再点任意「单跑」=停止当前单跑。"""
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
        self._fire_single(name, self._single_build_cfg(self.app.cfg, name), title)

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
        self._log_line(f"★ 单跑「{label}」：只跑这一个，其余不动（不改流程、配置不落盘）★", "warn")

    def _mark_single_row(self, name):
        """把正在单跑的任务行高亮：标题胶囊变主题蓝（文字反白）+ 就绪标签改「▶ 正在单跑」。"""
        for g in self._rows:
            for r in self._rows[g]:
                if r["name"] == name:
                    self._single_row = r
                    self._style_title(r, T.ACCENT, T.ON_ACCENT)
                    self._style_ready(r["ready"], "▶ 正在单跑", T.ACCENT)
                    return
        self._single_row = None

    def _restore_single_row(self):
        """复位单跑行（标题胶囊/就绪标签回构建时原样）。行可能已被重建：经记录引用还原，失败则静默。"""
        r = self._single_row
        self._single_row = None
        if r is not None:
            try:
                self._style_title(r, r["chip_fg"], r["title_fg"])
                self._style_ready(r["ready"], r["ready_text"], r["ready_color"])
            except Exception:
                pass

    def _style_title(self, rec, chip_fg, txt):
        """就地换某行标题胶囊配色，并同步「悬停还原基准色」（避免悬停后变回旧色）。"""
        rec["chip"].configure(fg_color=chip_fg)
        rec["title"].configure(text_color=txt)
        rec["title"]._chip_base = chip_fg
        rec["title"]._txt_base = txt

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
            default_tag = getattr(self, "LOG_SOURCE", None)
            for level, msg, src in self.runner.drain_logs():
                tag = self._SINGLE_SOURCE.get(src, default_tag) if src else default_tag
                self.app.log_line(msg, level, tag)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()
        elif self._wait_until is not None:
            # 定时等待中：到点才启动
            if datetime.now() >= self._wait_until:
                self._wait_until = None
                self._log_line("⏰ 定时时刻到，开始日常…", "warn")
                self._start_runner()
        if self.single_runner:
            tag = self._single_tag or getattr(self, "LOG_SOURCE", None)
            for level, msg, _src in self.single_runner.drain_logs():
                self.app.log_line(msg, level, tag)
            if not self.single_runner.is_running():
                self._single_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
