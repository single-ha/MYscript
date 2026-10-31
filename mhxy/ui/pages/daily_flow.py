# -*- coding: utf-8 -*-
"""「日常流程控制」页面：侧边栏独立入口，与「日常」页共享同一份配置（tasks.daily.steps 等）。

与旧「日常」页并存，不改动 daily.py。本页职责：
  · 把刷副本拆成 5 个独立子任务行（60侠士/70侠士/60普通1/60普通2/710普通），
    各自独立启停、拖拽排序，彻底替代旧页里合并的「刷副本」一行。
  · 把原来顶部控制区的开关（定时/关机/解散/整理/已组队）改造成可拖拽、
    可启停的「流程控制任务行」，排在主任务列表之中；定时固定在链开头、
    关机固定在链结尾，其余按各自逻辑位置生效。
  · 整体时间上限、自动拓印保留在顶部固定设置（不参与拖拽）。
配置与「日常」页读同一份 tasks.daily.steps / group_enabled / loop，两页数据互通。
本页不放「开始日常」与「调整窗口」按钮（启动由旧「日常」页承担）；
定时到点自动启动仍由 pump() 处理。
"""

import copy
from datetime import datetime, timedelta

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...tasks.base import dungeon_tasks
from ...tasks.daily import (
    CHAINABLE, GROUP_OF, GROUPS, GROUP_TITLES, MULTI_BARRIER,
    DUNGEON_SUB_TASKS, get_selected_dungeon_titles,
)
from ...core.config import SINGLE_TASK_ORDER
from ..common import Card, Tooltip, bind_wraplength, required_regions, required_templates

_SINGLE_ORDER = list(SINGLE_TASK_ORDER)

# 5个副本子任务对应的任务类名（用于显示标题）
_SUB_TASK_TITLES = {
    "dt_60_xiashi": "60级侠士副本",
    "dt_70_xiashi": "70级侠士副本",
    "dt_60_common1": "60级普通1",
    "dt_60_common2": "60级普通2",
    "dt_70_common": "710级普通",
}

FLOW_CONTROL_TASKS = [
    ("timing", "定时延后执行"),
    ("disband", "跑完多人任务后解散队伍"),
    ("organize", "自动整理背包"),
    ("skip_team", "已组队（跳过自动组队）"),
    ("shutdown", "跑完关机"),
]


