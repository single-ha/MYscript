# -*- coding: utf-8 -*-
"""设置页：基础项 + 「速度与节奏（手速）」一组滑块。独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ..common import (
    Card, bind_wraplength,
    HOTKEY_NAMES, FAILSAFE_CORNERS, FAILSAFE_LABELS, FAILSAFE_VALUE_OF,
    DEFAULT_STOP_HOTKEY, DEFAULT_FAILSAFE,
    _split_stop_hotkey, _compose_stop_hotkey,
)


# 「速度与节奏」滑块里 loop 那组落在秒装备命名空间；避免再 import SnperPage（跨页引用已移除）。
_SNIPER_TASK = "sniper"


class SettingsPage(ctk.CTkFrame):
    """设置页：基础项 + 「速度与节奏（手速）」一组滑块，全部可在界面里调。"""

    # 速度/节奏滑块定义：(存储位置, 键, 标签, 下限, 上限, 步数, 小数位, 说明)
    #   loc: "humanize" 存到 cfg["humanize"]；"loop" 存到 tasks.sniper.loop
    SPEED_FIELDS = [
        ("humanize", "speed", "整体速度倍率", 0.5, 3.0, 25, 2,
         "总开关：越大鼠标移动/点击越快(按比例缩短拟人化延迟)。想抢得快先调它。1.0=原速。"),
        ("humanize", "snipe_speed", "命中下单极速倍率", 1.0, 6.0, 25, 1,
         "命中后「下单那一下」的额外提速：只在抢的瞬间生效，巡航不受影响。越大越抢得到、也越不像人。建议 3~5。"),
        ("humanize", "px_per_step", "鼠标移动步长(px)", 6, 40, 34, 0,
         "每步移动的像素。越大步数越少→移动越快，但轨迹越不平滑(略更像机器)。"),
        ("loop", "shelf_load_wait_sec", "货架加载最长等待(秒)", 0.2, 3.0, 28, 2,
         "等货架刷出的上限/超时。自适应：画面一静止就提前识别，不会傻等满。只有慢机/慢网才需调大。"),
        ("loop", "shelf_load_min_sec", "货架加载最短等待(秒)", 0.0, 1.5, 30, 2,
         "再快也至少等这么久给画面起步。太小可能没开始加载就截图、偶发漏识别，那就调大一点。"),
        ("loop", "refresh_interval_sec", "两轮间隔(秒)", 0.0, 3.0, 30, 2,
         "两轮重进货架之间的停顿(带抖动)。想最快就调到接近 0，但完全无间隔更像机器。"),
        ("loop", "after_buy_cooldown_sec", "购买后冷却(秒)", 0.3, 5.0, 47, 2,
         "命中下单后的等待。给购买弹窗收尾用，太小可能下一轮误点。"),
        ("humanize", "idle_chance", "走神概率", 0.0, 0.10, 20, 3,
         "每轮随机“发呆”的概率，越像真人但会拖慢节奏。专心抢货时设 0。"),
        ("humanize", "click_radius", "落点随机半径(px)", 0, 12, 12, 0,
         "点击落点在目标周围随机偏移的范围。0=每次点正中心(更准但更机械)。"),
        ("humanize", "interval_jitter", "间隔抖动比例", 0.0, 0.8, 16, 2,
         "各种等待时间的随机浮动幅度。越大越不规律(更像人)，越小越稳定。"),
    ]

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(self, text="设置", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=4, pady=(2, 14))

        # 分页签：基础 / 速度与节奏（用户拍板 2026-09-20 改为页签展示）。
        self._value_labels = {}   # key -> 数值显示 Label
        self.speed_vars = {}      # key -> DoubleVar

        self.tabs = ctk.CTkTabview(
            self, fg_color="transparent", anchor="w",
            segmented_button_fg_color=T.SURFACE_2,
            segmented_button_selected_color=T.ACCENT,
            segmented_button_selected_hover_color=T.ACCENT_HOVER,
            segmented_button_unselected_color=T.SURFACE_2,
            segmented_button_unselected_hover_color=T.SURFACE,
            text_color=T.TEXT_DIM, text_color_disabled=T.TEXT_DIM,
            command=self._paint_tab_selection)
        self.tabs.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 12))

        self._tab_scrolls = {}
        for name, builder in (("基础", self._build_basic_card),
                              ("速度与节奏", self._build_speed_card)):
            tab = self.tabs.add(name)
            tab.grid_columnconfigure(0, weight=1)
            tab.grid_rowconfigure(0, weight=1)
            scroll = ctk.CTkScrollableFrame(tab, fg_color="transparent")
            scroll.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
            scroll.grid_columnconfigure(0, weight=1)
            T.tune_scroll_speed(scroll)
            builder(scroll)
            self._tab_scrolls[name] = scroll
        # 页签按钮靠左对齐（anchor="w"）+ 等宽（用户拍板 2026-09-20）：
        # CTkSegmentedButton 不提供统一宽度参数，直接给每个 segment 设相同 width。
        try:
            sb = getattr(self.tabs, "_segmented_button", None)
            if sb is not None:
                for value, btn in (getattr(sb, "_buttons_dict", None) or {}).items():
                    btn.configure(width=112)
        except Exception:
            pass
        # 初始选中第一个 tab（CTkTabview 初建无高亮，须手动补一次字色）
        self._paint_tab_selection()

        ctk.CTkButton(self, text="保存设置", font=self.fonts["btn"], height=42, width=160,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._save).grid(
                          row=2, column=0, padx=4, pady=(2, 16), sticky="w")

    # ---- 基础卡片 ----
    def _build_basic_card(self, parent):
        card = Card(parent)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(1, weight=1)

        cfg = self.app.cfg
        self.var_title = ctk.StringVar(value=cfg.get("window_title", "梦幻西游"))
        self.var_backend = ctk.StringVar(value=cfg.get("input_backend", "sendinput"))
        sc, sa, ss, sk = _split_stop_hotkey(cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        self.var_hk_ctrl = ctk.BooleanVar(value=sc)
        self.var_hk_alt = ctk.BooleanVar(value=sa)
        self.var_hk_shift = ctk.BooleanVar(value=ss)
        self.var_hk_key = ctk.StringVar(value=sk)
        fc = cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        self.var_failsafe = ctk.StringVar(value=FAILSAFE_CORNERS.get(fc, "右上角"))
        self.var_threshold = ctk.DoubleVar(value=self._get_threshold())
        self.var_debug = ctk.BooleanVar(value=bool(cfg.get("debug_log", False)))

        ctk.CTkLabel(card, text="基础", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=16, pady=(14, 4))
        self._row(card, 1, "游戏窗口标题关键字",
                  ctk.CTkEntry(card, textvariable=self.var_title, font=self.fonts["body"],
                               fg_color=T.SURFACE_2, border_color=T.BORDER, width=240))
        self._row(card, 2, "鼠标输入后端",
                  ctk.CTkOptionMenu(card, variable=self.var_backend,
                                    values=["sendinput", "pyautogui", "pydirectinput"],
                                    font=self.fonts["body"], fg_color=T.SURFACE_2,
                                    button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT, dropdown_text_color=T.TEXT, width=240))
        self._row(card, 3, "急停 快捷键（停止一切）", self._build_hotkey(card), sticky="ew")
        self._row(card, 4, "失控急停（甩鼠标到屏幕角）", self._build_failsafe(card), sticky="ew")
        self._row(card, 5, "识别置信度（匹配阈值）", self._build_threshold(card), sticky="ew")
        self.var_debug_sw = ctk.CTkSwitch(card, text="开启（显示 debug 级日志）",
                                          variable=self.var_debug, font=self.fonts["body"],
                                          text_color=T.TEXT, fg_color=T.ACCENT,
                                          progress_color=T.ACCENT_HOVER)
        self._row(card, 6, "调试日志", self.var_debug_sw)

        # 配置迁移：导出 / 导入（zip 打包 config.json + templates/，换机/换号搬标定）——用户拍板 2026-09-20。
        box = ctk.CTkFrame(card, fg_color="transparent")
        box.grid(row=7, column=0, columnspan=2, sticky="ew", padx=16, pady=(4, 14))
        box.grid_columnconfigure(0, weight=1)
        btns = ctk.CTkFrame(box, fg_color="transparent")
        btns.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(btns, text="导出配置", font=self.fonts["body"], height=32, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._export_config).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="导入配置", font=self.fonts["body"], height=32, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._import_config).pack(side="left")
        hint = ctk.CTkLabel(box, text="导出把 config.json 和 templates/（标定的模板图）打成 zip；导入用 zip 覆盖还原，"
                                      "换机/换号搬标定就靠它。导入会覆盖当前配置。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        bind_wraplength(hint)

    def _build_hotkey(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(anchor="w")
        for txt, var in (("Ctrl", self.var_hk_ctrl), ("Alt", self.var_hk_alt), ("Shift", self.var_hk_shift)):
            ctk.CTkCheckBox(row, text=txt, variable=var, font=self.fonts["body"],
                            text_color=T.TEXT, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                            checkbox_width=20, checkbox_height=20).pack(side="left", padx=(0, 14))
        ctk.CTkOptionMenu(row, variable=self.var_hk_key, values=HOTKEY_NAMES,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT,
                          dropdown_text_color=T.TEXT, width=120).pack(side="left")
        hint = ctk.CTkLabel(box, text="全局急停：游戏在前台也能按，按一下立刻停止所有正在跑的任务（鼠标被脚本"
                                      "拉着失控时随时叫停）。默认 Ctrl+Alt+F12；建议带修饰键，避免和游戏内按键误撞。改完记得保存。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
        return box

    def _build_failsafe(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        ctk.CTkOptionMenu(box, variable=self.var_failsafe, values=FAILSAFE_LABELS,
                          font=self.fonts["body"], fg_color=T.SURFACE_2,
                          button_color=T.BORDER, button_hover_color=T.ACCENT, text_color=T.TEXT,
                          dropdown_text_color=T.TEXT, width=160).pack(anchor="w")
        hint = ctk.CTkLabel(box, text="任务运行时，把鼠标猛甩到所选屏幕角（撞到角落）立刻急停。独立于鼠标后端、"
                                      "始终生效。选「关闭」则只靠停止按钮 / 急停热键。改完记得保存。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
        return box

    # ---- 速度与节奏卡片 ----
    def _build_speed_card(self, parent):
        card = Card(parent)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 14))
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        ctk.CTkLabel(head, text="速度与节奏（手速）", font=self.fonts["h2"],
                     text_color=T.TEXT).pack(anchor="w")
        warn = ctk.CTkLabel(head, text="抢不过别人就往「快」调；但越快越规律越像机器、封号风险越高。先用小号试。",
                     font=self.fonts["small"], text_color=T.WARN, justify="left")
        warn.pack(fill="x", pady=(2, 0))
        bind_wraplength(warn)

        for i, (loc, key, label, lo, hi, steps, dec, hint) in enumerate(self.SPEED_FIELDS):
            self._slider_row(card, i + 1, loc, key, label, lo, hi, steps, dec, hint)

    def _slider_row(self, parent, row, loc, key, label, lo, hi, steps, dec, hint):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", padx=16, pady=(10, 6))
        box.grid_columnconfigure(1, weight=1)

        # 第一行：标签 + 滑块 + 数值
        ctk.CTkLabel(box, text=label, font=self.fonts["body_b"], text_color=T.TEXT,
                     width=150, anchor="w").grid(row=0, column=0, sticky="w")

        var = ctk.DoubleVar(value=self._get_value(loc, key))
        self.speed_vars[key] = var
        fmt = f"{{:.{dec}f}}"
        val_lbl = ctk.CTkLabel(box, text=fmt.format(var.get()), font=self.fonts["body_b"],
                               text_color=T.ACCENT, width=56, anchor="e")
        val_lbl.grid(row=0, column=2, sticky="e", padx=(8, 0))
        self._value_labels[key] = (val_lbl, fmt)

        slider = ctk.CTkSlider(box, from_=lo, to=hi, number_of_steps=steps, variable=var,
                               command=lambda v, k=key: self._on_slider(k, v),
                               progress_color=T.ACCENT, button_color=T.ACCENT,
                               button_hover_color=T.ACCENT_HOVER)
        slider.grid(row=0, column=1, sticky="ew", padx=10)

        # 第二行：说明文字（整行单独占一行，不再和滑块重叠）
        hint_lbl = ctk.CTkLabel(box, text=hint, font=self.fonts["small"], text_color=T.TEXT_DIM,
                     justify="left")
        hint_lbl.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        bind_wraplength(hint_lbl)

    def _on_slider(self, key, val):
        lbl, fmt = self._value_labels[key]
        lbl.configure(text=fmt.format(float(val)))

    def _paint_tab_selection(self, _=None):
        """选中页签白字（T.ON_ACCENT），未选中回到 TEXT_DIM。
        CTkSegmentedButton 选中态只变背景不变字色，这里手动补字色（同 category.py 先例）。"""
        cur = self.tabs.get()
        bd = getattr(self.tabs._segmented_button, "_buttons_dict", None)
        if not bd:
            return
        for value, btn in bd.items():
            try:
                btn.configure(text_color=T.ON_ACCENT if value == cur else T.TEXT_DIM)
            except Exception:
                pass

    # ---- 取值/存值助手 ----
    def _get_value(self, loc, key):
        if loc == "humanize":
            src = self.app.cfg.get("humanize", {})
            default = cfg_mod.DEFAULT_CONFIG["humanize"].get(key, 0)
        else:  # loop
            src = cfg_mod.task_config(self.app.cfg, _SNIPER_TASK).get("loop", {})
            default = cfg_mod.DEFAULT_CONFIG["tasks"]["sniper"]["loop"].get(key, 0)
        try:
            v = src.get(key, default)
            return float(default if v is None else v)
        except (TypeError, ValueError):
            return float(default)

    def _row(self, parent, r, label, widget, sticky="w"):
        ctk.CTkLabel(parent, text=label, font=self.fonts["body"], text_color=T.TEXT).grid(
            row=r, column=0, sticky="w", padx=16, pady=12)
        widget.grid(row=r, column=1, sticky=sticky, padx=16, pady=12)

    # ---- 置信度滑块 ----
    def _get_threshold(self):
        tc = cfg_mod.task_config(self.app.cfg, _SNIPER_TASK)
        try:
            return float(tc.get("loop", {}).get("match_threshold", 0.85))
        except (TypeError, ValueError):
            return 0.85

    def _build_threshold(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.pack(anchor="w", fill="x")
        slider = ctk.CTkSlider(top, from_=0.60, to=0.99, number_of_steps=39, width=240,
                               variable=self.var_threshold, command=self._on_threshold,
                               progress_color=T.ACCENT, button_color=T.ACCENT,
                               button_hover_color=T.ACCENT_HOVER)
        slider.pack(side="left")
        self.thr_value = ctk.CTkLabel(top, text=f"{self.var_threshold.get():.2f}",
                                      font=self.fonts["body_b"], text_color=T.ACCENT, width=48)
        self.thr_value.pack(side="left", padx=(12, 0))
        hint = ctk.CTkLabel(box, text="越高越严格：命中更准但可能漏；越低越宽松：易命中但可能误认。建议 0.85~0.92。",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        hint.pack(fill="x", pady=(4, 0))
        bind_wraplength(hint)
        return box

    def _on_threshold(self, val):
        self.thr_value.configure(text=f"{float(val):.2f}")

    # ---- 配置迁移：导出 / 导入（整体备份 config.json + templates/，换机/换号搬标定）----
    def _export_config(self):
        from tkinter import filedialog
        import zipfile
        path = filedialog.asksaveasfilename(
            title="导出配置", defaultextension=".zip",
            initialfile="mhxy_config.zip",
            filetypes=[("压缩包", "*.zip")])
        if not path:
            return
        cfg_file = cfg_mod.DATA_ROOT / "config.json"
        if not cfg_file.exists():
            self.app.toast("没有可导出的配置（config.json 不存在）。", T.WARN)
            return
        try:
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(str(cfg_file), arcname="config.json")
                tpl_dir = cfg_mod.DATA_ROOT / "templates"
                if tpl_dir.is_dir():
                    for p in sorted(tpl_dir.rglob("*")):
                        if p.is_file():
                            zf.write(str(p), arcname="templates/" + p.relative_to(tpl_dir).as_posix())
        except Exception as e:
            self.app.toast(f"导出配置失败：{e}", T.DANGER)
            return
        self.app.toast(f"已导出配置 → {path}", T.SUCCESS)

    def _import_config(self):
        from tkinter import filedialog, messagebox
        import json, zipfile
        path = filedialog.askopenfilename(title="导入配置", filetypes=[("压缩包", "*.zip")])
        if not path:
            return
        ok, detail = self._probe_config_zip(path)
        if not ok:
            self.app.toast("选中的文件不是配置文件", T.WARN)
            return
        if not messagebox.askyesno("导入配置",
                                   f"该压缩包是工具配置包：{detail}\n导入会用它覆盖当前 config.json 与标定模板，是否继续？"):
            return
        try:
            with zipfile.ZipFile(path, "r") as zf:
                self._extract_safely(zf, cfg_mod.DATA_ROOT)
        except Exception as e:
            self.app.toast(f"导入配置失败：{e}", T.DANGER)
            return
        self.app.cfg = cfg_mod.load_config()
        try:
            self.refresh()
        except Exception:
            pass
        self.app.toast("已导入配置（其它页面切过去会自动刷新）", T.SUCCESS)

    def _probe_config_zip(self, path):
        """校验 zip 是不是本工具配置文件包：内含 config.json 且 JSON 为 dict 且带 tasks 键。
        返回 (True, 描述) 或 (False, 原因)。""" 
        import json, zipfile
        try:
            with zipfile.ZipFile(path, "r") as zf:
                names = [n for n in zf.namelist() if n.split("/")[-1] == "config.json"]
                for name in names:
                    try:
                        data = json.loads(zf.read(name).decode("utf-8"))
                    except Exception:
                        continue
                    if isinstance(data, dict) and (isinstance(data.get("tasks"), dict)
                                                   or "targets" in data):
                        has_tpl = any(n.startswith("templates/") for n in zf.namelist())
                        return True, ("config.json" + (" + templates/" if has_tpl else ""))
                return False, "无本工具 config.json"
        except Exception as e:
            return False, str(e)

    def _extract_safely(self, zf, dest):
        """把 zip 内容安全解压到 dest（过滤路径穿越，防 zip slip）。"""
        import shutil
        for info in zf.infolist():
            rel = info.filename.replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/"):
                continue
            target = dest / rel
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def _save(self):
        # 重新读盘再改，避免覆盖掉标定向导刚写入的 regions/watchlist。
        cfg = cfg_mod.load_config()
        cfg["window_title"] = self.var_title.get().strip() or "梦幻西游"
        cfg["input_backend"] = self.var_backend.get()
        cfg["hotkey_stop"] = _compose_stop_hotkey(
            self.var_hk_ctrl.get(), self.var_hk_alt.get(),
            self.var_hk_shift.get(), self.var_hk_key.get())
        cfg["failsafe_corner"] = FAILSAFE_VALUE_OF.get(self.var_failsafe.get(), DEFAULT_FAILSAFE)
        cfg["debug_log"] = bool(self.var_debug.get())

        hz = cfg.setdefault("humanize", {})
        tc = cfg_mod.task_config(cfg, _SNIPER_TASK)
        loop = tc.setdefault("loop", {})
        loop["match_threshold"] = round(float(self.var_threshold.get()), 2)

        # 速度/节奏滑块：按来源写回 humanize 或 loop；px_per_step 存整数
        for loc, key, *_ in self.SPEED_FIELDS:
            val = float(self.speed_vars[key].get())
            if key in ("px_per_step", "click_radius"):
                val = int(round(val))
            else:
                val = round(val, 3)
            (hz if loc == "humanize" else loop)[key] = val

        cfg_mod.set_task_config(cfg, _SNIPER_TASK, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast("设置已保存")

    def refresh(self):
        self.var_title.set(self.app.cfg.get("window_title", "梦幻西游"))
        self.var_backend.set(self.app.cfg.get("input_backend", "sendinput"))
        sc, sa, ss, sk = _split_stop_hotkey(self.app.cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        self.var_hk_ctrl.set(sc)
        self.var_hk_alt.set(sa)
        self.var_hk_shift.set(ss)
        self.var_hk_key.set(sk)
        fc = self.app.cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        self.var_failsafe.set(FAILSAFE_CORNERS.get(fc, "右上角"))
        self.var_debug.set(bool(self.app.cfg.get("debug_log", False)))
        thr = self._get_threshold()
        self.var_threshold.set(thr)
        if hasattr(self, "thr_value"):
            self.thr_value.configure(text=f"{thr:.2f}")
        for loc, key, *_ in self.SPEED_FIELDS:
            if key in self.speed_vars:
                v = self._get_value(loc, key)
                self.speed_vars[key].set(v)
                lbl, fmt = self._value_labels[key]
                lbl.configure(text=fmt.format(v))
