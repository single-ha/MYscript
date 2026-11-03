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
from ..tasks import get_task
from ..tasks.daily import (CHAINABLE, TEAM_STEPS, TEAM_STEP_TITLES,
                           ORGANIZE_STEP, FLOW_TIMING_STEP, FLOW_SHUTDOWN_STEP,
                           get_selected_dungeon_titles, normalize_flow_steps)
from .common import Tooltip

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

        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=3, sticky="e")
        ctk.CTkButton(btns, text="调整窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._adjust_windows).pack(side="left")
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left", padx=(8, 0))
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

        self.lbl_chain = ctk.CTkLabel(box, text="", font=self.fonts["body"], text_color=T.ACCENT,
                                      anchor="w", justify="left")
        self.lbl_chain.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        T.bind_wraplength(self.lbl_chain)
        Tooltip(self.lbl_chain, "点「开始日常」会依次跑的任务，按流程控制弹窗里设置的实际执行先后拼接"
                                "（含定时/关机/组队/解散/整理背包等流程动作）。调整顺序或这些流程设置，"
                                "点右上角「修改」。", self.fonts)

        self.lbl_ready = ctk.CTkLabel(box, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      anchor="w", justify="left")
        self.lbl_ready.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        T.bind_wraplength(self.lbl_ready)

    def _render_flow(self):
        """重绘执行链 + 就绪摘要（就地改文本，不重建控件）。

        执行链片段由 chain_parts 统一生成（镜像引擎执行序）：定时固定链首、关机固定链尾，
        组队/解散/每个「自动整理背包」按各自在弹窗里被拖到的位置显示（整条链可重复出现）。
        「共N项」= 真实执行步骤总数（len(parts)，含定时/整理/关机），与弹窗行前最大序号同口径。"""
        names = chain_task_names(self.app.cfg)
        parts = chain_parts(self.app.cfg)
        total = len(parts)
        if parts:
            chain_text = " -> ".join(parts) + f" (共{total}项)"
            self.lbl_chain.configure(text=chain_text, text_color=T.ACCENT)
        else:
            self.lbl_chain.configure(text="（无，开始日常只会空转）", text_color=T.TEXT_DIM)
        not_ready = [task_title(n, self.app.cfg) for n in names if not self._ready_bool(n)]
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