class DailyFlowPage(ctk.CTkFrame):
    """日常流程控制页：独立于旧「日常」页，共享配置。"""

    TASK_NAME = "daily"
    LOG_SOURCE = "日常流程控制"
    RUN_LABEL = "▶  开始日常"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._steps = []
        self._group_on = {"multi": True, "single": True}
        self._rows = {}
        self._drag = None
        self._flat = []
        self._wait_until = None      # 定时延迟执行：晚于该时刻才真正启动

        # 顶部固定设置变量
        self.var_limit = ctk.StringVar(value="0")
        self.var_shutdown = ctk.BooleanVar(value=False)
        self.var_schedule_on = ctk.BooleanVar(value=False)
        self.var_schedule_time = ctk.StringVar(value="10:00")
        self.switch_auto_tuoying = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_control()
        self._build_add_task()
        self._build_body()
        self.refresh()
    # ------------------------------------------------------------------ 头部
    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        title = ctk.CTkLabel(bar, text="日常流程控制", font=self.fonts["title"],
                             text_color=T.TEXT)
        title.grid(row=0, column=0, sticky="w")
        Tooltip(title, "与「日常」页共享同一份配置，两页数据互通。\n"
                       "主任务列表支持拖拽排序与启停；流程控制任务（定时/关机/解散/"
                       "整理/已组队）可独立启停，定时固定在链开头、关机固定在链结尾。",
                self.fonts)

    # ------------------------------------------------------------------ 控制区（不放“开始日常”“调整窗口”）
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        # 仅保留「刷新配置」工具按钮
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 10))
        opts.grid_columnconfigure(0, weight=1)
        opts.grid_columnconfigure(1, weight=1)

        # 整体时间上限（保留，固定不参与拖拽）
        lim = ctk.CTkFrame(opts, fg_color="transparent")
        lim.grid(row=0, column=0, sticky="w", padx=(0, 26), pady=(10, 0))
        ctk.CTkLabel(lim, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.lim_ent = ctk.CTkEntry(lim, textvariable=self.var_limit, width=70,
                                    font=self.fonts["body"], fg_color=T.SURFACE_2,
                                    border_color=T.BORDER)
        self.lim_ent.pack(side="left", padx=(8, 0))

        # 自动拓印（保留，固定不参与拖拽）
        tuo = ctk.CTkFrame(opts, fg_color="transparent")
        tuo.grid(row=0, column=1, sticky="e", pady=(10, 0))
        tuo_lbl = ctk.CTkLabel(tuo, text="自动拓印（关闭后遇拓印弹窗转手动）",
                               font=self.fonts["body"], text_color=T.TEXT)
        tuo_lbl.pack(side="left")
        self.switch_auto_tuoying = ctk.CTkSwitch(
            tuo, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN,
            button_color=T.ON_ACCENT, command=self._toggle_auto_tuoying)
        self.switch_auto_tuoying.pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------ 添加任务入口
    def _build_add_task(self):
        card = Card(self)
        card.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 10))
        card.grid_columnconfigure(1, weight=1)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.grid(row=0, column=0, sticky="ew", padx=16, pady=10)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text="添加任务到流程：", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left", padx=(0, 8))

        self.var_add_task = ctk.StringVar(value="")
        self.cmb_add_task = ctk.CTkComboBox(row, variable=self.var_add_task,
                                            values=[], width=200, font=self.fonts["body"],
                                            state="readonly")
        self.cmb_add_task.pack(side="left", padx=(0, 8))
        self._refresh_add_task_combo()

        ctk.CTkButton(row, text="添加", font=self.fonts["body"], height=34, width=70,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                      hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._do_add_task).pack(side="left")

    def _refresh_add_task_combo(self):
        """下拉框列出 CHAINABLE 中尚未加入流程的任务（排除 dungeon，已被5子任务替代）。"""
        present = {s["task"] for s in self._steps}
        opts = [t for t in CHAINABLE if t not in present and t != "dungeon"]
        self.cmb_add_task.configure(values=opts)
        if opts:
            self.var_add_task.set(opts[0])
        else:
            self.var_add_task.set("")

    def _do_add_task(self):
        name = self.var_add_task.get()
        if not name or name in {s["task"] for s in self._steps}:
            return
        self._steps.append({"task": name, "enabled": True})
        self._save_steps()
        self._steps_sig = None
        self._render_steps()
        self._refresh_add_task_combo()
    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)

        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(left, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 8))
        head.grid_columnconfigure(0, weight=1)
        head.grid_columnconfigure(1, weight=0)
        ctk.CTkLabel(head, text="执行任务与流程控制", font=self.fonts["h2"],
                     text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self.lbl_exec_order = ctk.CTkLabel(head, text="", font=self.fonts["small"],
                                           anchor="w", justify="left", text_color=T.ACCENT)
        self.lbl_exec_order.grid(row=0, column=1, sticky="e", padx=(10, 0))
        bind_wraplength(self.lbl_exec_order)

        self.list_frame = ctk.CTkScrollableFrame(left, fg_color="transparent")
        self.list_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.list_frame)

    # ------------------------------------------------------------------ 刷新/渲染
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        self._group_on = {g: bool((tc.get("group_enabled") or {}).get(g, True))
                          for g in GROUPS}
        self._steps = self._normalize(tc.get("steps", []))
        loopc = tc.get("loop", {}) or {}
        self.var_limit.set(str(loopc.get("time_limit_min", 0)))
        self.var_shutdown.set(bool(loopc.get("shutdown_after", False)))
        sched = loopc.get("schedule", "") or ""
        self.var_schedule_on.set(bool(sched))
        if sched and ":" in sched:
            self.var_schedule_time.set(sched)
        self._render_steps()
        self._sync_controls()
        self._refresh_add_task_combo()

    def _sync_controls(self):
        if self.switch_auto_tuoying is not None:
            tuo_tc = cfg_mod.task_config(self.app.cfg, "tuoying")
            if tuo_tc.get("enabled", True):
                self.switch_auto_tuoying.select()
            else:
                self.switch_auto_tuoying.deselect()

    @staticmethod
    def _normalize(stored):
        """把存储的 steps 规整：只保留 CHAINABLE 里的任务，去重，排除 dungeon
       （已被5个副本子任务彻底替代），未在存储里的新任务补到末尾（默认启用）。"""
        out, seen = [], set()
        for s in stored or []:
            if isinstance(s, dict) and s.get("task") in CHAINABLE and s["task"] not in seen \
                    and s["task"] != "dungeon":
                out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
                seen.add(s["task"])
        for t in CHAINABLE:
            if t not in seen and t != "dungeon":
                out.append({"task": t, "enabled": True})
                seen.add(t)
        return out

    def _task_display_name(self, name):
        if name in _SUB_TASK_TITLES:
            return _SUB_TASK_TITLES[name]
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        sub = cfg_mod.task_config(self.app.cfg, name)
        return self._calib_done(name, sub)

    def _calib_done(self, name, sub):
        spec = getattr(get_task(name), "CALIBRATION", None) or {}
        need_r = required_regions(spec) + list(cfg_mod.TASK_SHARED_REQ.get(name, ()))
        need_t = required_templates(spec) + list(cfg_mod.TASK_SHARED_TPL_REQ.get(name, ()))
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        return all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)

    def _runs_now(self, step):
        if not step.get("enabled"):
            return False
        name = step.get("task")
        # 新页面不再有合并的「dungeon」行，只保留5个独立子任务，无需额外勾选判断
        return True

    def _render_steps(self):
        sig = self._steps_signature()
        if getattr(self, "_steps_sig", None) == sig:
            return
        self._steps_sig = sig
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._rows = {}
        self._flat = []
        for step in self._steps:
            name = step["task"]
            rec = self._build_row("task", name, step)
            self._rows[name] = rec
            self._flat.append(("row", name))
        for key, label in FLOW_CONTROL_TASKS:
            enabled = self._flow_control_enabled(key)
            rec = self._build_row("flow", key, {"task": key, "enabled": enabled, "_label": label})
            self._rows[key] = rec
            self._flat.append(("row", key))
        self._grid_all()

    def _steps_signature(self):
        return (tuple((s["task"], s["enabled"]) for s in self._steps),
                tuple((k, self._flow_control_enabled(k)) for k, _ in FLOW_CONTROL_TASKS),
                tuple(self._group_on.get(g, True) for g in GROUPS))

    def _flow_control_enabled(self, key):
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        if key == "timing":
            return bool((tc.get("loop") or {}).get("schedule", ""))
        if key == "shutdown":
            return bool((tc.get("loop") or {}).get("shutdown_after", False))
        if key == "disband":
            return bool(cfg_mod.task_config(self.app.cfg, "teaming").get("auto_disband", False))
        if key == "skip_team":
            return bool(cfg_mod.task_config(self.app.cfg, "teaming").get("skip_team", False))
        if key == "organize":
            return bool(cfg_mod.task_config(self.app.cfg, "organize_bag").get("auto_organize", False))
        return False

    def _set_flow_control(self, key, on):
        cfg = cfg_mod.load_config()
        if key == "timing":
            loopc = cfg_mod.task_config(cfg, self.TASK_NAME).setdefault("loop", {})
            loopc["schedule"] = on
        elif key == "shutdown":
            loopc = cfg_mod.task_config(cfg, self.TASK_NAME).setdefault("loop", {})
            loopc["shutdown_after"] = on
        elif key in ("disband", "skip_team"):
            teamc = cfg["tasks"].setdefault("teaming", {})
            teamc[key] = on
        elif key == "organize":
            obtc = cfg_mod.task_config(cfg, "organize_bag")
            obtc["auto_organize"] = on
            cfg_mod.set_task_config(cfg, "organize_bag", obtc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _build_row(self, kind, key, step):
        name = step.get("task", key)
        label = step.get("_label") or self._task_display_name(name)
        row = ctk.CTkFrame(self.list_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        row.grid_columnconfigure(2, weight=1)

        grip = ctk.CTkLabel(row, text="⠿", font=self.fonts["h2"], text_color=T.TEXT_DIM,
                            width=22)
        grip.grid(row=0, column=0, rowspan=2, sticky="ns", padx=(10, 2), pady=5)
        try:
            grip.configure(cursor="fleur")
        except Exception:
            pass
        grip.bind("<Button-1>", lambda e, r=row: self._drag_start(e, r))
        grip.bind("<B1-Motion>", self._drag_motion)
        grip.bind("<ButtonRelease-1>", self._drag_end)

        # 整行可拖拽（除按钮/开关外）：在 row 和 mid 上绑定同一套拖拽事件，
        # handler 内部会忽略来自按钮/开关的事件，保留它们原有的点击行为。
        for _w in (row, mid):
            _w.bind("<Button-1>", lambda e, r=row: self._drag_start(e, r))
            _w.bind("<B1-Motion>", self._drag_motion)
            _w.bind("<ButtonRelease-1>", self._drag_end)

        badge = ctk.CTkLabel(row, text="·", font=self.fonts["body_b"], width=26, height=26,
                             corner_radius=13, text_color=T.TEXT_DIM, fg_color=T.SURFACE)
        badge.grid(row=0, column=1, rowspan=2, padx=(2, 12), pady=5)

        mid = ctk.CTkFrame(row, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=5)
        mid.grid_columnconfigure(0, weight=0)
        mid.grid_columnconfigure(1, weight=1)
        chip_fg = T.BTN
        chip = ctk.CTkFrame(mid, fg_color=chip_fg, corner_radius=T.RADIUS_SM)
        title = ctk.CTkLabel(chip, text=label, font=self.fonts["body_b"],
                             text_color=T.TEXT, cursor="hand2", anchor="w")
        title._chip_base = chip_fg
        title._txt_base = T.TEXT
        title.grid(row=0, column=0, padx=12, pady=4)
        chip.grid(row=0, column=0, sticky="w")
        if kind == "task":
            title.bind("<Button-1>", lambda e, nm=name: self._start_single(nm))
        title.bind("<Enter>",
                   lambda e, ch=chip, tl=title: (ch.configure(fg_color=T.ACCENT),
                                                 tl.configure(text_color=T.ON_ACCENT)))
        title.bind("<Leave>",
                   lambda e, ch=chip, tl=title:
                   (ch.configure(fg_color=getattr(tl, "_chip_base", T.BTN)),
                    tl.configure(text_color=getattr(tl, "_txt_base", T.TEXT))))
        Tooltip(title, f"点任务名 = 单独跑这一个任务（一次性，不落盘）\n整条龙一起跑用「开始日常」",
                self.fonts)

        st_text, st_color = self._ready_meta(name, step.get("enabled", True), kind)
        ready_lbl = ctk.CTkButton(mid, text="", font=self.fonts["small"], height=24,
                                  corner_radius=T.RADIUS_SM, border_width=0)
        self._style_ready(ready_lbl, st_text, st_color)
        ready_lbl.grid(row=0, column=1, sticky="e", padx=(10, 0))

        cfg_btn = ctk.CTkButton(row, text="⚙ 配置", font=self.fonts["small"], height=24,
                                corner_radius=T.RADIUS_SM, border_width=0,
                                fg_color=T.BTN, text_color=T.TEXT, cursor="hand2",
                                command=lambda nm=name: self._open_task_config(nm))
        cfg_btn.grid(row=0, column=3, rowspan=2, padx=(4, 6), pady=5)
        Tooltip(cfg_btn, "打开该任务在「任务配置」页的配置卡（标定 / 参数）。", self.fonts)

        var = ctk.BooleanVar(value=bool(step.get("enabled", True)))
        sw = ctk.CTkSwitch(row, text="", variable=var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                           command=lambda nm=key, v=var: self._toggle_step(nm, v, kind))
        sw.grid(row=0, column=4, rowspan=2, padx=(8, 14), pady=5)

        return {"frame": row, "name": name, "badge": badge, "ready": ready_lbl,
                "ready_text": st_text, "ready_color": st_color, "title": title,
                "chip": chip, "sw": sw, "kind": kind, "key": key, "step": step}

    def _ready_meta(self, name, enabled, kind):
        if kind == "flow":
            return ("开启" if enabled else "停用",
                    T.SUCCESS if enabled else T.TEXT_DIM)
        if not enabled:
            return "未选中", T.TEXT_DIM
        if not self._task_status(name):
            return "⚠ 还需标定", T.WARN
        return "✓ 已就绪", T.SUCCESS

    def _style_ready(self, btn, text, color):
        btn.configure(text=text, text_color=color, fg_color="transparent",
                      hover_color=T.SURFACE_2, border_width=0, cursor="arrow",
                      command=None)

    def _grid_all(self):
        for pos, entry in enumerate(self._flat):
            kind, key = entry
            r = self._rows[key]
            f = r["frame"]
            f.grid(row=pos, column=0, sticky="ew", pady=3, padx=4)
        self._renumber()

    def _renumber(self):
        order = 0
        for entry in self._flat:
            kind, key = entry
            r = self._rows[key]
            step = r["step"]
            if kind == "task" and self._runs_now(step):
                order += 1
                r["badge"].configure(text=str(order), fg_color=T.ACCENT,
                                     text_color=T.ON_ACCENT)
            else:
                r["badge"].configure(text="·", fg_color=T.SURFACE, text_color=T.TEXT_DIM)
        self._refresh_exec_order()

    def _refresh_exec_order(self):
        names = []
        for entry in self._flat:
            kind, key = entry
            r = self._rows[key]
            if kind == "task" and self._runs_now(r["step"]):
                names.append(self._task_display_name(r["name"]))
        if names:
            self.lbl_exec_order.configure(text=" -> ".join(names) +
                                          f" (共{len(names)}项)", text_color=T.ACCENT)
        else:
            self.lbl_exec_order.configure(text="（无，开始日常只会空转）",
                                          text_color=T.TEXT_DIM)
    # ------------------------------------------------------------------ 启用切换 / 拖拽 / 保存
    def _toggle_step(self, key, var, kind):
        if kind == "task":
            for s in self._steps:
                if s["task"] == key:
                    s["enabled"] = bool(var.get())
                    break
            self._save_steps()
        else:
            self._set_flow_control(key, bool(var.get()))
        rec = self._rows.get(key)
        if rec is not None:
            st_text, st_color = self._ready_meta(rec["name"],
                                                 bool(var.get()), kind)
            rec["ready_text"], rec["ready_color"] = st_text, st_color
            self._style_ready(rec["ready"], st_text, st_color)
        self._steps_sig = None
        self._render_steps()

    def _save_steps(self):
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        tc["steps"] = [{"task": s["task"], "enabled": bool(s["enabled"])}
                       for s in self._steps]
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

    def _drag_start(self, event, frame):
        # 点击按钮/开关/下拉框等交互控件时，不启动拖拽（保留它们原有行为）
        src = event.widget
        from customtkinter import CTkButton, CTkSwitch, CTkComboBox
        if isinstance(src, (CTkButton, CTkSwitch, CTkComboBox)):
            return
        for k, r in self._rows.items():
            if r["frame"] is frame:
                self._drag = {"key": k}
                frame.configure(fg_color=T.BTN)
                frame.tkraise()
                return

    def _drag_motion(self, event):
        if not self._drag:
            return
        tgt = self._target_at_pointer()
        if tgt is None or tgt == self._drag["key"]:
            return
        # 拖拽过程中只调整显示顺序（不改 _steps 数据、不存盘），避免连续写盘导致闪烁
        self._reorder_flat(self._drag["key"], tgt)
        self._grid_all()
        self._drag["key"] = tgt

    def _drag_end(self, event):
        if not self._drag:
            return
        self._rows[self._drag["key"]]["frame"].configure(fg_color=T.SURFACE_2)
        # 把拖拽过程累积的 _flat 顺序同步回 _steps 数据，并一次性存盘
        self._sync_steps_from_flat()
        self._save_steps()
        self._drag = None
        self._grid_all()

    def _target_at_pointer(self):
        py = self.list_frame.winfo_pointery()
        for entry in self._flat:
            kind, key = entry
            f = self._rows[key]["frame"]
            if not f.winfo_ismapped():
                continue
            if py < f.winfo_rooty() + f.winfo_height() / 2:
                return key
        return None

    def _reorder_flat(self, frm_key, to_key):
        """只调整 _flat 的显示顺序（拖拽过程中用），不碰 _steps、不存盘。"""
        flat_items = [e for e in self._flat]
        frm_idx = next((i for i, e in enumerate(flat_items) if e[1] == frm_key), None)
        to_idx = next((i for i, e in enumerate(flat_items) if e[1] == to_key), None)
        if frm_idx is not None and to_idx is not None:
            flat_items.insert(to_idx, flat_items.pop(frm_idx))
            self._flat = flat_items

    def _sync_steps_from_flat(self):
        """把 _flat 里 task 行的顺序写回 _steps（拖拽结束时调用）。"""
        task_order = [key for kind, key in self._flat if kind == "task"]
        new_steps = [s for s in self._steps if s["task"] in task_order]
        new_steps.sort(key=lambda s: task_order.index(s["task"]))
        self._steps = new_steps

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

    # ------------------------------------------------------------------ 运行控制（不绑按钮，供 pump 定时自动启动使用）
    def _start_runner(self):
        task_cls = get_task(self.TASK_NAME)
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动：" + p, "error")
            self.runner = None
            return
        # 无「开始日常」按钮，运行状态由全局侧栏药丸展示

    def _log_line(self, msg, level="info"):
        if hasattr(self.app, "log_line"):
            self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
        else:
            print(f"[{level}] {msg}")

    def _on_runner_finished(self):
        self.runner = None

    def pump(self):
        if self.runner:
            for level, msg, src in self.runner.drain_logs():
                self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))
            if not self.runner.is_running():
                self._on_runner_finished()
        elif self._wait_until is not None:
            if datetime.now() >= self._wait_until:
                self._wait_until = None
                self._log_line("⏰ 定时时刻到，开始日常…", "warn")
                self._start_runner()

    @staticmethod
    def _sched_wait_target(sched, now):
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

    # ------------------------------------------------------------------ 单跑
    def _start_single(self, name):
        if self.runner and self.runner.is_running():
            self._log_line("整条日常正在跑，请先在「日常」页停止再单跑。", "warn")
            return
        if not self._task_status(name):
            self._log_line(f"「{name}」还需标定，单跑未启动。", "warn")
            return
        run_cfg = copy.deepcopy(self.app.cfg)
        daily = run_cfg.setdefault("tasks", {}).setdefault("daily", {})
        daily["steps"] = [{"task": name, "enabled": True}]
        daily["explicit_steps"] = True
        daily["group_enabled"] = {"single": True, "multi": True}
        loop = daily.setdefault("loop", {})
        loop["time_limit_min"] = 0
        loop["shutdown_after"] = False
        loop.pop("schedule", None)
        run_cfg.setdefault("tasks", {}).setdefault("teaming", {})["auto_disband"] = False
        run_cfg.setdefault("tasks", {}).setdefault("organize_bag", {})["auto_organize"] = False
        runner = TaskRunner(get_task(self.TASK_NAME)(), run_cfg)
        ok, problems = runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法启动单跑：" + p, "error")
            return
        self.runner = runner

    def _open_task_config(self, name):
        cp = self.app.pages.get("config")
        if cp is not None and hasattr(cp, "reveal_card"):
            try:
                cp.reveal_card(name)
            except Exception:
                pass
