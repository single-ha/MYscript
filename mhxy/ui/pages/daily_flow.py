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
"""

import copy

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
from ..common import Card, Tooltip, bind_wraplength

_SINGLE_ORDER = list(SINGLE_TASK_ORDER)

_TASK_TITLES = {
    "treasure_map": "宝图", "secret_realm": "秘境降妖", "appreciation": "趣味鉴赏",
    "sanjie": "三界奇缘", "escort": "运镖", "guild_checkin": "帮派签到",
    "activity_reward": "活跃度奖励", "guaguale": "刮刮乐", "zhuagui": "抓鬼",
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

        self.var_limit = ctk.StringVar(value="0")
        self.var_shutdown = ctk.BooleanVar(value=False)
        self.var_schedule_on = ctk.BooleanVar(value=False)
        self.var_schedule_time = ctk.StringVar(value="10:00")
        self.var_disband = ctk.BooleanVar(value=False)
        self.var_skip_team = ctk.BooleanVar(value=False)
        self.switch_auto_organize = None
        self.switch_auto_tuoying = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        self._build_header()
        self._build_control()
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

    # ------------------------------------------------------------------ 控制区
    def _build_control(self):
        card = Card(self)
        card.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        top.grid_columnconfigure(1, weight=1)
        self.btn_run = ctk.CTkButton(top, text=self.RUN_LABEL, font=self.fonts["btn"],
                                     height=46, width=200, corner_radius=T.RADIUS_SM,
                                     fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                     text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(top, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        ctk.CTkButton(tools, text="调整窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._adjust_windows).pack(side="left")
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left", padx=(8, 0))

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(
            row=1, column=0, sticky="ew", padx=16, pady=(0, 4))

        opts = ctk.CTkFrame(card, fg_color="transparent")
        opts.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 10))
        opts.grid_columnconfigure(0, weight=1)
        opts.grid_columnconfigure(1, weight=1)

        lim = ctk.CTkFrame(opts, fg_color="transparent")
        lim.grid(row=0, column=0, sticky="w", padx=(0, 26), pady=(10, 0))
        ctk.CTkLabel(lim, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"],
                     text_color=T.TEXT).pack(side="left")
        self.lim_ent = ctk.CTkEntry(lim, textvariable=self.var_limit, width=70,
                                    font=self.fonts["body"], fg_color=T.SURFACE_2,
                                    border_color=T.BORDER)
        self.lim_ent.pack(side="left", padx=(8, 0))

        tuo = ctk.CTkFrame(opts, fg_color="transparent")
        tuo.grid(row=0, column=1, sticky="e", pady=(10, 0))
        tuo_lbl = ctk.CTkLabel(tuo, text="自动拓印（关闭后遇拓印弹窗转手动）",
                               font=self.fonts["body"], text_color=T.TEXT)
        tuo_lbl.pack(side="left")
        self.switch_auto_tuoying = ctk.CTkSwitch(
            tuo, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN,
            button_color=T.ON_ACCENT, command=self._toggle_auto_tuoying)
        self.switch_auto_tuoying.pack(side="left", padx=(8, 0))
    # ------------------------------------------------------------------ 主体
    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

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
        self.var_disband.set(bool(cfg_mod.task_config(self.app.cfg, "teaming")
                                  .get("auto_disband", False)))
        self.var_skip_team.set(bool(cfg_mod.task_config(self.app.cfg, "teaming")
                                    .get("skip_team", False)))
        ob = cfg_mod.task_config(self.app.cfg, "organize_bag")
        tuo_tc = cfg_mod.task_config(self.app.cfg, "tuoying")
        self._render_steps()
        self._sync_controls(ob, tuo_tc)

    def _sync_controls(self, ob, tuo_tc):
        if self.switch_auto_organize is not None:
            if ob.get("auto_organize"):
                self.switch_auto_organize.select()
            else:
                self.switch_auto_organize.deselect()
        if self.switch_auto_tuoying is not None:
            if tuo_tc.get("enabled", True):
                self.switch_auto_tuoying.select()
            else:
                self.switch_auto_tuoying.deselect()

    @staticmethod
    def _normalize(stored):
        out, seen = [], set()
        for s in stored or []:
            if isinstance(s, dict) and s.get("task") in CHAINABLE and s["task"] not in seen:
                out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
                seen.add(s["task"])
        for t in CHAINABLE:
            if t not in seen:
                out.append({"task": t, "enabled": True})
                seen.add(t)
        return out

    def _task_display_name(self, name):
        if name == "dungeon":
            titles = get_selected_dungeon_titles(self.app.cfg)
            if titles:
                return " -> ".join(titles)
            return "刷副本（未勾选）"
        cls = get_task(name)
        return cls.title if cls else name

    def _task_status(self, name):
        sub = cfg_mod.task_config(self.app.cfg, name)
        return self._calib_done(name, sub)

    def _calib_done(self, name, sub):
        spec = getattr(get_task(name), "CALIBRATION", None) or {}
        need_r = [k for k in spec.get("regions", []) if k]
        need_t = [k for k in spec.get("templates", []) if k]
        regions, templates = sub.get("regions", {}), sub.get("templates", {})
        return all(regions.get(k) for k in need_r) and all(templates.get(k) for k in need_t)

    def _runs_now(self, step):
        if not step.get("enabled"):
            return False
        name = step.get("task")
        if name == "dungeon" and not get_selected_dungeon_titles(self.app.cfg):
            return False
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
        if name == "dungeon" and not get_selected_dungeon_titles(self.app.cfg):
            return "未勾选副本", T.WARN
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
        if tgt is None:
            return
        if tgt == self._drag["key"]:
            return
        self._reorder(self._drag["key"], tgt)
        self._drag["key"] = tgt

    def _drag_end(self, event):
        if not self._drag:
            return
        self._rows[self._drag["key"]]["frame"].configure(fg_color=T.SURFACE_2)
        self._drag = None
        self._save_steps()
        self._steps_sig = None
        self._render_steps()

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

    def _reorder(self, frm_key, to_key):
        task_items = [s for s in self._steps]
        frm_idx = next((i for i, s in enumerate(task_items) if s["task"] == frm_key), None)
        to_idx = next((i for i, s in enumerate(task_items) if s["task"] == to_key), None)
        if frm_idx is not None and to_idx is not None:
            task_items.insert(to_idx, task_items.pop(frm_idx))
            self._steps = task_items
        self._save_steps()
        self._grid_all()
    def _adjust_windows(self):
        from ...core import window as win_mod
        cfg = cfg_mod.load_config()
        self.app.cfg = cfg
        win_mod.arrange_windows(cfg, self.app)
        self.refresh()

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

    def _toggle_auto_organize(self, on):
        self._set_flow_control("organize", on)

    def _toggle_run(self, save=True):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return
        if save:
            self._save_steps()
        self.app.cfg = cfg_mod.load_config()
        self._start_runner()

    def _start_runner(self):
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
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                               state="normal")

    def _log_line(self, msg, level="info"):
        if hasattr(self.app, "log_line"):
            self.app.log_line(msg, level)
        else:
            print(f"[{level}] {msg}")

    def _start_single(self, name):
        if self.runner and self.runner.is_running():
            self._log_line("整条日常正在跑，请先「停止」再单跑。", "warn")
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
        self.btn_run.configure(text="■  停止", fg_color=T.DANGER,
                               hover_color=T.DANGER_HOVER, state="normal")

    def _open_task_config(self, name):
        cp = self.app.pages.get("config")
        if cp is not None and hasattr(cp, "reveal_card"):
            try:
                cp.reveal_card(name)
            except Exception:
                pass
