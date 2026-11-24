# -*- coding: utf-8 -*-
"""起始页「日常任务卡」：把「日常」链的运行入口（开始/停止 · 调整窗口/刷新）与
「已勾选任务」清单搬到起始页，做成「一眼可跑」的主面板。

设计要点（改这里前先读）：
- 本卡**只放运行控制 + 只读执行链**（user 2026-11-01 拍板）：整体时间上限、跑完解散、跑完关机、
  定时延后、自动整理背包、已组队、自动拓印等开关/设置一律不在本卡出现；它们在「日常流程控制」
  二级弹窗（定时/关机/整理/时间上限/拓印）及对应配置页（组队、整理背包、拓印）里改，引擎统一
  读 config，本卡不再持有这些变量、也不写这些命名空间。
- 运行仍由「日常」页单例持有的 runner 承载——本卡只是入口 + 状态镜像：点「开始日常」=
  调 ``DailyPage._toggle_run(save=False)``（本卡不写任何设置，无需先落盘）；按钮文字/配色每
  tick 从 DailyPage 的 runner 镜像（见 ``pump``）。
- 「已勾选任务」清单**只读**：只列出当前启用、会真正跑的任务（按执行顺序 + 就绪状态），
  勾选/排序/流程设置点该清单右上角「修改」打开「日常流程控制」二级弹窗改。
- 整卡可折叠：点标题/箭头收起内容（清单），只留标题与「开始日常」按钮。
"""

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core.runner import TaskRunner
from ..core.teaming import (TEAM_CALIBRATION, TEAM_REQUIRED_REGIONS,
                            TEAM_REQUIRED_TEMPLATES, DISBAND_REQUIRED_TEMPLATES)
from ..tasks import get_task
from ..tasks.daily import (CHAINABLE, TEAM_STEPS, TEAM_STEP_TITLES,
                           ORGANIZE_STEP, FLOW_TIMING_STEP, FLOW_SHUTDOWN_STEP,
                           DUNGEON_SUB_TASKS, MULTI_BARRIER,
                           get_selected_dungeon_titles, normalize_flow_steps)
from ..tasks.dungeon_base import DUNGEON_CALIBRATION
from .common import (Tooltip, required_regions, required_templates,
                     optional_templates)

# 流程摘要节点的三态（user 2026-11-25）：必标项缺=红、必标齐但可选未标=黄、全标=绿；无标定概念=中性。
NODE_RED, NODE_YELLOW, NODE_GREEN, NODE_NEUTRAL = "red", "yellow", "green", "neutral"
_NODE_TAG = {NODE_RED: "node_red", NODE_YELLOW: "node_yellow",
             NODE_GREEN: "node_green", NODE_NEUTRAL: "node_neutral"}

# 摘要/执行链的顺序一律以流程控制弹窗口径为准（tasks.daily.normalize_flow_steps），
# 起始页、弹窗、引擎共用同一来源；这里不再另算「多人在前」的按区展示序。


def task_title(name, cfg=None):
    """任务显示名：刷副本展开为当前勾选的副本标题列表（按展示顺序），其余取任务类的 title。"""
    if name == "dungeon" and cfg is not None:
        titles = get_selected_dungeon_titles(cfg)
        if titles:
            return " -> ".join(titles)
        return "刷副本（未勾选）"
    cls = get_task(name)
    return cls.title if cls else name


