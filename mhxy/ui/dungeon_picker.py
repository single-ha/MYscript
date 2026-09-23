# -*- coding: utf-8 -*-
"""
副本勾选组件（单源配置 tasks.dungeon.selected）：
  唯一入口在「日常」页「刷副本」步（「任务配置」页只做副本共用标定，不含勾选）。
  组件自管布局（侠士本/普通本两行、每行等级低→高）与选中同步；宿主传 on_change(name, checked, sel)
  刷新自己的就绪状态。on_run(name) 可选：提供了则每个副本的勾选框改成【无文字勾选框 + 副本名即按钮】，
  勾选框只管勾选、点副本名=单跑那一本。
  组件本身不写日志（日志归属各宿主按其 LOG_SOURCE 打）。
"""

import re

import customtkinter as ctk

from . import theme as T
from .common import Tooltip
from ..core import config as cfg_mod
from ..tasks.base import dungeon_tasks


class DungeonPicker(ctk.CTkFrame):
    """副本勾选网格。每个 checkbox 直接读写 tasks.dungeon.selected（按展示顺序归一），
    与刷副本页的勾选区行为完全一致（同一份 config，谁勾都同步）。
    collapsible=True 时标题行变成折叠开关（▾/▸，点标题可展开/收起勾选区），
    折叠/展开状态经 on_open_change(open) 通知宿主持久化（宿主重建组件时可回传 default_open）。"""

    TASK_NAME = "dungeon"

    def __init__(self, master, app, fonts, on_change=None, caption=None,
                 collapsible=False, default_open=True, on_open_change=None, on_run=None):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = fonts
        self._on_change = on_change
        self._collapsible = bool(collapsible)
        self._open = bool(default_open)
        self._on_open_change = on_open_change
        self._on_run = on_run           # 「单跑」回调：提供了就把每个副本名渲染成可点的单跑按钮
        self._cat_rows = []            # [(rowidx, frame)] 供折叠 grid_remove/grid 恢复
        self.chevron = None
        self._dungeons = dungeon_tasks()

        # 展示/勾选顺序：第一行侠士本、第二行普通本，每行内按等级低→高。取自各 Task.cat。
        def _sort_key(name):
            m = re.match(r"dt_(\d+)_([a-z]+?)(\d*)$", name)
            if not m:
                return (0, 0)                          # 非法名垫底
            return (int(m.group(1)), int(m.group(3) or 0))

        self.cat_order = ["xiashi", "common"]
        self.cat_label = {"xiashi": "侠士本", "common": "普通本"}
        self.layout = {c: [] for c in self.cat_order}
        for c in self._dungeons:
            cat = getattr(c, "cat", "common")
            if cat not in self.layout:
                self.layout[cat] = []
            self.layout[cat].append(c.name)
        for names in self.layout.values():
            names.sort(key=_sort_key)
        self.display_names = [n for cat in self.cat_order for n in self.layout[cat]]

        self._vars = {}                # name -> (CB, var)
        self._selected = []            # 当前勾选（按 display_names 顺序）
        self.grid_columnconfigure(0, weight=1)   # 本列伸展，标题行/勾选行填满组件宽
        self._build(caption)

    # ------------------------------------------------------------------
    def _build(self, caption):
        row0 = 0
        if caption:
            if self._collapsible:
                # 标题行 = 折叠开关：chevron + 标题（点哪都切换展开/收起）。
                # pack left 顺序排列：chevron 固定宽、标题 fill x expand → 无论展开收起都贴左，不会顶到右缘。
                head = ctk.CTkFrame(self, fg_color="transparent")
                head.grid(row=0, column=0, sticky="ew", pady=(0, 4))
                self.chevron = ctk.CTkLabel(head, text="▾" if self._open else "▸",
                                            font=self.fonts["body_b"], text_color=T.TEXT_DIM,
                                            width=20, anchor="w")
                self.chevron.pack(side="left")
                cap = ctk.CTkLabel(head, text=caption, font=self.fonts["body_b"], text_color=T.TEXT,
                                   justify="left", anchor="w")
                cap.pack(side="left", fill="x", expand=True)
                for w in (self.chevron, cap):
                    try:
                        w.configure(cursor="hand2")
                    except Exception:
                        pass
                    w.bind("<Button-1>", lambda e: self.toggle())
            else:
                cap = ctk.CTkLabel(self, text=caption, font=self.fonts["small"],
                                   text_color=T.TEXT_DIM, justify="left", anchor="w")
                cap.grid(row=0, column=0, sticky="ew", pady=(0, 4))
            row0 = 1
        for r, cat in enumerate(self.cat_order, start=row0):
            rowbox = ctk.CTkFrame(self, fg_color="transparent")
            rowbox.grid(row=r, column=0, sticky="ew", pady=(0, 4))
            self._cat_rows.append((r, rowbox))
            ctk.CTkLabel(rowbox, text=self.cat_label[cat] + "（等级低→高）",
                         font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(0, 2))
            cs = ctk.CTkFrame(rowbox, fg_color="transparent")
            cs.pack(anchor="w")
            for col, name in enumerate(self.layout[cat]):
                c = next((cc for cc in self._dungeons if cc.name == name), None)
                if c is None:
                    continue
                var = ctk.BooleanVar(value=False)
                cell = ctk.CTkFrame(cs, fg_color="transparent")
                cell.grid(row=0, column=col, sticky="w", padx=(0, 14), pady=2)
                cb = ctk.CTkCheckBox(cell, text=("" if self._on_run else c.title),
                                     variable=var, font=self.fonts["body"],
                                     width=(26 if self._on_run else 120),
                                     text_color=T.TEXT, fg_color=T.SURFACE_2,
                                     hover_color=T.BORDER, checkmark_color=T.ON_ACCENT,
                                     border_color=T.BORDER, command=lambda n=name: self._on_toggle(n))
                cb.pack(side="left")
                self._vars[name] = (cb, var)
                if self._on_run:
                    # 副本名做成按钮胶囊（勾选框只管勾选；点胶囊=只刷那一个副本）
                    chip = ctk.CTkFrame(cell, fg_color=T.BTN, corner_radius=T.RADIUS_SM)
                    name_lbl = ctk.CTkLabel(chip, text=c.title, font=self.fonts["body"],
                                            text_color=T.TEXT, cursor="hand2", anchor="w")
                    name_lbl.grid(row=0, column=0, padx=8, pady=2)
                    chip.pack(side="left", padx=(4, 0))
                    name_lbl.bind("<Button-1>", lambda e, n=name: self._on_run(n))
                    name_lbl.bind("<Enter>", lambda e, ch=chip, l=name_lbl:
                                  (ch.configure(fg_color=T.ACCENT),
                                   l.configure(text_color=T.ON_ACCENT)))
                    name_lbl.bind("<Leave>", lambda e, ch=chip, l=name_lbl:
                                  (ch.configure(fg_color=T.BTN),
                                   l.configure(text_color=T.TEXT)))
                    Tooltip(name_lbl, f"「{c.title}」· 点它=只刷这一个副本"
                                      "（一次性、不落盘）。", self.fonts)
        if self._collapsible and not self._open:
            self._apply_open(notify=False)

    def toggle(self, notify=True):
        """展开/收起勾选区（仅 collapsible 有效），状态经 on_open_change 通知宿主。"""
        if not self._collapsible:
            return
        self._open = not self._open
        self._apply_open(notify)

    def _apply_open(self, notify):
        for r, f in self._cat_rows:
            if self._open:
                f.grid(row=r, column=0, sticky="ew", pady=(0, 4))
            else:
                f.grid_remove()
        if self.chevron is not None:
            self.chevron.configure(text="▾" if self._open else "▸")
        if notify and self._on_open_change:
            self._on_open_change(self._open)

    # ------------------------------------------------------------------
    # 选中同步（唯一真源 = tasks.dungeon.selected）
    # ------------------------------------------------------------------
    def _load_selection(self):
        hub = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        sel = hub.get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if isinstance(sel, list):
            sel = [n for n in self.display_names if n in sel]
        else:
            sel = []
        if not sel:
            sel = list(self.display_names[:1])   # 空则默认第一个（与刷副本页 refresh 一致）
        return sel

    def sync(self):
        """从配置重读选中并回填勾选态（不触 on_change）。返回当前选中列表。"""
        sel = self._load_selection()
        self._selected = sel
        for name, (cb, var) in self._vars.items():
            var.set(name in sel)
        return list(sel)

    @property
    def selected(self):
        return list(self._selected)

    def _on_toggle(self, name):
        var = self._vars[name][1]
        cfg = cfg_mod.load_config()
        hub = cfg_mod.task_config(cfg, self.TASK_NAME)
        sel = hub.get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if not isinstance(sel, list):
            sel = []
        sel = [n for n in self.display_names if n in sel]
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
        if self._on_change:
            self._on_change(name, bool(var.get()), list(sel))