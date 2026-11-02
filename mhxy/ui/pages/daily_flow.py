# -*- coding: utf-8 -*-
"""「日常流程控制」二级弹窗：从起始页「🐉 日常任务」卡的「修改」按钮打开（不再是侧栏页面）。

与「日常」页共享同一份配置（tasks.daily.steps 等）。本弹窗职责：
  · 把刷副本拆成 5 个独立子任务行（60侠士/70侠士/60普通1/60普通2/70普通），
    各自独立启停、拖拽排序，彻底替代旧页里合并的「刷副本」一行。
  · 流程控制（定时延后/自动整理/跑完关机）也作为 steps 里的可拖拽行：可改/＋/✕/排序，
    不占执行序号，右侧开关决定是否生效；「定时」行内带一个 HH:MM 时刻输入框
    （存 timing 步骤的 time，关开关也记住时刻）。保存时三项同步回引擎仍在读的旧字段
    （loop.schedule / loop.shutdown_after / organize_bag.auto_organize），引擎零改动，
    生效时机固定（定时=链开头、整理=任务之间、关机=链结尾），与拖拽位置无关。
    「组队」「解散队伍」同样是可拖到任意位置、到点真正执行的步骤。
  · 整体时间上限、自动拓印保留在顶部固定设置（不参与拖拽）。
配置与「日常」页读同一份 tasks.daily.steps / loop，两边数据互通。
本弹窗不放「开始日常」与「调整窗口」按钮（启动在起始页日常卡 / 后台由「日常」页承载）。

弹窗约定（与 account_gallery.AccountGallery 一致）：CTkToplevel + open() 去重 +
transient/grab_set 模态；关闭即销毁，再次打开重新读盘构建。
"""

import tkinter as tk

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...tasks import get_task
from ...tasks.daily import (
    CHAINABLE, TEAM_STEPS, TEAM_STEP_TITLES,
    ORGANIZE_STEP, ORGANIZE_STEP_TITLE, REPEATABLE_STEPS, FLOW_ORDERED_KEY,
)
from ...core.config import SINGLE_TASK_ORDER
from ..common import Card, Tooltip, bind_wraplength, required_regions, required_templates
from .flow_items import TaskFlowItem, FlowControlItem

_SINGLE_ORDER = list(SINGLE_TASK_ORDER)

# 5个副本子任务对应的任务类名（用于显示标题）
_SUB_TASK_TITLES = {
    "dt_60_xiashi": "60级侠士副本",
    "dt_70_xiashi": "70级侠士副本",
    "dt_60_common1": "60级普通1",
    "dt_60_common2": "60级普通2",
    "dt_70_common": "70级普通",
}

# 流程控制伪步骤：与普通任务一样作为 steps 里的可拖拽行（可改/＋/✕/排序）。
# · 定时 / 跑完关机：整条链各只有一个（单例，右侧开关决定是否生效，生效时机引擎固定——
#   定时=链开头、关机=链结尾），不占执行序号；
# · 自动整理背包（ORGANIZE_STEP）：**可在流程里放多个**（见 tasks.daily.REPEATABLE_STEPS），
#   引擎走到每个该步就在各号上整理一次，位置即生效位置（不占执行序号）。
# 保存时定时/关机由本弹窗同步回引擎仍在读的旧字段（loop.schedule / loop.shutdown_after）；
# 整理是否启用=链中是否存在启用的 organize 步，同步 organize_bag.auto_organize（仅供旧交接点
# 自动整理与其它页面开关回显；新结构链下引擎只按 organize 步的位置执行）。
FLOW_TIMING = "timing"
FLOW_ORGANIZE = ORGANIZE_STEP          # "organize"
FLOW_SHUTDOWN = "shutdown"
# 单例流程控制（整条链至多一个）；organize 不在此列（可重复）。
FLOW_SINGLE = [FLOW_TIMING, FLOW_SHUTDOWN]
FLOW_STEPS = [FLOW_TIMING, FLOW_ORGANIZE, FLOW_SHUTDOWN]
FLOW_TITLES = {
    FLOW_TIMING: "定时延后执行",
    FLOW_ORGANIZE: ORGANIZE_STEP_TITLE,
    FLOW_SHUTDOWN: "跑完关机",
}