def enabled_chain_entries(cfg):
    """本趟真正执行的【摘要用】条目列表，顺序/口径与流程控制弹窗、引擎 `_enabled_steps`
    严格同源（都走 tasks.daily.normalize_flow_steps），保证「弹窗顺序 == 起始页摘要 == 实跑顺序」。

    严格按规整后的存储顺序输出：
    - 普通 CHAINABLE 任务：只取启用的（去重、隐藏聚合 dungeon，缺省任务按 CHAINABLE 补末尾）；
    - 组队/解散、自动整理背包：可重复出现，按各自存储位置逐个计入（停用的不计）；
    - 定时/关机不在此输出（chain_parts 按 loop.schedule / shutdown_after 贴链首/链尾）。
    返回每项为 (kind, name)：kind∈{"task","team","organize"}。"""
    d = ((cfg.get("tasks", {}) or {}).get("daily", {}) or {})
    legacy = {
        FLOW_TIMING_STEP: ((d.get("loop", {}) or {}).get("schedule", "") or ""),
        FLOW_SHUTDOWN_STEP: bool((d.get("loop", {}) or {}).get("shutdown_after", False)),
        ORGANIZE_STEP: bool(((cfg.get("tasks", {}) or {}).get("organize_bag", {}) or {})
                            .get("auto_organize", False)),
    }
    rows = normalize_flow_steps(d.get("steps") or [], d.get("removed_tasks") or (), legacy)
    out = []
    for row in rows:
        t = row.get("task")
        if not t or not row.get("enabled", True):
            continue
        if t == ORGANIZE_STEP:
            out.append(("organize", t))
        elif t in TEAM_STEPS:
            out.append(("team", t))
        elif t in CHAINABLE and t != "dungeon":
            out.append(("task", t))
    return out


def chain_task_names(cfg):
    """本趟真正会跑的【普通任务】名，按执行顺序（=enabled_chain_entries 里的 task 项）。
    不含组队/解散/整理/定时/关机等伪步骤，也不含聚合 dungeon（清单只展示 5 个独立副本子任务）。"""
    return [name for kind, name in enabled_chain_entries(cfg) if kind == "task"]


# 流程控制伪步骤（与 daily_flow.FLOW_STEPS/FLOW_TITLES 同义，这里就地定义避免与页面包循环导入）。
# 定时固定在链开头、关机固定在链结尾；「整理背包」可由用户拖到任意任务之间，摘要按存储位置插入。
_FLOW_TAGS = (("timing", "⏰定时"), ("organize", "🎒整理背包"), ("shutdown", "🔌关机"))


def chain_parts(cfg):
    """起始页执行链文本片段（不含「(共N项)」），镜像引擎实际执行顺序（=弹窗摘要同口径）。

    中段严格按存储 steps 顺序：普通任务 + 可重复的组队/解散/自动整理背包（后两者各显示在被拖到的
    位置，整条链可出现多个）；定时固定贴链首、关机固定贴链尾（引擎固定语义）。
    定时不显示固定的「定时」字样，而是带上真正的启动时刻——「⏰HH:MM开始执行」
    （user 2026-11-01；时刻取引擎读的 tasks.daily.loop.schedule，与弹窗定时行 time 同源）。"""
    tags = dict(_FLOW_TAGS)
    middle = []
    for kind, name in enabled_chain_entries(cfg):
        if kind == "organize":
            middle.append(tags["organize"])
        elif kind == "team":
            middle.append(TEAM_STEP_TITLES.get(name, name))
        else:
            middle.append(task_title(name, cfg))
    flow_on = set(enabled_flow_tags(cfg))
    parts = []
    schedule = ((((cfg.get("tasks", {}) or {}).get("daily", {}) or {}).get("loop", {}) or {})
                .get("schedule", "") or "").strip()
    if tags["timing"] in flow_on:
        # 有具体时刻就显示「⏰HH:MM开始执行」；时刻意外为空（理论上开定时必有值）才退回固定标签
        parts.append(f"⏰{schedule}开始执行" if schedule else tags["timing"])
    parts += middle
    if tags["shutdown"] in flow_on:
        parts.append(tags["shutdown"])
    return parts


def enabled_flow_tags(cfg):
    """当前开启的流程控制标签，按「定时→整理→关机」的固定生效顺序返回。

    直接读引擎实际消费的旧字段（弹窗 _save_steps 与起始页开关都同步到这里）：
    定时=loop.schedule 非空、关机=loop.shutdown_after、整理=organize_bag.auto_organize。
    这样无论从起始页开关还是流程弹窗切换，摘要都反映真实生效状态。"""
    tasks = cfg.get("tasks", {}) or {}
    d = tasks.get("daily", {}) or {}
    loop = d.get("loop", {}) or {}
    on = {
        "timing": bool(loop.get("schedule", "")),
        "organize": bool((tasks.get("organize_bag", {}) or {}).get("auto_organize", False)),
        "shutdown": bool(loop.get("shutdown_after", False)),
    }
    return [tag for key, tag in _FLOW_TAGS if on[key]]


