# -*- coding: utf-8 -*-
"""刷副本页（副本中枢：勾选一个或多个已收录副本，按顺序一个个顺序刷）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。

多副本顺序刷机制（本页承担，不侵入各副本任务）：
  · 「开始」把勾选的副本名按勾选顺序排成队列 self._queue；
  · 每个副本用一个独立 TaskRunner 跑（各副本任务内部自带「先组队再跑」流程，本页不碰）；
  · pump() 轮询当前 runner：跑完（正常结束、超时/异常中止都算）就自动接下一个；
    某副本 preflight 不过/异常 → 记日志、跳过，继续下一个（「失败跳过」）；
  · 「停止」置 _abort，当前副本停后不再接下一个、清空队列。"""

import re
import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...tasks.base import dungeon_tasks
from ...tasks.dungeon_base import DUNGEON_CALIBRATION
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ..common import Card, bind_wraplength, teaming_ns, teaming_ready, required_regions, required_templates


class DungeonPage(ctk.CTkFrame):
    """副本中枢：勾选多个副本次序刷。新增副本写个 is_dungeon=True 的 Task 即自动出现在勾选列表里。"""

    TASK_NAME = "dungeon"
    LOG_SOURCE = "刷副本"
    RUN_LABEL = "▶  开始刷副本"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.runner = None
        self._queue = []               # 尚未开始的副本名（按勾选顺序）
        self._current_name = None      # 正在跑的副本名
        self._abort = False            # 停止：当前副本停后不再接下一个
        self._cal_dialog = None        # 副本自身「标定」的去重槽
        self._win_count = 0
        # 已收录的副本（按注册顺序）。title 显示、name 作配置命名空间键。
        self._dungeons = dungeon_tasks()
        self._dtitles = [c.title for c in self._dungeons]
        self._checkboxes = {}                 # name -> (CB, var)

        # 副本展示/运行顺序：第一行侠士本、第二行普通本，每行内按等级低→高。
        # 与刷副本列无关——这里只决定「显示/勾选顺序」，运行也按此顺序（所见即所刷）。
        def _sort_key(name):
            m = re.match(r"dt_(\d+)_([a-z]+?)(\d*)$", name)
            if not m:
                return (0, 0)
            # 等级低→高；同等级里按子序号小→大（60普通1 在 60普通2 前）
            return (int(m.group(1)), int(m.group(3) or 0))
        self._cat_order = ["xiashi", "common"]                     # 第1行侠士本、第2行普通本
        self._cat_label = {"xiashi": "侠士本", "common": "普通本"}
        self._layout = {cat: [] for cat in self._cat_order}
        for c in self._dungeons:
            cat = getattr(c, "cat", "common")
            if cat not in self._layout:
                self._layout[cat] = []
            self._layout[cat].append(c.name)
        for names in self._layout.values():
            names.sort(key=_sort_key)
        self._display_names = [n for cat in self._cat_order for n in self._layout[cat]]
        self._selected = list(self._display_names)   # 勾选的副本名列表（按 _display_names 顺序）

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        self._build_header()
        self._build_control()
        self._build_body()
        self.refresh()

    # ---- 选中副本的辅助 ----
    def _names_in(self, order):
        return [n for n in order if n in self._selected]

    def _selected_names(self):
        return self._names_in(self._display_names)

    def _selected_in_cat(self, cat):
        return self._names_in(self._layout.get(cat, []))

    def _selected_titles(self):
        return [c.title for c in self._dungeons if c.name in self._selected]

    def _build_header(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(bar, text="刷副本", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(bar, text="按勾选顺序一个个自动刷",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)

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
        ctk.CTkButton(tools, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")

    def _build_body(self):
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=2, column=0, sticky="nsew", padx=4)
        body.grid_columnconfigure(0, weight=1)   # 日志已移到全局右栏，主体内容独占整宽
        body.grid_rowconfigure(0, weight=1)

        # 左：选副本 + 标定状态
        left = Card(body)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        left.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(left, text="副本设置", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=16, pady=(14, 6))

        # 勾选区：第一行侠士本、第二行普通本，每行内按等级高→低横向排布
        sel = ctk.CTkFrame(left, fg_color="transparent")
        sel.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
        sel.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(sel, text="选择要刷的副本（可多选，按顺序一个个刷）",
                     font=self.fonts["body"], text_color=T.TEXT).grid(row=0, column=0, sticky="w")
        self._cks = ctk.CTkFrame(sel, fg_color="transparent")
        self._cks.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        for row, cat in enumerate(self._cat_order):
            rowbox = ctk.CTkFrame(self._cks, fg_color="transparent")
            rowbox.grid(row=row, column=0, sticky="ew", pady=(0, 6))
            ctk.CTkLabel(rowbox, text=self._cat_label[cat] + "（等级低→高）",
                         font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(0, 2))
            cs = ctk.CTkFrame(rowbox, fg_color="transparent")
            cs.pack(anchor="w")
            for col, name in enumerate(self._layout[cat]):
                c = next((cc for cc in self._dungeons if cc.name == name), None)
                if c is None:
                    continue
                var = ctk.BooleanVar(value=(name in self._selected))
                cb = ctk.CTkCheckBox(cs, text=c.title, variable=var, font=self.fonts["body"],
                                     text_color=T.TEXT, fg_color=T.SURFACE_2,
                                     hover_color=T.BORDER, checkmark_color=T.ON_ACCENT,
                                     border_color=T.BORDER, command=lambda n=name: self._on_toggle(n))
                cb.grid(row=0, column=col, sticky="w", padx=(0, 18), pady=3)
                self._checkboxes[name] = (cb, var)

        # 组队设置（队长/已组队/跑完解散）统一在「通用/多人任务」页共用一份，存共享 tasks.teaming
        hint = ctk.CTkLabel(left, text="按勾选顺序一个个刷，失败自动跳过下一个。\n"
                                       "组队标定在「通用」页、副本模板用本页「标定」。",
                             font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=2, column=0, sticky="ew", padx=16, pady=(2, 8))
        bind_wraplength(hint)

        self.lbl_calib = ctk.CTkLabel(left, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=3, column=0, sticky="ew", padx=16, pady=(2, 14))
        bind_wraplength(self.lbl_calib)

        # 日志已统一到 App 右侧的全局日志面板，本页不再单独建日志框。

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        hub = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        sel = hub.get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if isinstance(sel, list):
            sel = [n for n in self._display_names if n in sel]
        else:
            sel = []
        if not sel:
            sel = list(self._display_names[:1])
        self._selected = sel
        for name, (cb, var) in self._checkboxes.items():
            var.set(name in self._selected)
        # 组队设置（已组队/解散）统一在「多人任务」页共用一份，存共享 tasks.teaming
        self._render_team_status()
        self._kick_count_windows()

    def _selected_skip_team(self):
        return teaming_ns(self.app.cfg).get("skip_team", False)

    def _dungeons_calib_counts(self):
        """所有副本共用一套标定：按共享 DUNGEON_CALIBRATION 汇总 tasks.dungeon 完成情况。
        一次标定覆盖全部副本，返回 ((rdone, rneed, tdone, tneed), 全齐?)。"""
        spec = DUNGEON_CALIBRATION
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        regions, templates = tc.get("regions", {}), tc.get("templates", {})
        # 副本的公共区域（活动列表）不在 spec 里，按 TASK_SHARED_REQ 补查（统一在「通用」页标定）
        need_r = required_regions(spec)
        need_r += [k for k in cfg_mod.TASK_SHARED_REQ.get(self.TASK_NAME, ()) if k not in need_r]
        # 共享模板同理：小闹钟(寻路)已迁「通用」页「标定（公共区域）」，按 TASK_SHARED_TPL_REQ 补查，
        # task_config 已把 shared 模板叠加进 tasks.dungeon templates，直接 templates.get(k) 即得。
        need_t = required_templates(spec)
        need_t += [k for k in cfg_mod.TASK_SHARED_TPL_REQ.get(self.TASK_NAME, ()) if k not in need_t]
        rdone = sum(1 for k in need_r if regions.get(k))
        tdone = sum(1 for k in need_t if templates.get(k))
        ok = (rdone == len(need_r) and tdone == len(need_t))
        return (rdone, len(need_r), tdone, len(need_t)), ok

    def _render_team_status(self):
        """据当前 self._win_count + 组队标定 + 副本标定，渲染就绪状态（紧凑摘要）。"""
        n = self._win_count
        team_tc = teaming_ns(self.app.cfg)
        skip_team = team_tc.get("skip_team", False)
        cap = team_tc.get("captain_index", 0)
        if not (0 <= cap < max(1, n)):
            cap = 0
        team_ok = teaming_ready(team_tc)
        (rdone, rneed, tdone, tneed), dun_ok = self._dungeons_calib_counts()

        if skip_team:
            ready = dun_ok and n >= 1
            team_line = "组队标定：已勾「已组队」，本次跳过组队\n"
            tail = "　✓ 可运行" if ready else "　（需≥1 个号 且副本标定齐全）"
        else:
            ready = team_ok and dun_ok and n >= 2
            team_line = "组队标定：" + ("齐全 ✓" if team_ok
                                      else "需标定（去「通用」页标定组队）") + "\n"
            tail = "　✓ 可运行" if ready else "　（需多开≥2 且组队+副本标定齐全）"
        dun_line = f"副本标定：区域 {rdone}/{rneed}，模板 {tdone}/{tneed}" + (
            "　✓（一次标定覆盖全部副本）" if dun_ok else "　✗（用本页「标定」补齐）")
        color = T.SUCCESS if ready else T.WARN
        self.lbl_calib.configure(text=f"{team_line}{dun_line}\n已选 {n} 个号，队长=号{cap + 1}" + tail,
                                 text_color=color)

    def _title_of(self, name):
        for c in self._dungeons:
            if c.name == name:
                return c.title
        return name

    def _cat_of(self, name):
        """该副本所属标签区("xiashi"/"common")，供计算同区队列序号。"""
        cls = get_task(name)
        return getattr(cls, "cat", "common") if cls else "common"

    def _write_enter_target(self, name):
        """启动某副本前，把 {cat, pos} 写进 tasks.dungeon.enter_target。
        pos = 该副本在其标签区里的序号(0-based)。游戏进入列表按标签区从上到下排布，
        用该区展示顺序(等级低→高，_layout[cat]，即 GUI 勾选区所见顺序)当基准，多命中点按(行,列)排序后取第 pos 个。
        注意：不能用「勾选队列」算序号——只勾一个时它在队列里是 0，但物理位置未必是列表第一个（曾进错副本）。"""
        cat = self._cat_of(name)
        order = self._layout.get(cat, [])
        pos = order.index(name) if name in order else 0
        cfg = cfg_mod.load_config()
        hub = cfg_mod.task_config(cfg, self.TASK_NAME)
        hub["enter_target"] = {"cat": cat, "pos": pos}
        cfg_mod.set_task_config(cfg, self.TASK_NAME, hub)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg


    def _kick_count_windows(self):
        """后台枚举已选窗口数，变了再回主线程重渲染。token 丢弃过期结果。"""
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        targets = self.app.cfg.get("targets", {})
        token = object()
        self._count_token = token

        def work():
            try:
                n = len(win_mod.resolve_targets(title, offset, targets))
            except Exception:
                n = 0

            def apply():
                if token is not getattr(self, "_count_token", None):
                    return
                if n != self._win_count:
                    self._win_count = n
                    self._render_team_status()

            try:
                self.app.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _on_toggle(self, name):
        """勾选/取消勾选副本：写进中枢命名空间 tasks.dungeon.selected（保存勾选顺序）。"""
        _, var = self._checkboxes[name]
        cfg = cfg_mod.load_config()
        hub = cfg_mod.task_config(cfg, self.TASK_NAME)
        sel = hub.get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if not isinstance(sel, list):
            sel = []
        sel = [n for n in self._display_names if n in sel]
        if var.get():
            if name not in sel:
                sel.append(name)
        else:
            sel = [n for n in sel if n != name]
        hub["selected"] = sel
        cfg_mod.set_task_config(cfg, self.TASK_NAME, hub)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._selected = sel
        self._render_team_status()
        if var.get():
            self._log_line(f"已勾选副本：{name}（当前共 {len(sel)} 个）", "info")
        else:
            self._log_line(f"已取消副本：{name}", "info")

    # ---- 运行控制（多副本顺序刷队列）----
    def _toggle_run(self):
        if self.runner and self.runner.is_running():
            self._abort = True
            self.runner.stop()
            self._log_line("正在停止（当前副本停后不再接下一个）…", "warn")
            self.btn_run.configure(text="停止中…", state="disabled")
            return

        names = self._selected_names()
        if not names:
            self._log_line("还没有勾选任何副本。", "error")
            return
        self.app.cfg = cfg_mod.load_config()   # 组队设置已由「多人任务」页管理，这里重载最新配置即可
        self._abort = False
        self._queue = list(names)
        self._log_line(f"开始刷副本队列：{' → '.join(self._title_of(n) for n in names)}", "hit")
        if self._start_next():
            self.btn_run.configure(text="■  停止", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _start_next(self):
        """从队列取下一个副本并启动其 runner。启动成功返回 True；队列空/无下一个返回 False。"""
        while self._queue:
            name = self._queue.pop(0)
            if self._abort:
                self._log_line("已停止。", "warn")
                return False
            task_cls = get_task(name)
            if task_cls is None:
                self._log_line(f"找不到副本任务「{name}」，跳过。", "error")
                continue
            self._current_name = name
            self._write_enter_target(name)   # 写 {cat, pos}：该副本同标签区内的队列序号，供多命中点取第几个「进入」
            runner = TaskRunner(task_cls(), self.app.cfg)
            ok, problems = runner.start()   # preflight 不过则不启动（当作该副本失败，跳过下一个）
            if not ok:
                self._log_line(f"副本「{self._title_of(name)}」无法启动，跳过下一个：", "error")
                for p in problems:
                    self._log_line("　· " + p, "error")
                self._current_name = None
                continue
            self.runner = runner
            self._log_line(f"★ 开始刷副本：{self._title_of(name)} ★", "hit")
            self._render_team_status()
            return True
        # 队列清空且没启动新的 → 全部结束或已停止
        self._current_name = None
        self._finish_all()
        return False

    def _finish_all(self):
        self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT,
                               hover_color=T.ACCENT_HOVER, state="normal")
        self._current_name = None
        self.runner = None
        self._log_line("全部副本刷完,结束任务", "warn")

    def _on_runner_finished(self):
        """当前副本已结束（正常/超时/异常都算）。若未停止则接下一个副本。"""
        if self._abort:
            self._current_name = None
            self._finish_all()
            return
        if self._queue:
            self._start_next()
        else:
            self._finish_all()

    def _open_calibrate(self):
        """打开副本共用标定向导（一次标定所有副本，写入共享 tasks.dungeon；组队标定在「通用」页）。"""
        if getattr(self, "_cal_dialog", None) is not None:
            try:
                if self._cal_dialog.winfo_exists():
                    self._cal_dialog.lift()
                    self._cal_dialog.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            self._cal_dialog = None
            self.refresh()
            self._log_line("标定完成，配置已更新（一次标定覆盖全部副本）。", "info")

        try:
            self._cal_dialog = CalibrateDialog(self.app, task_name=self.TASK_NAME, on_done=_after)
        except Exception as e:
            self._cal_dialog = None
            self._log_line(f"打开标定向导失败：{e}", "error")

    # ---- 日志（由 App._tick 驱动）----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level)
            if not self.runner.is_running() and self.btn_run.cget("text") != self.RUN_LABEL:
                self._on_runner_finished()

    def _log_line(self, msg, level="info"):
        # 日志统一汇到 App 右侧全局面板，按本页 LOG_SOURCE 打来源标签。
        self.app.log_line(msg, level, getattr(self, "LOG_SOURCE", None))

    def _clear_log(self):
        self.app.clear_log()
