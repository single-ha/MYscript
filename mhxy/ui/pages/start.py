# -*- coding: utf-8 -*-
"""起始页：仅放「登录游戏」卡片。不属于日常任务链，独立运行。"""

import os

import customtkinter as ctk

from .. import theme as T
from ..account_gallery import AccountGallery
from ...core import account_history as ah
from ...core import config as cfg_mod
from ...core.config import (LOGIN_FLOW_TPL_KEYS, LOGIN_MAX_ACCOUNTS, LOGIN_REQUIRED_REGION,
                            SHARED_REGION_KEYS, SHARED_TPL_KEYS)
from ..common import (Card, OrderThumbs, Tooltip, bind_wraplength, open_calibrate)
from ..calibrate_dialog import calibrate_template_direct


class StartPage(ctk.CTkFrame):
    """起始页：登录游戏（独立于日常任务链）。"""
    TASK_NAME = "login"
    LOG_SOURCE = "登录"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self._cal_dialog = None
        self.login_runner = None

        # 顶部：登录游戏卡片（原日常页的登录区）
        self._build_login_card()

        # 底部留白占位
        ctk.CTkFrame(self, fg_color="transparent").grid(row=1, column=0, sticky="nsew")
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

    def _build_login_card(self):
        """登录游戏卡片：账号库/配置/开始登录 + 缩略图顺序条。"""
        card = Card(self)
        card.grid(row=0, column=0, sticky="ew", padx=4, pady=(12, 12))
        card.grid_columnconfigure(0, weight=1)
        self._login_card = card

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        head.grid_columnconfigure(3, weight=1)

        self._login_chev = ctk.CTkLabel(head, text="▾", font=self.fonts["h2"], text_color=T.TEXT_DIM,
                                        cursor="hand2", anchor="w")
        self._login_chev.grid(row=0, column=0, sticky="w", padx=(0, 4))
        ttl = ctk.CTkLabel(head, text="🚀 登录游戏", font=self.fonts["h2"], text_color=T.TEXT,
                           cursor="hand2", anchor="w")
        ttl.grid(row=0, column=1, sticky="w")
        for w in (self._login_chev, ttl):
            w.bind("<Button-1>", lambda e: self._toggle_login())

        self.lbl_login_badge = ctk.CTkLabel(head, text="", font=self.fonts["small"],
                                            anchor="w", text_color=T.TEXT_DIM)
        self.lbl_login_badge.grid(row=0, column=2, sticky="w", padx=(10, 0))

        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=4, sticky="e")
        ctk.CTkButton(btns, text="🗂  账号库…", font=self.fonts["body"], height=36, width=124,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._open_account_gallery).pack(side="left")
        ctk.CTkButton(btns, text="⚙  配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._goto_login_config).pack(side="left", padx=(8, 0))
        self.btn_login = ctk.CTkButton(btns, text="▶  开始登录", font=self.fonts["btn"], height=36,
                                       width=124, corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                                       hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                       command=self._toggle_login_run)
        self.btn_login.pack(side="left", padx=(8, 0))

        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(row=1, column=0, sticky="ew",
                                                              padx=16, pady=(0, 2))

        self._login_body = ctk.CTkFrame(card, fg_color="transparent")
        self._login_body.grid(row=2, column=0, sticky="ew", padx=16, pady=(10, 14))
        self._login_body.grid_columnconfigure(0, weight=1)

        self.login_order = OrderThumbs(self._login_body, self.fonts, empty_text="",
                                       thumb_h=54, border_color=T.SUCCESS, max_w=60)
        self.login_order.grid(row=0, column=0, sticky="w")
        self.lbl_login_order = ctk.CTkLabel(self._login_body, text="", font=self.fonts["small"],
                                            anchor="w", justify="left", text_color=T.TEXT_DIM)
        self.lbl_login_order.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        bind_wraplength(self.lbl_login_order)

        self._login_open = True

    def _toggle_login(self):
        self._login_open = not self._login_open
        if self._login_open:
            self._login_body.grid()
            self._login_chev.configure(text="▾", text_color=T.TEXT_DIM)
        else:
            self._login_body.grid_remove()
            self._login_chev.configure(text="▸", text_color=T.TEXT_DIM)

    def _refresh_login(self):
        lib = ah.get_library(self.app.cfg)
        names = ah.get_names(self.app.cfg)
        queue = ah.get_selection(self.app.cfg)

        self.login_order.set_items([(i + 1, names.get(s, "槽%d" % (s + 1)), ah.slot_rel(s))
                                    for i, s in enumerate(queue)])

        if not lib:
            badge, color = "账号库空", T.TEXT_DIM
            text = ("点「账号库」框选账号卡片/头像存进库里（最多 %d 个），"
                    "再从库里挑最多 %d 个去登录，按挑的先后依次登录。"
                    % (ah.MAX_SLOTS, LOGIN_MAX_ACCOUNTS))
        elif not queue:
            badge, color = "账号库 %d/%d　未挑" % (len(lib), ah.MAX_SLOTS), T.TEXT_DIM
            text = ("点「账号库」挑最多 %d 个要登录的号 —— 挑一个进一个，"
                    "下面缩略图的先后就是登录顺序。" % LOGIN_MAX_ACCOUNTS)
        else:
            miss = sum(1 for s in queue if not ah.char_slot_exists(s))
            badge, color = "已选 %d/%d" % (len(queue), LOGIN_MAX_ACCOUNTS), T.ACCENT
            if miss:
                badge, color = "已选 %d/%d · 角色卡缺 %d" % (len(queue), LOGIN_MAX_ACCOUNTS, miss), T.WARN
                text = ("有 %d 个号还没标「角色卡」（登录流程最后一步选角色要用）——"
                        "点「账号库」给它们各标一张。" % miss)
            else:
                text = "按下面缩略图的先后依次登录，每个号各开一次客户端。"
        self.lbl_login_badge.configure(text=badge, text_color=color)
        self.lbl_login_order.configure(text=text, text_color=T.TEXT_DIM)
        self._sync_login_btn()

    def _open_account_gallery(self):
        AccountGallery.open(self.app, on_done=self._refresh_login)

    def _goto_login_config(self):
        self._login_open = True
        self._login_body.grid()
        self._login_chev.configure(text="▾", text_color=T.TEXT_DIM)
        self.app._show("config")
        page = getattr(self.app, "pages", {}).get("config")
        if page is not None:
            try:
                page.reveal_card("login")
            except Exception:
                pass

    def _sync_login_btn(self):
        if self.login_runner and self.login_runner.is_running():
            if self.btn_login.cget("text") != "■  停止登录":
                self.btn_login.configure(text="■  停止登录", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
            return
        if self.btn_login.cget("text") != "▶  开始登录":
            self.btn_login.configure(text="▶  开始登录", fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER)

    def _toggle_login_run(self):
        if self.login_runner and self.login_runner.is_running():
            self.login_runner.stop()
            self._sync_login_btn()
            return

        self.app.cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(self.app.cfg, self.TASK_NAME)
        _ready, _, _ = self._status(tc)
        if not _ready:
            self.app.log_line("登录游戏：就绪检查未通过，请先完成配置/标定", "warn", self.LOG_SOURCE)
            return

        from ...tasks import get_task
        from ...core.runner import TaskRunner

        self.login_runner = TaskRunner(get_task(self.TASK_NAME)(), self.app.cfg)
        ok, problems = self.login_runner.start()
        if not ok:
            for p in problems:
                self.app.log_line("无法启动：" + p, "error", self.LOG_SOURCE)
            self.login_runner = None
            return
        self._sync_login_btn()

    def _status(self, tc):
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
        text = "　".join(seg) + ("　✓ 可运行（点「开始登录」运行）" if ready
                                  else "　（还需设置/标定）")
        if 0 < len(lib) < ah.MAX_SLOTS and not sel:
            text += f"；账号不必全标，已标 {len(lib)} 个就能挑 {len(lib)} 个"
        elif char_done < len(sel):
            missing = "、".join(ah.get_names(self.app.cfg).get(s, "槽%d" % (s + 1))
                                for s in sel if not ah.char_slot_exists(s))
            text += f"；「{missing}」还没标角色卡——去账号库点它们的「标角色」"
        return ready, text, (T.SUCCESS if ready else T.WARN)

    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        self._refresh_login()

    def pump(self):
        """被 App._tick 周期调用（RUNNABLE_KEYS 含 start）：抽干日志队列、跑完复位按钮。"""
        if not self.login_runner:
            return
        q = self.login_runner.log_queue
        while not q.empty():
            level, msg = q.get()
            self.app.log_line(msg, level, self.LOG_SOURCE)
        if not self.login_runner.is_running():
            self._sync_login_btn()