# ----------------------------------------------------------------------
# 流程节点标定三态（user 2026-11-25）：红=有必标项未标 / 黄=必标齐但可选【模板】未全标 / 绿=全标
# 必选/可选集合一律从任务 CALIBRATION spec 推导（与各配置页 calib_status、日常页 _calib_done 同源），
# 再叠加该任务的共享必选区域/模板（TASK_SHARED_*）。无标定概念的流程动作（定时/关机）= 中性。
# 注意：可选【区域】（如 scene 主识别区，留空=整窗当识别区是推荐用法）不参与黄/绿——只有可选
# 模板没标才显黄，避免「按推荐留空 scene」这种健康状态被误染成黄（user 2026-11-25 拍板）。
# ----------------------------------------------------------------------
def _state_from_spec(spec, regions, templates, task_name, cfg, *, team_gate=False):
    """按一份 CALIBRATION spec + 已叠加共享的 regions/templates 算红/黄/绿。

    team_gate=True 时（抓鬼/副本这类多人步），未勾「已组队」还要把组队必标项算进必选，
    口径与 DailyPage._barrier_step_status/_dungeon_calib_ok 保持一致。"""
    # 自身必选 + 该任务的共享必选（共享区域/模板都按必选处理，缺了跑不起来 → 红）
    req_r = list(required_regions(spec)) + list(cfg_mod.TASK_SHARED_REQ.get(task_name, ()))
    req_t = list(required_templates(spec)) + list(cfg_mod.TASK_SHARED_TPL_REQ.get(task_name, ()))
    if team_gate:
        team = cfg_mod.task_config(cfg, "teaming")
        if not team.get("skip_team", False):      # 没勾「已组队」：组队标定也是必选
            treg, ttpl = team.get("regions", {}), team.get("templates", {})
            if any(not treg.get(k) for k in TEAM_REQUIRED_REGIONS) or \
               any(not ttpl.get(k) for k in TEAM_REQUIRED_TEMPLATES):
                return NODE_RED
    if any(not regions.get(k) for k in req_r) or any(not templates.get(k) for k in req_t):
        return NODE_RED
    # 必选齐了：还有可选【模板】没标 → 黄；全标 → 绿。
    # 可选区域（scene/listing 等，留空有推荐默认行为）不计入，避免推荐用法被误判为未就绪。
    opt_t = optional_templates(spec)
    if any(not templates.get(k) for k in opt_t):
        return NODE_YELLOW
    return NODE_GREEN


def _task_node_state(name, cfg):
    """普通 CHAINABLE 任务 / 抓鬼 / 副本子任务的三态。"""
    # 副本（5 个子任务 + 聚合 dungeon）共用共享 tasks.dungeon 与 DUNGEON_CALIBRATION
    if name == "dungeon" or name in DUNGEON_SUB_TASKS:
        sub = cfg_mod.task_config(cfg, "dungeon")
        return _state_from_spec(DUNGEON_CALIBRATION, sub.get("regions", {}), sub.get("templates", {}),
                                "dungeon", cfg, team_gate=True)
    cls = get_task(name)
    if cls is None:
        return NODE_NEUTRAL                 # 未知/已下线任务名：无标定可判，中性，不误导成绿色
    spec = getattr(cls, "CALIBRATION", None) or {"regions": [], "templates": []}
    sub = cfg_mod.task_config(cfg, name)
    multi_barrier = name in MULTI_BARRIER     # 抓鬼等多人步：未勾已组队时查组队标定
    return _state_from_spec(spec, sub.get("regions", {}), sub.get("templates", {}),
                            name, cfg, team_gate=multi_barrier)