class DailyFlowPage(ctk.CTkToplevel):
    """日常流程控制二级弹窗（起始页日常卡「修改」打开）。类名保留 DailyFlowPage 以减少改动面。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "日常流程控制"

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self._steps = []
        self._items = {}
        self._drag = None
        self._drag_line = None   # 拖拽时的目标位置彩色细线预览
        self._flat = []
        self._removed = []       # 用户明确移出流程的任务（存 tasks.daily.removed_tasks）

        # 顶部固定设置变量
        self.var_limit = ctk.StringVar(value="0")
        # 定时时刻已挪到「定时延后执行」行内输入框（存 timing 步骤的 time，保存时
        # 同步到引擎读的 tasks.daily.loop.schedule），本弹窗不再有独立的定时变量/控件。
        self.switch_auto_tuoying = None

        self.title("日常流程控制")
        self.geometry("760x720")
        self.minsize(680, 560)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(120, self._popup)

    @classmethod
    def open(cls, app, on_done=None):
        """打开（去重：已开则前置，避免两个流程窗同时写 config 打架）。"""
        existing = getattr(app, "_flow_dialog", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return existing
            except Exception:
                pass
        dlg = cls(app, on_done=on_done)
        app._flow_dialog = dlg
        return dlg

    def _popup(self):
        try:
            self.lift()
            self.focus_force()
            self.grab_set()      # 模态：打开期间锁住主窗
        except Exception:
            pass

    def _close(self, *_a):
        try:
            self.grab_release()
        except Exception:
            pass
        try:
            if getattr(self.app, "_flow_dialog", None) is self:
                self.app._flow_dialog = None
        except Exception:
            pass
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()

    # ------------------------------------------------------------------ 头部
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(14, 12))
        bar.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(bar, text="🔧 日常流程控制", font=self.fonts["title"],
                             text_color=T.TEXT)
        title.grid(row=0, column=0, sticky="w")
        Tooltip(title, "列表里的每一行都是真正的执行步骤，整行拖拽即可排序，到点按顺序跑；\n"
                       "「组队」「解散队伍」也是可拖到任意位置的步骤，各号汇合后集体执行。\n"
                       "行前序号就是真实执行先后：定时固定为第 1 步、跑完关机固定为最后一步\n"
                       "（与拖到哪无关），自动整理按你拖到的位置编号；停用行不编号。",
                self.fonts)
        ctk.CTkButton(bar, text="完成", font=self.fonts["btn"], width=96, height=34,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._close).grid(row=0, column=1, sticky="e")

    # ------------------------------------------------------------------ 控制区（不放“开始日常”“调整窗口”）
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 12))
        card.grid_columnconfigure(0, weight=1)

        # 单行：整体时间上限 | 自动拓印（弹窗内改完即存，不再放“刷新配置”——重开弹窗即从盘读）
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=16, pady=12)

        ctk.CTkLabel(row, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.lim_ent = ctk.CTkEntry(row, textvariable=self.var_limit, width=70,
                                    font=self.fonts["body"], fg_color=T.SURFACE_2,
                                    border_color=T.BORDER)
        self.lim_ent.pack(side="left", padx=(8, 24))
        # 弹窗里没有“开始前统一落盘”的入口，时间上限失焦/回车即存盘（_save_steps 会带上它）。
        self.lim_ent.bind("<FocusOut>", lambda e: self._save_steps())
        self.lim_ent.bind("<Return>", lambda e: self._save_steps())

        ctk.CTkLabel(row, text="自动拓印（关闭后遇拓印弹窗转手动）",
                     font=self.fonts["body"], text_color=T.TEXT).pack(side="left")
        self.switch_auto_tuoying = ctk.CTkSwitch(
            row, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN,
            button_color=T.ON_ACCENT, command=self._toggle_auto_tuoying)
        self.switch_auto_tuoying.pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=14, pady=(0, 14))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew")
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        # 执行顺序摘要：占标题行全部宽度并在该宽度内折行（列 weight=1 + sticky=ew）。
        self.lbl_exec_order = ctk.CTkLabel(head, text="", font=self.fonts["small"],
                                           anchor="w", justify="left", text_color=T.ACCENT)
        self.lbl_exec_order.grid(row=0, column=0, sticky="ew")
        bind_wraplength(self.lbl_exec_order)

        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

    # ------------------------------------------------------------------ 刷新/渲染
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        self._removed = [t for t in (tc.get("removed_tasks") or [])]
        # 旧开关字段（流程伪步骤还没进 steps 的旧 config 用它做一次性迁移）
        # timing 直接给已存的时间串（空串=关）；迁移成行时把时刻一并带上。
        legacy = {
            FLOW_TIMING: ((tc.get("loop") or {}).get("schedule", "") or ""),
            FLOW_SHUTDOWN: bool((tc.get("loop") or {}).get("shutdown_after", False)),
            FLOW_ORGANIZE: bool(cfg_mod.task_config(self.app.cfg, "organize_bag")
                                .get("auto_organize", False)),
        }
        self._steps = self._normalize(tc.get("steps", []), self._removed, legacy)
        loopc = tc.get("loop", {}) or {}
        # 时间上限：整数（含 0=不限）显示成整数，非整数保留一位，避免配置里 0.0 显示成“0.0”
        try:
            _lv = float(loopc.get("time_limit_min", 0))
            lim_txt = str(int(_lv)) if abs(_lv - round(_lv)) < 1e-9 else f"{_lv:.1f}"
        except (TypeError, ValueError):
            lim_txt = "0"
        self.var_limit.set(lim_txt)
        self._render_steps()
        self._sync_controls()

    def _sync_controls(self):
        if self.switch_auto_tuoying is not None:
            tuo_tc = cfg_mod.task_config(self.app.cfg, "tuoying")
            if tuo_tc.get("enabled", True):
                self.switch_auto_tuoying.select()
            else:
                self.switch_auto_tuoying.deselect()

    @staticmethod
    def _norm_hhmm(raw, default="10:00"):
        """把输入规整成合法 HH:MM；非法/空返回 default。与行控件输入框同一口径。"""
        if not raw:
            return default
        raw = str(raw).strip().replace("：", ":")
        if ":" not in raw:
            return default
        try:
            hh, mm = raw.split(":")[:2]
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError):
            return default
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return default
        return f"{hh:02d}:{mm:02d}"

    @staticmethod
    def _normalize(stored, removed=(), legacy=None):
        """把存储的 steps 规整成统一的可编辑行序列：
        · task="" 空白占位原样保留；
        · CHAINABLE 普通任务（排除已废弃的 dungeon）去重、尊重存储的 enabled
          （行开关=启用/停用，停用的行保留在流程里、引擎跳过），removed 里明确
          移除的不补、其余缺省任务补到末尾（默认启用）；
        · TEAM_STEPS（组队/解散）与「整理背包」(organize) 是【可重复】伪步骤：按存储位置
          原样保留任意多个、不去重、不参与缺行补齐；
        · 定时/关机是【单例】伪步骤：整条链各至多一个、按存储位置保留、不去重补齐；
          「定时」额外保留/补齐 time=HH:MM（缺失时取旧 loop.schedule，再缺省 10:00）；
        · 旧 config 的 steps 里还没有流程伪步骤时，按旧开关字段 legacy 迁移一次
          （开着的默认开启，timing 带上旧 schedule 时刻）；organize 仅在链中一个都没有时迁移，
          用户已移出（进 removed）不补。"""
        removed = set(removed or ())
        legacy = legacy or {}
        legacy_sched = legacy.get(FLOW_TIMING, "") if isinstance(
            legacy.get(FLOW_TIMING, ""), str) else ""
        out, seen = [], set()
        organize_count = 0
        for s in stored or []:
            if not isinstance(s, dict):
                continue
            task = s.get("task", "")
            if task == "":
                out.append({"task": "", "enabled": bool(s.get("enabled", False))})
                continue
            if task == FLOW_ORGANIZE:
                # 整理背包：允许在流程里出现多次，全部按存储位置/启用态保留。
                out.append({"task": task, "enabled": bool(s.get("enabled", False))})
                organize_count += 1
                continue
            if task in TEAM_STEPS:
                # 组队/解散：可重复，按存储位置保留任意多个（不参与缺行补齐）。尊重行开关的存储态
                # （缺省 True 兼容旧链）；停用的行要能在重开弹窗后仍是关的，否则关掉组队不生效。
                out.append({"task": task, "enabled": bool(s.get("enabled", True))})
                continue
            if task in FLOW_SINGLE and task not in seen:
                entry = {"task": task, "enabled": bool(s.get("enabled", False))}
                if task == FLOW_TIMING:
                    entry["time"] = DailyFlowPage._norm_hhmm(
                        s.get("time") or legacy_sched)
                out.append(entry)
                seen.add(task)
                continue
            if task in CHAINABLE and task != "dungeon" and task not in seen:
                # 任务行开关=启用/停用：尊重存储的 enabled（缺省 True）。停用的行保留在
                # 流程里（引擎按 enabled=False 跳过、不补回），可用开关再开；✕ 才彻底移除。
                out.append({"task": task, "enabled": bool(s.get("enabled", True))})
                seen.add(task)
        for task in CHAINABLE:
            if task not in seen and task != "dungeon" and task not in removed:
                out.append({"task": task, "enabled": True})
                seen.add(task)
        # 旧开关 → 流程伪步骤的一次性迁移（只在存储里一个对应伪步骤都没有时进行）
        def _has(kind):
            return any(x.get("task") == kind for x in (stored or [])
                       if isinstance(x, dict))
        for task in FLOW_STEPS:
            if task in removed or not legacy.get(task):
                continue
            if task == FLOW_ORGANIZE:
                if organize_count > 0:
                    continue                 # 已有（哪怕多个）整理步，不再按旧开关迁移
            elif task in seen or _has(task):
                continue                     # 定时/关机单例，已存在不迁
            entry = {"task": task, "enabled": True}
            if task == FLOW_TIMING:
                entry["time"] = DailyFlowPage._norm_hhmm(legacy.get(task))
            out.append(entry)
            seen.add(task)
        return out

    def _task_display_name(self, name):
        if not name:
            return "空白任务"
        if name in FLOW_TITLES:
            return FLOW_TITLES[name]
        if name in TEAM_STEP_TITLES:
            return TEAM_STEP_TITLES[name]
        if name in _SUB_TASK_TITLES:
            return _SUB_TASK_TITLES[name]
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        if name in TEAM_STEPS or name in FLOW_STEPS:
            # 组队/解散由共享 teaming 标定在运行时 preflight 校验；流程项无需标定。
            return True
        sub = cfg_mod.task_config(self.app.cfg, name)
        return self._calib_done(name, sub)

    def _calib_done(self, name, sub):
        spec = getattr(get_task(name), "CALIBRATION", None) or {}
        need_r = required_regions(spec) + list(cfg_mod.TASK_SHARED_REQ.get(name, ()))
        need_t = required_templates(spec) + list(cfg_mod.TASK_SHARED_TPL_REQ.get(name, ()))
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        return all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)

    def _render_steps(self):
        sig = self._steps_signature()
        if getattr(self, "_steps_sig", None) == sig:
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            if w is not getattr(self, "_drag_line", None):
                w.destroy()
        self._items = {}
        self._flat = []
        for i, step in enumerate(self._steps):
            name = step["task"]
            # 行 key 用「steps 下标」而非 task 名：组队/解散/整理允许重复出现，
            # 任务名相同的多行也必须各自唯一（否则 _items 字典会互相覆盖、拖删改串到别的行）。
            uid = name if name else f"_blank{i}"
            uid = f"{uid}#{i}"
            if name in FLOW_STEPS:
                self._items[uid] = FlowControlItem(self.list_frame, self, uid, step)
            else:
                self._items[uid] = TaskFlowItem(self.list_frame, self, uid, step)
            self._flat.append(("task", uid))
        self._grid_all()

    def _steps_signature(self):
        return tuple((s["task"], s["enabled"]) for s in self._steps)

    # ------------------------------------------------------------------ 行控件回调
    def on_flow_toggle(self, key, on):
        """FlowControlItem 开关：step.enabled 已在行控件里改好，这里整表存盘即可
        （_save_steps 会同步 loop.schedule / shutdown_after / auto_organize 旧字段）。
        不整表重建，就地保留开关/胶囊状态（避免闪烁）；重排序号（定时/关机开关会改变首尾号）
        并即时带上/去掉执行链摘要标签。"""
        self._save_steps()
        self._renumber()

    def on_task_toggle(self, key, on):
        """TaskFlowItem 开关：只做启用/停用（行不移除）。step.enabled 已在行控件改好，
        这里存盘并就地重排序号/执行链摘要（停用项不再编号、不计入摘要），不整表重建。"""
        self._save_steps()
        self._renumber()

    def _open_task_config_for(self, name):
        """供 TaskFlowItem 的「⚙ 配置」按钮生成回调。"""
        return lambda: self._open_task_config(name)

    def _grid_all(self):
        for pos, entry in enumerate(self._flat):
            item = self._items[entry[1]]
            item.grid(row=pos, column=0, sticky="ew", pady=3, padx=4)
        self._renumber()

    def _renumber(self):
        # 徽标序号严格按**引擎真实执行顺序**编号（user 2026-11-01：眼睛看到的序号要和干活顺序
        # 一致，定时/关机也要号），而不是按行在列表里的视觉位置：
        #   ① 定时：启用则恒为第 1 步（引擎在整条链启动前等时刻，固定链首，与被拖到哪无关）；
        #   ② 中段：按存储绝对序的启用步——普通任务/组队/解散 + 每个启用的「自动整理背包」
        #      （整理可有多个，各按其位置），依次连续编号；
        #   ③ 关机：启用则恒为最后一步（所有号跑完整条链后才关机，固定链尾）。
        # 停用行/空白占位显「·」；定时或关机停用时其号消失、中段序号仍与执行序一致。
        runs = []           # 真正会执行的行控件，按执行先后
        timing = shutdown = None
        for entry in self._flat:
            item = self._items[entry[1]]
            if item.kind == "task":
                if item.runs_now():
                    runs.append(item)
            elif item.kind == "flow" and bool(getattr(item, "enabled", False)):
                if item.name == FLOW_TIMING:
                    timing = item
                elif item.name == FLOW_SHUTDOWN:
                    shutdown = item
                else:
                    runs.append(item)     # 整理：按其存储位置就地编号
        ordered = ([timing] if timing else []) + runs + ([shutdown] if shutdown else [])
        for entry in self._flat:
            self._items[entry[1]].clear_number()
        for i, item in enumerate(ordered, start=1):
            item.set_number(i)
        self._refresh_exec_order()

    def _refresh_exec_order(self):
        # 摘要严格镜像引擎真实执行顺序（与行前徽标序号、「共N项」完全一致）：
        # 定时=第 1 步、关机=最后一步（引擎固定首尾，与拖拽位置无关），中段的任务/组队/解散/
        # 每个启用的自动整理背包按各自存储位置排列；停用行与空白占位不计。
        flow_label = {FLOW_TIMING: "⏰定时",
                      FLOW_ORGANIZE: "🎒整理背包",
                      FLOW_SHUTDOWN: "🔌关机"}
        middle, timing, shutdown = [], None, None
        for entry in self._flat:
            item = self._items[entry[1]]
            if item.kind == "task":
                if item.runs_now():                 # 启用的任务/组队/解散
                    middle.append(item.label_text)
            elif item.kind == "flow":
                name = item.name
                if not bool(getattr(item, "enabled", False)):
                    continue
                if name == FLOW_TIMING:
                    timing = flow_label[name]
                elif name == FLOW_SHUTDOWN:
                    shutdown = flow_label[name]
                else:
                    middle.append(flow_label.get(name, item.label_text))
        parts = ([timing] if timing else []) + middle + ([shutdown] if shutdown else [])
        # 「共N项」= 真正会执行的步骤总数（含定时/每个整理/关机），与行前徽标的最大号一致。
        n_total = len(parts)
        if parts:
            self.lbl_exec_order.configure(text=" -> ".join(parts) +
                                          f" (共{n_total}项)", text_color=T.ACCENT)
        else:
            self.lbl_exec_order.configure(text="（无，开始日常只会空转）",
                                          text_color=T.TEXT_DIM)

    # ------------------------------------------------------------------ 启用切换 / 拖拽 / 保存
    def _flow_on(self, name):
        return any(s.get("task") == name and s.get("enabled") for s in self._steps)

    def _save_steps(self):
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["steps"] = [self._step_to_save(s) for s in self._steps]
        tc["removed_tasks"] = list(dict.fromkeys(self._removed))
        # 这条链是流程控制弹窗排定并保存的：要求引擎严格按上面 steps 的存储顺序执行
        # （哪怕全是普通任务、没有组队/解散/整理伪步骤，也不再按多人/单人分区重排）。
        tc[FLOW_ORDERED_KEY] = True
        loopc = tc.setdefault("loop", {})
        try:
            loopc["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        # —— 单例流程伪步骤 → 引擎仍在读的旧字段（定时/关机）——
        # 跑完关机
        loopc["shutdown_after"] = self._flow_on(FLOW_SHUTDOWN)
        # 定时延后：开启时用定时行自己的时刻 time（兜底默认 10:00），关闭=空串
        tstep = next((s for s in self._steps
                      if s.get("task") == FLOW_TIMING and s.get("enabled")), None)
        if tstep is not None:
            tstep["time"] = self._norm_hhmm(tstep.get("time"))
            loopc["schedule"] = tstep["time"]
        else:
            loopc["schedule"] = ""
        # 自动整理背包：链中任一 organize 步开启即置位（供旧交接点逻辑/其它页面开关回显；
        # 新结构链下引擎按 organize 步的位置执行，可放多个）。
        obtc = cfg_mod.task_config(cfg, "organize_bag")
        obtc["auto_organize"] = self._flow_on(FLOW_ORGANIZE)
        cfg_mod.set_task_config(cfg, "organize_bag", obtc)
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    @staticmethod
    def _step_to_save(s):
        # 只有「定时」步骤额外存 time=HH:MM；其余步骤只存 task/enabled，保持存盘干净
        entry = {"task": s["task"], "enabled": bool(s["enabled"])}
        if s.get("task") == FLOW_TIMING:
            entry["time"] = DailyFlowPage._norm_hhmm(s.get("time"))
        return entry

    def _drag_start(self, event, item):
        # 点击按钮/开关/下拉框等交互控件时，不启动拖拽（保留它们原有行为）
        from customtkinter import CTkButton, CTkSwitch, CTkComboBox, CTkEntry
        if isinstance(event.widget, (CTkButton, CTkSwitch, CTkComboBox, CTkEntry)):
            return
        self._drag = {"key": item.key, "item": item, "ghost": None}
        item.set_drag_highlight(True)
        item.tkraise()
        # 创建 ghost 浮窗：跟随鼠标显示被拖拽行的标题和背景色
        try:
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            ghost.attributes("-topmost", True)
            try:
                ghost.attributes("-alpha", 0.85)
            except Exception:
                pass
            inner = tk.Label(
                ghost, text=str(item.label_text),
                bg=self._hex(T.BTN), fg=self._hex(T.TEXT),
                font=self.fonts["body_b"], padx=14, pady=6)
            inner.pack()
            ghost.geometry(f"+{event.x_root + 14}+{event.y_root + 10}")
            self._drag["ghost"] = ghost
        except Exception:
            self._drag["ghost"] = None

    @staticmethod
    def _hex(color):
        """theme 颜色可能是 tuple（CTk 支持），tk 控件只认字符串，取其主色。"""
        if isinstance(color, (tuple, list)):
            color = color[0]
        return color if isinstance(color, str) else "#2b2b2b"

    def _drag_motion(self, event):
        if not self._drag:
            return
        # ghost 跟随鼠标（屏幕坐标）
        ghost = self._drag.get("ghost")
        if ghost is not None:
            try:
                ghost.geometry(f"+{event.x_root + 14}+{event.y_root + 10}")
            except Exception:
                pass
        # 槽位式排序：把拖起的行从列表里抽掉，余下的行按「指针在各行中线
        # 以上/以下」直接数出目标插入槽位——一次 motion 就能跨任意多行。
        # （旧实现找「指针所在的那一行」再逐格交换，快拖永远差一行。）
        dragged = self._drag["key"]
        others = [e for e in self._flat if e[1] != dragged]
        slot = 0
        for e in others:
            f = self._items[e[1]]
            if f.winfo_ismapped() and event.y_root > f.winfo_rooty() + f.winfo_height() / 2:
                slot += 1
        if slot > len(others):
            slot = len(others)
        dragged_e = next(e for e in self._flat if e[1] == dragged)
        new_flat = others[:slot] + [dragged_e] + others[slot:]
        if new_flat != self._flat:
            self._flat = new_flat
            self._grid_all()   # 拖拽中只动显示顺序，不存盘（防闪烁）
        self._place_drag_line(event)

    def _place_drag_line(self, event):
        """在拖起行当前落位的上边缘显示彩色细线预览。"""
        try:
            self.list_frame.update_idletasks()
        except Exception:
            return
        try:
            f = self._items[self._drag["key"]]
        except (KeyError, TypeError):
            self._hide_drag_line()
            return
        if not f.winfo_ismapped():
            self._hide_drag_line()
            return
        y = max(0, f.winfo_rooty() - self.list_frame.winfo_rooty() - 3)
        line = getattr(self, "_drag_line", None)
        if line is not None:
            try:
                if not line.winfo_exists():
                    self._drag_line = None
            except Exception:
                self._drag_line = None
        if getattr(self, "_drag_line", None) is None:
            try:
                self._drag_line = tk.Frame(
                    self.list_frame, bg=self._hex(T.ACCENT), height=3,
                    bd=0, highlightthickness=0)
            except Exception:
                self._drag_line = None
                return
        try:
            self._drag_line.place(x=10, y=y, relwidth=0.97)
            self._drag_line.lift()
        except Exception:
            pass

    def _hide_drag_line(self):
        if getattr(self, "_drag_line", None) is not None:
            try:
                self._drag_line.place_forget()
            except Exception:
                pass

    def _drag_end(self, event):
        if not self._drag:
            return
        # 先销毁 ghost 浮窗和预览细线（失败不能影响落盘流程）
        try:
            if self._drag.get("ghost") is not None:
                self._drag["ghost"].destroy()
        except Exception:
            pass
        self._drag["ghost"] = None
        self._hide_drag_line()
        try:
            self._items[self._drag["key"]].set_drag_highlight(False)
        except Exception:
            pass
        # 把拖拽过程累积的 _flat 顺序同步回 _steps 数据，并一次性存盘
        self._sync_steps_from_flat()
        self._save_steps()
        self._drag = None
        self._grid_all()

    def _sync_steps_from_flat(self):
        """把 _flat 里行的顺序写回 _steps（拖拽结束时调用）。
        行 key 形如  task#idx / _blank#idx，# 前是 task 名（空名占位为 _blank），# 后是该行
        在拖起前 _steps 中的下标；直接用行控件持有的 step 对象按新顺序重排，天然支持重复行。"""
        order = [key for kind, key in self._flat if kind == "task"]
        new_steps = []
        for k in order:
            it = self._items.get(k)
            if it is not None and getattr(it, "step", None) is not None:
                new_steps.append(it.step)
        self._steps = new_steps

    # ------------------------------------------------------------------ 行内任务按钮（改 / 加 / 移除）
    def _change_task(self, key):
        item = self._items.get(key)
        if item is None or getattr(item, "step", None) is None:
            return
        step, old = item.step, item.name
        present = {s["task"] for s in self._steps if s is not step}
        # 普通任务、定时、关机是单例（已存在的不再列出）；组队/解散/整理允许在流程里放多个。
        repeatable = set(REPEATABLE_STEPS)
        choices = [t for t in CHAINABLE if t != "dungeon" and t not in present]
        choices += [t for t in TEAM_STEPS if t in repeatable or t not in present]
        choices += [t for t in FLOW_STEPS
                    if t == FLOW_ORGANIZE or (t in FLOW_SINGLE and t not in present)]
        if not choices:
            self._log_line("没有可更换的任务/动作了（普通任务、定时、关机整条链各只能有一个）。", "warn")
            return
        disp = [self._task_display_name(t) for t in choices]
        win = ctk.CTkToplevel(self)
        win.title("更改任务")
        win.transient(self)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        win.geometry(f"+{self.winfo_rootx() + 220}+{self.winfo_rooty() + 160}")
        win.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(win, text=("为空白占位选择一个任务：" if not old
                                else f"替换「{self._task_display_name(old)}」为："),
                     font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, padx=18, pady=(16, 6), sticky="w")
        var = ctk.StringVar(value=disp[0])
        cmb = ctk.CTkComboBox(win, variable=var, values=disp, width=240,
                              state="readonly", font=self.fonts["body"])
        cmb.grid(row=1, column=0, padx=18, pady=4, sticky="ew")

        def _confirm(_event=None):
            val = var.get()
            if val in disp:
                win.destroy()
                self._apply_change_task(step, old, choices[disp.index(val)])

        cmb.bind("<Return>", _confirm)
        btns = ctk.CTkFrame(win, fg_color="transparent")
        btns.grid(row=2, column=0, padx=18, pady=(8, 16), sticky="e")
        ctk.CTkButton(btns, text="取消", font=self.fonts["body"], width=76, height=32,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, command=win.destroy).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="确定", font=self.fonts["body"], width=76, height=32,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=_confirm).pack(side="left")

        def _grab():
            try:
                win.grab_set()
                win.focus_set()
            except Exception:
                pass
        win.after(60, _grab)

    def _apply_change_task(self, step, old, new):
        # 换成流程项时默认开着（和插进来一致）；换成任务/组队解散则强制启用
        step["task"] = new
        step["enabled"] = True
        if new == FLOW_TIMING:
            step["time"] = self._norm_hhmm(step.get("time"))
        elif "time" in step:
            step.pop("time", None)
        # 旧行被换掉后：只有「整条链里不再存在该名字」时才标记 removed——普通任务借此阻止
        # 缺行补齐，定时/关机借此阻止下次加载被当旧 config 迁移/补回。可重复的组队/解散/整理
        # 若还留有同名行，则一个都不能标 removed（否则会误伤其余同名行）。
        remaining = {s["task"] for s in self._steps if s is not step and s.get("task")}
        if old and old not in remaining:
            if old not in self._removed:
                self._removed.append(old)
        if new in self._removed:
            self._removed.remove(new)
        self._save_steps()
        self._steps_sig = None
        self._render_steps()
        self._log_line(f"已换成为「{self._task_display_name(new)}」。", "info")

    def _add_below(self, key):
        item = self._items.get(key)
        if item is None or getattr(item, "step", None) is None:
            return
        step = item.step
        try:
            idx = self._steps.index(step)
        except ValueError:
            idx = len(self._steps) - 1
        self._steps.insert(idx + 1, {"task": "", "enabled": False})
        self._save_steps()
        self._steps_sig = None
        self._render_steps()
        self._log_line("已在下方插入空白占位：点该行标题选定任务。", "info")

    def _remove_task(self, key):
        item = self._items.get(key)
        if item is None or getattr(item, "step", None) is None:
            return
        step = item.step
        name = step.get("task")
        # 普通任务进 removed_tasks 阻止缺行补齐；定时/关机进 removed_tasks 阻止下次加载被当
        # 旧 config 迁移/补回。可重复的组队/解散/整理：仅当删掉后整条链里再无同名行才标记，
        # 否则会误伤其余同名行。空白占位不记。
        remaining = {s.get("task") for s in self._steps
                     if s is not step and s.get("task")}
        if name and name not in remaining and name not in self._removed:
            self._removed.append(name)
        try:
            self._steps.remove(step)
        except ValueError:
            pass
        self._save_steps()
        self._steps_sig = None
        self._render_steps()
        self._log_line(f"已把「{self._task_display_name(name)}」移出流程。", "info")

    # ------------------------------------------------------------------ 控制区回调
    def _toggle_auto_tuoying(self):
        on = bool(self.switch_auto_tuoying.get())
        cfg = cfg_mod.load_config()
        tuo_tc = cfg["tasks"].setdefault("tuoying", {})
        tuo_tc["enabled"] = on
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._log_line(("已开启「自动拓印」：遇拓印弹窗会自动描摹并点「上传」。" if on
                        else "已关闭「自动拓印」：遇拓印弹窗不再自动描摹。"),
                       "warn" if not on else "info")

    def _log_line(self, msg, level="info"):
        if hasattr(self.app, "log_line"):
            self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
        else:
            print(f"[{level}] {msg}")

    def _open_task_config(self, name):
        if name in TEAM_STEPS:
            name = "teaming"   # 组队/解散的标定都在「组队（共享）」卡
        # 模态弹窗里点「⚙ 配置」：先关弹窗释放 grab，再跳「任务配置」页定位卡片。
        self._close()
        try:
            self.app._show("config")
            cp = self.app.pages.get("config")
            if cp is not None and hasattr(cp, "reveal_card"):
                cp.reveal_card(name)
        except Exception:
            pass
