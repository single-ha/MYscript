# -*- coding: utf-8 -*-
"""「日常流程控制」列表中的行控件。

FlowItem（基类）：序号 badge、可点标题 chip、就绪状态胶囊，以及整行拖拽
绑定；子类只填差异部分（右侧控件、就绪口径、标题点击行为）。
  · TaskFlowItem：日常任务行（含 task="" 的空白占位）——
    普通任务行右侧 ⚙ 配置 / ＋ / ✕ / 开关；开关只做「启用/停用」（停用行保留在流程里、
    引擎按 step.enabled 跳过且不补回，可随时再开），✕ 才把任务彻底移出流程；
    空白占位行右侧 ＋ / ✕（无配置、无开关）。就绪=停用/标定状态，点标题=更改任务
    （换成另一个任务或组队/解散集体动作）；
  · FlowControlItem：定时/整理/关机这类流程控制行（也是 steps 里的伪步骤）——
    右侧 ＋/✕ + 一个启停开关，「定时」行额外带一个 HH:MM 时刻输入框（存 step.time，
    关闭开关也会记住时刻）；点标题同样可改成其它任务/流程；就绪态=开启/停用，
    不参与执行序号，随整条日常按固定逻辑生效。
弹窗（DailyFlowPage）只负责 steps 数据、拖拽排序与存盘，不关心行内控件怎么搭。
"""

import customtkinter as ctk

from .. import theme as T
from ..common import Tooltip