def _team_node_state(name, cfg):
    """组队/解散伪步骤的三态（标定都在共享 tasks.teaming）。"""
    team = cfg_mod.task_config(cfg, "teaming")
    treg, ttpl = team.get("regions", {}), team.get("templates", {})
    if name == "team_disband":
        # 解散只依赖「退出队伍」按钮这一个必选模板
        return NODE_RED if any(not ttpl.get(k) for k in DISBAND_REQUIRED_TEMPLATES) else NODE_GREEN
    # 组队：必选区域 + 必选模板齐了才算过；TEAM_CALIBRATION 里其余（team_arrow 可选/team_quit 解散用等）
    # 没标 → 黄。leader_id 在「标定队长ID」单独维护，不计入这里的可选计数。
    if any(not treg.get(k) for k in TEAM_REQUIRED_REGIONS) or \
       any(not ttpl.get(k) for k in TEAM_REQUIRED_TEMPLATES):
        return NODE_RED
    opt_t = [it[0] for it in TEAM_CALIBRATION["templates"]
             if it[0] not in TEAM_REQUIRED_TEMPLATES and it[0] != "leader_id"]
    return NODE_YELLOW if any(not ttpl.get(k) for k in opt_t) else NODE_GREEN


def _organize_node_state(cfg):
    """整理背包伪步骤三态：必选模板取决于已配置物品用到的动作（与 OrganizeBagTask.preflight 同口径）。"""
    from ..core.inventory import required_templates as inv_required
    ob = cfg_mod.task_config(cfg, "organize_bag")
    items = [it for it in ob.get("items", []) if it.get("template")]
    if not items:
        return NODE_RED                    # 一个要整理的物品都没有 → 跑了也白跑，按未就绪（红）
    templates = ob.get("templates", {})
    seen, missing_req = set(), False
    for act in {it.get("action", "use") for it in items}:
        for key, _lbl in inv_required(act):
            if key in seen:
                continue
            seen.add(key)
            if not templates.get(key):
                missing_req = True
    if missing_req:
        return NODE_RED
    # 必选齐：CALIBRATION 里标了可选的动作按钮（如 more_button）没标 → 黄，否则绿
    spec = getattr(get_task("organize_bag"), "CALIBRATION", None) or {}
    opt_t = optional_templates(spec)
    return NODE_YELLOW if any(not templates.get(k) for k in opt_t) else NODE_GREEN


def node_calib_state(kind, name, cfg):
    """链上单个节点 → NODE_RED / NODE_YELLOW / NODE_GREEN / NODE_NEUTRAL。

    kind 与 enabled_chain_entries 的输出对齐：task/team/organize；定时/关机由调用方按中性处理。"""
    try:
        if kind == "team":
            return _team_node_state(name, cfg)
        if kind == "organize":
            return _organize_node_state(cfg)
        return _task_node_state(name, cfg)
    except Exception:
        # 判定异常绝不阻断摘要渲染：退回中性，避免单个任务配置问题让整张卡空白
        return NODE_NEUTRAL


