# -*- coding: utf-8 -*-
"""登录游戏「配置/标定」卡：客户端路径 + 参数改动即保存，标定/重新加载即点即做。
只管「流程模板 + 账号列表区」这几样；账号本身在「日常」页顶部的「账号库」弹窗里标定与挑选
（ui/account_gallery.py）。运行唯一入口也在那儿（挑好号 → 点开始）。由 ConfigPage 统一组装。"""

import os

import customtkinter as ctk

from .. import theme as T
from ..account_gallery import AccountGallery
from ...core import account_history as ah
from ...core import config as cfg_mod
from ...core.config import (LOGIN_FLOW_TPL_KEYS, LOGIN_MAX_ACCOUNTS, LOGIN_REQUIRED_REGION,
                            SHARED_REGION_KEYS, SHARED_TPL_KEYS)
from ..common import (Card, Tooltip, bind_wraplength, open_calibrate)


class LoginConfig(ctk.CTkFrame):
    TASK_NAME = "login"
    LOG_SOURCE = "登录"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cal_dialog = None

        card = Card(self)
        card.pack(fill="x", padx=2)
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        head.grid_columnconfigure(0, weight=1)
        ttl = ctk.CTkLabel(head, text="登录游戏", font=self.fonts["h2"], text_color=T.TEXT, anchor="w")
        ttl.grid(row=0, column=0, sticky="w")
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        # 说明文字单独占一整行、跨两列（columnspan=2）：与标题/按钮同一行时，3 个按钮会把文字列
        # 挤到只剩几十像素、说明被压成左边一条细柱（bind_wraplength 越换越窄）。同约束 8。
        sub = ctk.CTkLabel(head, text="逐号启动客户端：切换账号→翻找账号卡并点→进入游戏→更换角色→"
                                     "选「已有角色」角色卡→进主界面；账号卡片+角色卡片都在「账号库」里标定与"
                                     "挑选（最多 %d 个，挑 %d 个去登录）。"
                                     % (ah.MAX_SLOTS, LOGIN_MAX_ACCOUNTS),
                         font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        bind_wraplength(sub)
        ctk.CTkButton(btns, text="账号库…", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_gallery).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="标定", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_calibrate).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left")
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(row=1, column=0, sticky="ew",
                                                              padx=16, pady=(0, 2))

        self.lbl_calib = ctk.CTkLabel(card, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      justify="left")
        self.lbl_calib.grid(row=2, column=0, sticky="ew", padx=16, pady=(8, 0))
        bind_wraplength(self.lbl_calib)

        # 客户端路径（带「浏览…」按钮）+ 启动参数
        self._path_row(card, 3)
        self._args_row(card, 4)

        # 四个等待/阈值参数
        self.var_launch = self._num_row(
            card, 5, "等客户端窗口出现（秒）", "client_launch_timeout_sec", 180,
            "启动客户端后等它的窗口弹出来的上限。多开/机器慢就调大。")
        self.var_step = self._num_row(
            card, 6, "单步等待（秒）", "step_timeout_sec", 30,
            "等「用户/切换账号/选择账号/登录」这些按钮出现的单步超时，超了当前号跳过、其余号继续。")
        self.var_enter = self._num_row(
            card, 7, "等进入游戏（秒）", "enter_game_wait_sec", 300,
            "点完「进入游戏」后等这个号真正进游戏的上限（按商城图标判定主界面）。")
        self.var_threshold = self._num_row(
            card, 8, "匹配阈值", "match_threshold", 0.85,
            "越高越严格：认按钮/账号卡片更准但可能漏；越低越宽松、易误认。建议 0.85~0.92。")
        ctk.CTkFrame(card, fg_color="transparent", height=14).grid(row=9, column=0, sticky="ew")

        self.refresh()

    # ---- 行构件 ----
    def _path_row(self, card, row):
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=16, pady=(10, 0))
        f.grid_columnconfigure(0, weight=1)
        lbl = ctk.CTkLabel(f, text="客户端路径", font=self.fonts["body"], text_color=T.TEXT)
        lbl.grid(row=0, column=0, sticky="w")
        Tooltip(lbl, "游戏客户端的可执行文件（.exe）。登录任务用它逐个启动每个号的客户端。", self.fonts)
        self.var_path = ctk.StringVar(value="")
        ent = ctk.CTkEntry(f, textvariable=self.var_path, font=self.fonts["body"], height=32,
                           fg_color=T.SURFACE_2, border_color=T.BORDER, justify="left")
        ent.grid(row=0, column=1, sticky="ew", padx=(10, 8))
        ent.bind("<FocusOut>", lambda e: self._save_path())
        ent.bind("<Return>", lambda e: self._save_path())
        ctk.CTkButton(f, text="浏览…", font=self.fonts["body"], height=32, width=88,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._browse).grid(row=0, column=2, sticky="e")

    def _args_row(self, card, row):
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=16, pady=(8, 0))
        lbl = ctk.CTkLabel(f, text="启动参数（可选）", font=self.fonts["body"], text_color=T.TEXT)
        lbl.pack(side="left")
        Tooltip(lbl, "启动客户端时附带的参数，按空格分隔。一般留空；客户端需要指定服/大区时才填。", self.fonts)
        self.var_args = ctk.StringVar(value="")
        ent = ctk.CTkEntry(f, textvariable=self.var_args, font=self.fonts["body"], height=32, width=320,
                           fg_color=T.SURFACE_2, border_color=T.BORDER, justify="left")
        ent.pack(side="left", padx=(10, 0), fill="x", expand=True)
        ent.bind("<FocusOut>", lambda e: self._save_args())
        ent.bind("<Return>", lambda e: self._save_args())

    def _num_row(self, card, row, label, key, initial, tooltip):
        """一行数字参数（tasks.login.loop.<key>）：返回 StringVar，失焦/回车写回。"""
        f = ctk.CTkFrame(card, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=16, pady=(8, 0))
        lbl = ctk.CTkLabel(f, text=label, font=self.fonts["body"], text_color=T.TEXT)
        lbl.pack(side="left")
        Tooltip(lbl, tooltip, self.fonts)
        var = ctk.StringVar(value=str(initial))
        ent = ctk.CTkEntry(f, textvariable=var, width=92, height=32, font=self.fonts["body"],
                           fg_color=T.SURFACE_2, border_color=T.BORDER, justify="right")
        ent.pack(side="left", padx=(10, 0))
        for ev in ("<FocusOut>", "<Return>"):
            ent.bind(ev, lambda e, k=key, d=initial: self._save_num(k, d))
        return var

    # ---- 刷新 / 状态 ----
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        loop = tc.get("loop") or {}
        self.var_path.set(tc.get("client_path") or "")
        self.var_args.set(tc.get("client_args") or "")
        self.var_launch.set(str(loop.get("client_launch_timeout_sec", 180)))
        self.var_step.set(str(loop.get("step_timeout_sec", 30)))
        self.var_enter.set(str(loop.get("enter_game_wait_sec", 300)))
        self.var_threshold.set(str(loop.get("match_threshold", 0.85)))
        _ready, txt, color = self._status(tc)
        self.lbl_calib.configure(text=txt, text_color=color)

    def _status(self, tc):
        """就绪状态文本：客户端路径 + 必要区域/模板 + 账号库标了几个 + 挑了几个去登录。
        账号库的数字来自 core/account_history（图片/名字都存在库里，不看 config 模板键）。"""
        path = (tc.get("client_path") or "").strip()
        if not path:
            path_txt, path_ok = "客户端路径 未设置", False
        elif not os.path.isfile(path):
            path_txt, path_ok = "客户端路径 指向的文件不存在", False
        else:
            path_txt, path_ok = "客户端路径 ✓", True
        regions = tc.get("regions") or {}
        templates = tc.get("templates") or {}
        reg_ok = bool(regions.get(LOGIN_REQUIRED_REGION))
        tpl_done = sum(1 for k in LOGIN_FLOW_TPL_KEYS if templates.get(k))
        lib = ah.get_library(self.app.cfg)
        sel = ah.get_selection(self.app.cfg)
        char_done = sum(1 for s in sel if ah.char_slot_exists(s))
        ready = (path_ok and reg_ok and tpl_done == len(LOGIN_FLOW_TPL_KEYS)
                 and bool(lib) and bool(sel) and char_done == len(sel))
        seg = [path_txt, f"账号列表区域 {'✓' if reg_ok else '未标'}",
               f"必要模板 {tpl_done}/{len(LOGIN_FLOW_TPL_KEYS)}",
               f"账号库 {len(lib)}/{ah.MAX_SLOTS} 个",
               f"已挑 {len(sel)}/{LOGIN_MAX_ACCOUNTS} 个号",
               f"角色卡 {char_done}/{len(sel)} 个"]
        text = "　".join(seg) + ("　✓ 可运行（在「日常」页顶部登录区点开始）" if ready
                                 else "　（还需设置/标定）")
        if not sel:
            text += "；挑好号后记得每号各标一张「角色卡」（登录流程最后一步选角色要用）"
        elif char_done < len(sel):
            missing = "、".join(ah.get_names(self.app.cfg).get(s, "槽%d" % (s + 1))
                                for s in sel if not ah.char_slot_exists(s))
            text += f"；以下号还没标角色卡：「{missing}」—— 去账号库点它们的「标角色」"
        elif 0 < len(lib) < ah.MAX_SLOTS:
            text += f"；账号不必全标，已标 {len(lib)} 个就能挑 {len(lib)} 个"
        return ready, text, (T.SUCCESS if ready else T.WARN)

    # ---- 参数保存（改动即保存）----
    def _write(self, mutate):
        """改一项 tasks.login 配置并落盘。mutate(tc) 就地改。

        读回来的 tc 已叠加了「通用」页公共标定（活动列表/背包列表/战斗标识/小闹钟），
        写回前必须剥掉那些共享键——共享标定只归 tasks.shared 所有（见 core/config
        的 EXCLUSIVE_SHARED_REGIONS / calibrate_dialog._save 的同一约定），否则一次改参数
        就把共享标定抄进本任务命名空间。"""
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, self.TASK_NAME)
        for k in SHARED_REGION_KEYS:
            tc.get("regions", {}).pop(k, None)
        for k in SHARED_TPL_KEYS:
            tc.get("templates", {}).pop(k, None)
        mutate(tc)
        cfg_mod.set_task_config(cfg, self.TASK_NAME, tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._status_refresh()

    def _save_num(self, key, default):
        var = {"client_launch_timeout_sec": self.var_launch,
               "step_timeout_sec": self.var_step,
               "enter_game_wait_sec": self.var_enter,
               "match_threshold": self.var_threshold}[key]
        try:
            val = float(var.get())
        except (TypeError, ValueError):
            val = float(default)
        if key == "match_threshold":
            val = min(0.99, max(0.5, val))
        else:
            val = max(1.0, val)

        def _m(tc):
            tc.setdefault("loop", {})[key] = val
        self._write(_m)
        try:
            var.set(str(val))
        except Exception:
            pass

    def _save_path(self):
        path = (self.var_path.get() or "").strip().strip('"')
        self._write(lambda tc: tc.__setitem__("client_path", path))

    def _save_args(self):
        args = (self.var_args.get() or "").strip()
        self._write(lambda tc: tc.__setitem__("client_args", args))

    def _status_refresh(self):
        try:
            _r, txt, color = self._status(cfg_mod.task_config(self.app.cfg, self.TASK_NAME))
            self.lbl_calib.configure(text=txt, text_color=color)
        except Exception:
            pass

    # ---- 选客户端 ----
    def _browse(self):
        from tkinter import filedialog
        cur = (self.var_path.get() or "").strip()
        init = os.path.dirname(cur) if cur else None
        path = filedialog.askopenfilename(title="选择游戏客户端", initialdir=init,
                                         filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if not path:
            return
        self.var_path.set(path)
        self._save_path()

    # ---- 标定 ----
    def _open_calibrate(self):
        open_calibrate(self.app, self.TASK_NAME, on_done=self.refresh, owner=self, slot="_cal_dialog")

    def _open_gallery(self):
        """账号库弹窗（标定账号图 + 挑最多 5 个去登录），改完刷新本卡状态。
        登录区（日常页）也开着同一个窗：AccountGallery.open 去重，两边共享一份数据。"""
        AccountGallery.open(self.app, on_done=self.refresh)