class FlowItem(ctk.CTkFrame):
    """流程列表中一行的公共骨架。"""

    kind = "item"

    def __init__(self, master, page, key, label, enabled=True):
        super().__init__(master, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
        self.page = page
        self.key = key
        self.label_text = label
        self.enabled = bool(enabled)

        self.grid_columnconfigure(2, weight=1)
        self.grid_columnconfigure(3, weight=0)
        self.grid_columnconfigure(4, weight=0)
        self._build_badge()
        self._build_title_area()
        self._build_extra()
        self.refresh_ready()

    # ------------------------------------------------------------ 公共骨架
    def _bind_drag(self, widget):
        widget.bind("<Button-1>", lambda e, it=self: self.page._drag_start(e, it))
        widget.bind("<B1-Motion>", self.page._drag_motion)
        widget.bind("<ButtonRelease-1>", self.page._drag_end)

    def _build_badge(self):
        self.badge = ctk.CTkLabel(self, text="·", font=self.page.fonts["body_b"],
                                  width=26, height=26, corner_radius=13,
                                  text_color=T.TEXT_DIM, fg_color=T.SURFACE)
        self.badge.grid(row=0, column=1, rowspan=2, padx=(12, 12), pady=5)

    def _build_title_area(self):
        mid = ctk.CTkFrame(self, fg_color="transparent")
        mid.grid(row=0, column=2, rowspan=2, sticky="ew", pady=5)
        mid.grid_columnconfigure(0, weight=0)
        mid.grid_columnconfigure(1, weight=1)
        # 整行空白处与标题区都可抓拖（按钮/开关在 page._drag_start 里被类型过滤）
        self._bind_drag(mid)
        self._bind_drag(self)

        chip = ctk.CTkFrame(mid, fg_color=T.BTN, corner_radius=T.RADIUS_SM)
        title = ctk.CTkLabel(chip, text=self.chip_text(), font=self.page.fonts["body_b"],
                             text_color=T.TEXT, cursor="hand2", anchor="w")
        title.grid(row=0, column=0, padx=12, pady=4)
        chip.grid(row=0, column=0, sticky="w")
        title._idle_chip = T.BTN     # 悬停还原基准色（子类按启用/停用可改）
        title._idle_txt = T.TEXT
        title.bind("<Enter>",
                   lambda e: (chip.configure(fg_color=T.ACCENT),
                              title.configure(text_color=T.ON_ACCENT)))
        title.bind("<Leave>",
                   lambda e: (chip.configure(fg_color=getattr(title, "_idle_chip", T.BTN)),
                              title.configure(text_color=getattr(title, "_idle_txt", T.TEXT))))
        title.bind("<Button-1>", lambda e: self.on_title_click(), add="+")
        Tooltip(title, self.title_tooltip(), self.page.fonts)
        self.chip = chip
        self.title = title

        self.ready = ctk.CTkButton(mid, text="", font=self.page.fonts["small"], height=24,
                                   corner_radius=T.RADIUS_SM, border_width=0)
        self.ready.grid(row=0, column=1, sticky="e", padx=(10, 0))

    # ------------------------------------------------------------ 子类钩子
    def _build_extra(self):
        """右侧差异控件（按钮/开关），子类覆写。"""

    def ready_meta(self):
        """返回 (状态文字, 颜色)，子类必须覆写。"""
        return "", T.TEXT_DIM

    def title_tooltip(self):
        return ""

    def chip_text(self):
        """标题 chip 上显示的文本；默认同 label_text。执行序列摘要等仍用 label_text。"""
        return self.label_text

    def on_title_click(self):
        pass

    def runs_now(self):
        """该行是否计入执行序号/执行序列（流程控制行恒为 False）。"""
        return False

    # ------------------------------------------------------------ 公共行为
    def refresh_ready(self):
        text, color = self.ready_meta()
        self.ready.configure(text=text, text_color=color, fg_color="transparent",
                             hover_color=T.SURFACE_2, border_width=0,
                             cursor="arrow", command=None)

    def set_number(self, n):
        self.badge.configure(text=str(n), fg_color=T.ACCENT, text_color=T.ON_ACCENT)

    def clear_number(self):
        self.badge.configure(text="·", fg_color=T.SURFACE, text_color=T.TEXT_DIM)

    def set_drag_highlight(self, on):
        self.configure(fg_color=T.BTN if on else T.SURFACE_2)


class TaskFlowItem(FlowItem):
    """日常任务行：task="" 时为空白占位。"""

    kind = "task"

    def __init__(self, master, page, key, step):
        self.step = step
        self.name = step.get("task", "")
        super().__init__(master, page, key,
                         page._task_display_name(self.name),
                         enabled=bool(step.get("enabled", True)))
        self._apply_enabled_look()

    def _apply_enabled_look(self):
        """停用任务：标题胶囊置灰；启用：正常配色。就绪胶囊同步刷新。"""
        on = bool(self.step.get("enabled", True)) if self.name else True
        chip_bg, txt = (T.BTN, T.TEXT) if on else (T.SURFACE, T.TEXT_DIM)
        self.chip.configure(fg_color=chip_bg)
        self.title.configure(text_color=txt)
        self.title._idle_chip = chip_bg
        self.title._idle_txt = txt
        self.refresh_ready()

    def runs_now(self):
        # 空白占位不参与执行；停用（开关关）的任务保留在流程里但不计入执行序号
        return bool(self.step.get("enabled", True)) and bool(self.name)

    def chip_text(self):
        # 🔁 仅作「点此更改任务」的视觉提示；执行序列摘要仍读不含图标的 label_text。
        return self.label_text + " 🔁"

    def ready_meta(self):
        if not self.name:
            return "空白占位", T.TEXT_DIM
        if not bool(self.step.get("enabled", True)):
            return "已停用", T.TEXT_DIM
        if not self.page._task_status(self.name):
            return "⚠ 还需标定", T.WARN
        return "✓ 已就绪", T.SUCCESS

    def title_tooltip(self):
        if not self.name:
            return "空白占位：点任务名从支持的日常任务/集体动作里选一个填进来。"
        return "点任务名 = 更改任务（换成另一个任务或组队/解散）"

    def on_title_click(self):
        self.page._change_task(self.key)

    def _build_extra(self):
        if self.name:
            # 普通任务/组队解散行：⚙ 配置 / ＋ 插行 / ✕ 移除 / 开关
            cfg_btn = ctk.CTkButton(self, text="⚙ 配置", font=self.page.fonts["small"],
                                    height=24, corner_radius=T.RADIUS_SM, border_width=0,
                                    fg_color=T.BTN, text_color=T.TEXT, cursor="hand2",
                                    command=self.page._open_task_config_for(self.name))
            cfg_btn.grid(row=0, column=3, rowspan=2, padx=(4, 6), pady=5)
            Tooltip(cfg_btn, "打开该任务在「任务配置」页的配置卡（标定 / 参数）。",
                    self.page.fonts)

            add_btn = ctk.CTkButton(self, text="＋", font=self.page.fonts["body_b"],
                                    height=24, width=30, corner_radius=T.RADIUS_SM,
                                    border_width=0, fg_color=T.BTN, text_color=T.TEXT,
                                    cursor="hand2",
                                    command=lambda k=self.key: self.page._add_below(k))
            add_btn.grid(row=0, column=4, rowspan=2, padx=(4, 0), pady=5)
            Tooltip(add_btn, "在该行下方插入一个空白占位，之后点占位标题选定任务。",
                    self.page.fonts)

            del_btn = ctk.CTkButton(self, text="✕", font=self.page.fonts["body_b"],
                                    height=24, width=30, corner_radius=T.RADIUS_SM,
                                    border_width=0, fg_color=T.BTN, text_color=T.DANGER,
                                    cursor="hand2",
                                    command=lambda k=self.key: self.page._remove_task(k))
            del_btn.grid(row=0, column=5, rowspan=2, padx=(4, 0), pady=5)
            Tooltip(del_btn, "把这个任务从流程中移除（引擎也不会再自动补回）。",
                    self.page.fonts)

            self.var = ctk.BooleanVar(value=bool(self.step.get("enabled", True)))
            sw = ctk.CTkSwitch(self, text="", variable=self.var, width=44,
                               progress_color=T.ACCENT, fg_color=T.BTN,
                               button_color=T.ON_ACCENT, command=self._on_toggle)
            sw.grid(row=0, column=6, rowspan=2, padx=(8, 14), pady=5)
            self.switch = sw
            Tooltip(sw, "关掉 = 该任务暂不执行（行保留，引擎跳过、不补回）；\n"
                        "再打开即恢复执行。要彻底从流程里删除用 ✕。", self.page.fonts)
        else:
            # 空白占位：没有配置卡；＋ 插行、✕ 删除占位
            add_btn = ctk.CTkButton(self, text="＋", font=self.page.fonts["body_b"],
                                    height=24, width=30, corner_radius=T.RADIUS_SM,
                                    border_width=0, fg_color=T.BTN, text_color=T.TEXT,
                                    cursor="hand2",
                                    command=lambda k=self.key: self.page._add_below(k))
            add_btn.grid(row=0, column=4, rowspan=2, padx=(4, 0), pady=5)
            Tooltip(add_btn, "在该空白占位下方再插入一个空白占位。", self.page.fonts)

            del_btn = ctk.CTkButton(self, text="✕", font=self.page.fonts["body_b"],
                                    height=24, width=30, corner_radius=T.RADIUS_SM,
                                    border_width=0, fg_color=T.BTN, text_color=T.DANGER,
                                    cursor="hand2",
                                    command=lambda k=self.key: self.page._remove_task(k))
            del_btn.grid(row=0, column=5, rowspan=2, padx=(4, 14), pady=5)
            Tooltip(del_btn, "删除这个空白占位。", self.page.fonts)

    def _on_toggle(self):
        # 开关只做「启用/停用」：停用的任务行仍保留在流程里（引擎按 step.enabled 跳过、不补回），
        # 可随时再开；要彻底删除用 ✕。
        on = bool(self.var.get())
        self.enabled = on
        self.step["enabled"] = on
        self._apply_enabled_look()
        self.page.on_task_toggle(self.key, on)


class FlowControlItem(FlowItem):
    """流程控制行：定时/整理/关机，已纳入统一 steps（伪步骤）。

    与任务行一样可改标题（换成其它任务/流程）、＋ 插行、✕ 移出、可拖拽；
    右侧保留开关决定该项是否生效。runs_now 恒 False 只用于「任务行是否会跑」的门控判定；
    行前执行序号由 DailyFlowPage._renumber 单独按引擎真实顺序给（定时=1、关机=末号、整理按位置）。
    「自动整理背包」可在流程里放多个：每个都在各自被拖到的位置、由各号走到时整理一次。"""

    kind = "flow"

    _TIPS = {
        "timing": "流程控制：开启后整条日常等到设定时刻才启动（固定在链开头生效），整条链只能有一个。",
        "shutdown": "流程控制：整条日常跑完后自动关机（固定在链结尾生效），整条链只能有一个。",
        "organize": "流程控制：各号走到这一步时就地整理一次背包（按你拖到的位置执行，不是任务交接点）；\n"
                    "整条流程里可以放多个。需在「工具-整理背包」标定「背包满图标」，背包没满会静默跳过。",
    }

    def __init__(self, master, page, key, step):
        self.step = step
        self.name = step.get("task", "")
        super().__init__(master, page, key,
                         page._task_display_name(self.name),
                         enabled=bool(step.get("enabled", False)))

    def runs_now(self):
        # 恒 False：流程行不参与「任务行是否会跑」的门控/任务项判定；
        # 其执行序号由 DailyFlowPage._renumber 按引擎真实顺序（定时=1、关机=末号、整理按位置）单独给。
        return False

    def ready_meta(self):
        if self.enabled:
            return "开启", T.SUCCESS
        return "停用", T.TEXT_DIM

    def title_tooltip(self):
        base = self._TIPS.get(self.name,
                              "流程控制行：不单独执行，随整条日常按固定逻辑生效。")
        return base + "\n点标题可换成其它任务/流程；右侧开关开/关，✕ 可移出流程。"

    def on_title_click(self):
        # 点标题 = 更改任务（与任务行一致）；开关只能用右侧那个拨。
        self.page._change_task(self.key)

    def chip_text(self):
        # 与任务行一致：标题后缀 🔁 表示「点此更改」；label_text 仍不含图标。
        return self.label_text + " 🔁"

    def _build_extra(self):
        if self.name == "timing":
            # 定时行：时刻输入框(HH:MM) / ＋ / ✕ / 开关
            ctk.CTkLabel(self, text="时刻", font=self.page.fonts["small"],
                         text_color=T.TEXT_DIM).grid(row=0, column=3, rowspan=2,
                                                     padx=(4, 4), pady=5)
            self.time_var = ctk.StringVar(value=str(self.step.get("time") or "10:00"))
            ent = ctk.CTkEntry(self, textvariable=self.time_var, width=64,
                               font=self.page.fonts["body"], justify="center",
                               fg_color=T.SURFACE_2, border_color=T.BORDER)
            ent.grid(row=0, column=4, rowspan=2, padx=(0, 4), pady=5)
            ent.bind("<FocusOut>", lambda e: self._commit_time())
            ent.bind("<Return>", lambda e: self._commit_time())
            Tooltip(ent, "24 小时制 HH:MM，例如 10:00 或 22:30。\n"
                         "失焦/回车即保存；开关关闭时该时刻仍会记住，开启后生效。\n"
                         "整条日常在到该时刻后才启动（固定在链开头生效）。",
                    self.page.fonts)
            self.time_entry = ent

            add_col, del_col, sw_col = 5, 6, 7
        else:
            self.time_var = None
            self.time_entry = None
            add_col, del_col, sw_col = 3, 4, 5

        add_btn = ctk.CTkButton(self, text="＋", font=self.page.fonts["body_b"],
                                height=24, width=30, corner_radius=T.RADIUS_SM,
                                border_width=0, fg_color=T.BTN, text_color=T.TEXT,
                                cursor="hand2",
                                command=lambda k=self.key: self.page._add_below(k))
        add_btn.grid(row=0, column=add_col, rowspan=2, padx=(4, 0), pady=5)
        Tooltip(add_btn, "在该行下方插入一个空白占位，之后点占位标题选定任务。",
                self.page.fonts)

        del_btn = ctk.CTkButton(self, text="✕", font=self.page.fonts["body_b"],
                                height=24, width=30, corner_radius=T.RADIUS_SM,
                                border_width=0, fg_color=T.BTN, text_color=T.DANGER,
                                cursor="hand2",
                                command=lambda k=self.key: self.page._remove_task(k))
        del_btn.grid(row=0, column=del_col, rowspan=2, padx=(4, 0), pady=5)
        Tooltip(del_btn, "把这个流程控制移出列表（=停用；可用 ＋→更改 再加回）。",
                self.page.fonts)

        self.var = ctk.BooleanVar(value=self.enabled)
        sw = ctk.CTkSwitch(self, text="", variable=self.var, width=44,
                           progress_color=T.ACCENT, fg_color=T.BTN,
                           button_color=T.ON_ACCENT, command=self._on_switch)
        sw.grid(row=0, column=sw_col, rowspan=2, padx=(8, 14), pady=5)
        self.switch = sw

    @staticmethod
    def _norm_time(raw):
        """把输入规整成合法 HH:MM；非法返回 None。"""
        if not raw:
            return None
        raw = raw.strip().replace("：", ":")
        if ":" not in raw:
            return None
        try:
            hh, mm = raw.split(":")[:2]
            hh, mm = int(hh), int(mm)
        except (ValueError, TypeError):
            return None
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return f"{hh:02d}:{mm:02d}"

    def _commit_time(self):
        norm = self._norm_time(self.time_var.get())
        if norm is None:
            # 非法输入：回退为已存时刻（默认 10:00），不落任何错误值
            norm = str(self.step.get("time") or "10:00")
        self.time_var.set(norm)
        if self.step.get("time") != norm:
            self.step["time"] = norm
            self.page.on_flow_toggle(self.name, self.enabled)
            self.refresh_ready()

    def _on_switch(self):
        self.enabled = bool(self.var.get())
        self.step["enabled"] = self.enabled
        self.page.on_flow_toggle(self.name, self.enabled)
        self.refresh_ready()