class DailyRunCard(ctk.CTkFrame):
    """日常运行控制卡（起始页用）。构造后由宿主页 refresh()/pump() 驱动。"""

    RUN_LABEL = "▶  开始日常"

    def __init__(self, master, app, fonts):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = fonts
        self.grid_columnconfigure(0, weight=1)
        self._open = True        # 内容（执行链清单）是否展开
        self._run_sig = None     # 上一次套到按钮上的 (文字, 风格) 签名，用于去抖（见 sync_run）
        # 一键组队/一键解散各自的后台 runner（全局唯一运行锁由 TaskRunner 保证，不会与日常链并发）
        self.runner_team = None
        self.runner_disband = None

        self._build_head()
        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, sticky="ew")
        self._body.grid_columnconfigure(0, weight=1)
        ctk.CTkFrame(self._body, fg_color=T.BORDER, height=1).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._build_checklist(self._body)

    # ------------------------------------------------------------------
    # 头部（常驻）：折叠箭头 + 标题 + 计数 + 按钮
    # ------------------------------------------------------------------
    def _build_head(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        head.grid_columnconfigure(2, weight=1)
        self._chev = ctk.CTkLabel(head, text="▾", font=self.fonts["h2"], text_color=T.TEXT_DIM,
                                  cursor="hand2", width=22, anchor="w")
        self._chev.grid(row=0, column=0, sticky="w")
        ttl = ctk.CTkLabel(head, text="🐉 日常任务", font=self.fonts["h2"], text_color=T.TEXT,
                           cursor="hand2", anchor="w")
        ttl.grid(row=0, column=1, sticky="w")
        for w in (self._chev, ttl):
            w.bind("<Button-1>", lambda e: self._toggle_body())
        Tooltip(ttl, "整条「日常」链的运行入口：一眼看清这趟会按什么顺序跑哪些任务，直接开跑。"
                     "点标题/箭头可收起或展开；任务顺序与定时/关机/整理等流程设置点下方「修改」，"
                     "在「日常流程控制」弹窗里调整。", self.fonts)
        self.lbl_badge = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM, anchor="w")
        self.lbl_badge.grid(row=0, column=2, sticky="w", padx=(10, 0))

        # 按钮另起一行（user 2026-11-24：不再挤在标题同一行），左对齐排开，窄窗口也不压标题。
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ctk.CTkButton(btns, text="调整窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._adjust_windows).pack(side="left")
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left", padx=(8, 0))
        # 一键组队/解散（与任务配置页组队卡同源：组队=get_task("dungeon")，解散=get_task("disband")）。
        # 放在起始页日常卡，省得为单独组队切到配置页；标定/队长ID 仍在配置页组队卡维护。
        self.btn_team = ctk.CTkButton(
            btns, text="👥  一键组队", font=self.fonts["body"], height=36, width=104,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
            border_width=1, border_color=T.BORDER, command=self._toggle_team)
        self.btn_team.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_team, "让所选多开窗口自动握手组队（队长建队→队员申请→队长接受）。"
                               "组队标定/队长ID 在「任务配置→组队（共享）」卡维护。", self.fonts)
        self.btn_disband = ctk.CTkButton(
            btns, text="✖  一键解散", font=self.fonts["body"], height=36, width=104,
            corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
            border_width=1, border_color=T.BORDER, command=self._toggle_disband)
        self.btn_disband.pack(side="left", padx=(8, 0))
        Tooltip(self.btn_disband, "让所选多开窗口里的每个号都退出当前队伍（所有人退队）。"
                                  "退队模板（退出队伍按钮）在「任务配置→组队（共享）」卡里标定。", self.fonts)
        self.btn_run = ctk.CTkButton(btns, text=self.RUN_LABEL, font=self.fonts["btn"], height=36, width=150,
                                     corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                     text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.pack(side="left", padx=(8, 0))

    def _toggle_body(self):
        self._open = not self._open
        if self._open:
            self._body.grid()
            self._chev.configure(text="▾", text_color=T.TEXT_DIM)
        else:
            self._body.grid_remove()
            self._chev.configure(text="▸", text_color=T.TEXT_DIM)

    # ------------------------------------------------------------------
    # 已勾选任务清单（只读）：一条「刷副本 -> 抓鬼 -> 宝图 ...」的执行链 + 就绪摘要
    # ------------------------------------------------------------------
    def _build_checklist(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=1, column=0, sticky="ew", pady=(2, 2))
        box.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="已勾选任务", font=self.fonts["body_b"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="修改", font=self.fonts["small"], height=28, width=72,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._edit_flow).grid(row=0, column=2, sticky="e")

        # 执行链：用只读 CTkTextbox 承载，按节点三态分段着色（单色 CTkLabel 无法一段内多色）。
        # wrap=word 自动折行；state=disabled 只读；高度随内容在 _render_flow 里按行数自适应。
        self.txt_chain = ctk.CTkTextbox(box, font=self.fonts["body"], fg_color="transparent",
                                        text_color=T.TEXT, corner_radius=0, wrap="word",
                                        activate_scrollbars=False, height=24)
        self.txt_chain.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self._apply_chain_tags()
        Tooltip(self.txt_chain, "点「开始日常」会依次跑的任务，按流程控制弹窗里设置的实际执行先后拼接"
                                "（含定时/关机/组队/解散/整理背包等流程动作）。颜色：红=有必标项未标，"
                                "黄=必标齐但可选未标，绿=全部标定。调整顺序或这些流程设置，点右上角「修改」。",
                self.fonts)

        self.lbl_ready = ctk.CTkLabel(box, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      anchor="w", justify="left")
        self.lbl_ready.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        T.bind_wraplength(self.lbl_ready)

    # 三态 → 底层 tk Text 的 tag 配色（只收单值颜色，按当前外观解析；明暗切换后重跑本方法）。
    _CHAIN_TAG_COLOR = {
        "node_red": T.DANGER, "node_yellow": T.WARN,
        "node_green": T.SUCCESS, "node_neutral": T.TEXT_DIM,
        "sep": T.TEXT_DIM, "count": T.TEXT_DIM,
    }

    def _apply_chain_tags(self):
        """给执行链 Textbox 的各 tag 配前景色（随当前外观模式解析）。失败静默，不影响主流程。"""
        tb = getattr(self, "txt_chain", None)
        if tb is None:
            return
        for tag, token in self._CHAIN_TAG_COLOR.items():
            try:
                tb._textbox.tag_config(tag, foreground=T.resolve(token))
            except Exception:
                pass

    def _render_flow(self):
        """重绘执行链（按节点三态分段着色）+ 就绪摘要（就地改文本，不重建控件）。

        执行链节点由 enabled_chain_entries 统一生成（镜像引擎执行序）：普通任务/副本/抓鬼按
        node_calib_state 着红/黄/绿，组队/解散/整理背包各查各自标定，定时/关机无标定概念走中性色；
        「->」分隔符与「(共N项)」用次要色。「共N项」= 真实执行步骤总数，与弹窗行前最大序号同口径。"""
        cfg = self.app.cfg
        entries = enabled_chain_entries(cfg)
        total = self._chain_total(cfg)
        tb = self.txt_chain
        tb.configure(state="normal")
        tb._textbox.delete("1.0", "end")
        if not entries and total <= 0:
            tb._textbox.insert("end", "（无，开始日常只会空转）", "node_neutral")
        else:
            # 定时固定链首、关机固定链尾（无标定概念 → 中性色）；口径与旧 chain_parts 完全一致，
            # 中段 entries 才按各自标定三态着色。这样显示节点数与「共N项」始终对得上。
            schedule = ((((cfg.get("tasks", {}) or {}).get("daily", {}) or {}).get("loop", {}) or {})
                        .get("schedule", "") or "").strip()
            flow_on = set(enabled_flow_tags(cfg))
            timing_tag = dict(_FLOW_TAGS)["timing"]
            shutdown_tag = dict(_FLOW_TAGS)["shutdown"]
            seg = []        # (文本, tag)
            if timing_tag in flow_on:
                seg.append((f"⏰{schedule}开始执行" if schedule else timing_tag, "node_neutral"))
            for kind, name in entries:
                seg.append((self._entry_label(kind, name, cfg),
                            _NODE_TAG[node_calib_state(kind, name, cfg)]))
            if shutdown_tag in flow_on:
                seg.append((shutdown_tag, "node_neutral"))
            for idx, (text, tag) in enumerate(seg):
                if idx:
                    tb._textbox.insert("end", " -> ", "sep")
                tb._textbox.insert("end", text, tag)
            # 「共N项」= chain_parts 长度（含定时/关机），与弹窗行前最大序号同口径
            tb._textbox.insert("end", f" (共{total}项)", "count")
        tb.configure(state="disabled")
        self._fit_chain_height()

        names = chain_task_names(cfg)
        not_ready = [task_title(n, cfg) for n in names if not self._ready_bool(n)]
        if not names:
            self.lbl_ready.configure(text="")
        elif not_ready:
            self.lbl_ready.configure(text="⚠ 未就绪：" + "、".join(not_ready) + "（点「修改」在流程控制里看该行为何未就绪）",
                                     text_color=T.WARN)
        else:
            self.lbl_ready.configure(text="✓ 全部就绪", text_color=T.SUCCESS)
        self._refresh_badge()

    def _ready_bool(self, name):
        """单任务就绪布尔：委托「日常」页的 _task_status（避免两处各算一套标定判定）。"""
        page = self.app.pages.get("daily")
        if page is not None and hasattr(page, "_task_status"):
            try:
                return bool(page._task_status(name))
            except Exception:
                return True
        return True

    def _entry_label(self, kind, name, cfg):
        """单个链节点的显示文案（与旧 chain_parts 中段口径一致）。"""
        if kind == "organize":
            return dict(_FLOW_TAGS)["organize"]
        if kind == "team":
            return TEAM_STEP_TITLES.get(name, name)
        return task_title(name, cfg)

    def _chain_total(self, cfg):
        """「共N项」口径 = chain_parts 长度（含贴链首/链尾的定时、关机），与旧实现 len(parts) 一致。"""
        return len(chain_parts(cfg))

    def _fit_chain_height(self):
        """按 Textbox 实际折行行数自适应高度（透明底、无滚动条，避免固定高度留白或裁切）。

        先 update_idletasks 让 Tk 重排文本，再用 count 取显示行数；行高取字体度量，留出上下内边距。"""
        try:
            tb = self.txt_chain
            tb.update_idletasks()
            # count -displaylines 给出折行后的实际显示行数（Tk 8.5+ 支持）
            n_lines = max(1, int(tb._textbox.count("1.0", "end", "displaylines")[0]))
            line_h = int(self.fonts["body"].metrics("linespace"))
            tb.configure(height=max(24, n_lines * line_h + 8))
        except Exception:
            pass

    def _refresh_badge(self):
        # 徽标数本趟真正会跑的【普通任务】（执行序，不含组队/解散/整理/定时/关机等伪步骤）
        n = len(chain_task_names(self.app.cfg))
        self.lbl_badge.configure(text=f"已勾选 {n} 个" if n else "未勾选任务",
                                 text_color=T.ACCENT if n else T.TEXT_DIM)

    # ------------------------------------------------------------------
    # 运行控制（镜像 DailyPage 的 runner）
    # ------------------------------------------------------------------
    def _daily_page(self):
        try:
            page, _just = self.app._ensure_page("daily")
            return page
        except Exception:
            return None

    def _edit_flow(self):
        """「修改」：打开「日常流程控制」二级弹窗（不再跳「日常」页）。关闭后刷新本卡勾选清单。"""
        try:
            from .pages.daily_flow import DailyFlowPage
        except Exception:
            return

        def _on_done():
            try:
                self.refresh()
            except Exception:
                pass

        try:
            DailyFlowPage.open(self.app, on_done=_on_done)
        except Exception:
            pass

    def _adjust_windows(self):
        page = self._daily_page()
        if page is not None and hasattr(page, "_adjust_windows"):
            page._adjust_windows()

    def _toggle_run(self):
        page = self._daily_page()
        if page is None:
            return
        # 本卡不再持有任何可编辑设置（时间上限/关机/定时/解散/整理/已组队/拓印都在流程弹窗或各自
        # 配置页改、各自落盘），故这里直接让 DailyPage 按当前 config 启动，无需先保存。
        page._toggle_run(save=False)
        self.sync_run()

    # ------------------------------------------------------------------
    # 一键组队 / 一键解散（与任务配置页组队卡同源，runner 挂在本卡上）
    # ------------------------------------------------------------------
    def _start_utility(self, task_name, runner_attr, btn, busy_text, log_tag):
        """通用：启动一个工具型任务（组队=dungeon / 解散=disband）。返回 runner；启动失败返回 None。
        全局唯一运行锁在 TaskRunner.start 内拦截并发（日常链在跑时这里会被拒绝并 toast）。"""
        task_cls = get_task(task_name)
        if task_cls is None:
            self.app.log_line(f"找不到{log_tag}任务。", "error", log_tag)
            return None
        runner = TaskRunner(task_cls(), cfg_mod.load_config())
        ok, problems = runner.start()
        if not ok:
            for p in problems:
                self.app.log_line(f"无法开始{log_tag}：" + p, "error", log_tag)
            return None
        setattr(self, runner_attr, runner)
        btn.configure(text=busy_text, fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                      text_color=T.ON_ACCENT, border_width=0, state="normal")
        return runner

    def _toggle_team(self):
        # 运行中再点 = 停止（与配置页组队卡一致）
        if self.runner_team and self.runner_team.is_running():
            self.runner_team.stop()
            self.btn_team.configure(text="停止中…", state="disabled")
            return
        self._start_utility("dungeon", "runner_team", self.btn_team,
                            "■  停止组队", "组队")

    def _toggle_disband(self):
        if self.runner_disband and self.runner_disband.is_running():
            self.runner_disband.stop()
            self.btn_disband.configure(text="停止中…", state="disabled")
            return
        self._start_utility("disband", "runner_disband", self.btn_disband,
                            "■  停止解散", "解散")

    def _pump_utility(self, runner, btn, normal_text, log_tag):
        """抽干一个工具 runner 的日志到全局面板，跑完把按钮复位。"""
        if not runner:
            return
        for level, msg, _src in runner.drain_logs():
            self.app.log_line(msg, level, log_tag)
        if not runner.is_running() and btn.cget("text") != normal_text:
            btn.configure(text=normal_text, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                          text_color=T.TEXT, border_width=1, border_color=T.BORDER, state="normal")

    def stop_pending(self):
        """急停/关窗钩子：停掉本卡正在跑的组队/解散（App.stop_all_tasks 对每个页面调）。"""
        for attr in ("runner_team", "runner_disband"):
            runner = getattr(self, attr, None)
            if runner and runner.is_running():
                runner.stop()

    def sync_run(self):
        """按钮状态镜像：读「日常」页 runner 的状态，套到本卡按钮上（每 tick 调）。

        ⚠ 必须**只在签名变化时**才 configure：customtkinter 的 CTkButton.configure 只要收到
        fg_color/hover_color 就无条件整只重绘（ctk_button.py 里 fg_color/hover_color 分支直接
        置 require_redraw=True），每 tick 无脑 configure 会让可见页的那一行以 ~6.7Hz 频闪。
        故这里先算出目标 (文字, 风格) 签名，与上次相同就直接返回（对齐 sniper._update_wait_button /
        App._refresh_run_status 的同款去抖写法）。"""
        page = self.app.pages.get("daily")
        if page is None or not hasattr(page, "btn_run"):
            return
        try:
            txt = page.btn_run.cget("text")
        except Exception:
            return
        if txt == getattr(page, "RUN_LABEL", self.RUN_LABEL):
            sig = (self.RUN_LABEL, "accent")
        elif txt.startswith("等待到"):
            sig = (txt, "warn")
        else:
            sig = (txt, "danger")
        if sig == self._run_sig:
            return
        self._run_sig = sig
        if sig[1] == "accent":
            self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, state="normal")
        elif sig[1] == "warn":
            self.btn_run.configure(text=txt, fg_color=T.WARN, hover_color=T.WARN_HOVER, state="normal")
        else:
            self.btn_run.configure(text=txt, fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    # ------------------------------------------------------------------
    # 宿主页驱动
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        self._render_flow()
        self.sync_run()

    def pump(self):
        self.sync_run()
        # 一键组队/解散的日志与按钮复位（runner 挂在本卡，自己轮询；与日常链 runner 互不影响）
        self._pump_utility(self.runner_team, self.btn_team, "👥  一键组队", "组队")
        self._pump_utility(self.runner_disband, self.btn_disband, "✖  一键解散", "解散")
