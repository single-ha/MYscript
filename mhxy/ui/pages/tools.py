# -*- coding: utf-8 -*-
"""工具分类页：内嵌 商城（抢关注）、窗口尺寸归一化、整理背包、拓印；后续其它工具汇总到这里。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ..common import Card, bind_wraplength
from ...core import config as cfg_mod
from ...core import window as win_mod
from .category import CategoryPage
from .mall import MallPage
from .organize_bag import OrganizeBagPage
from .tuoying import TuoyingPage


class WindowNormalizePage(ctk.CTkFrame):
    """按基准尺寸统一所有游戏窗口大小。"""

    LOG_SOURCE = "窗口归一化"

    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.cfg = app.cfg
        self._enum_pending = None
        self._enum_empty_retries = 0
        self._enum_token = None
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="窗口尺寸归一化", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(
            head,
            text="把所有窗口拉回同一基准尺寸；基准尺寸可在窗口列表中设为基准，也可在任务配置页/日常页读取。",
            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w",
        )
        sub.pack(fill="x", anchor="w", pady=(4, 0))
        bind_wraplength(sub)

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)
        self.refresh()

    def refresh(self):
        self.cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg
        self._refresh_body()

    def _card(self):
        c = Card(self.body)
        c.pack(fill="x", pady=(0, T.SP_3), padx=2)
        return c

    def _normalize_now(self):
        cfg = cfg_mod.load_config()
        self.cfg = cfg
        self.app.cfg = cfg
        base = (cfg.get("targets") or {}).get("base_size")
        if not base or len(base) < 2:
            self.app.toast("请先设置基准尺寸（在下面窗口列表点「设为基准」）")
            return
        bw, bh = int(base[0]), int(base[1])
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        targets = cfg.get("targets", {})
        try:
            wins = win_mod.resolve_targets(title, offset, targets)
        except Exception:
            wins = []
        if not wins:
            self.app.toast(f"没检测到目标窗口（标题含「{title}」），请先打开游戏")
            return
        todo = []
        for w in wins:
            r = w.rect()
            if r and (abs(r[2] - bw) > 4 or abs(r[3] - bh) > 4):
                todo.append(w)
        if not todo:
            self.app.toast(f"所有目标窗口已是基准尺寸 {bw}×{bh}，无需还原")
            self.refresh()
            return
        ok = 0
        for w in todo:
            w.activate()
            if w.resize_to(bw, bh):
                ok += 1
        self.app._game_connected = None
        if ok < len(todo):
            self.app.toast(f"已还原 {ok}/{len(todo)} 个号到 {bw}×{bh}；部分窗口可能锁了分辨率档位")
        else:
            self.app.toast(f"已把 {ok} 个号还原到基准尺寸 {bw}×{bh}")
        self.refresh()

    def _refresh_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        cfg = self.cfg
        targets = cfg.get("targets", {})
        base = targets.get("base_size")

        c2 = self._card()
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(14, 4))
        head2.grid_columnconfigure(0, weight=1)
        txt2 = ctk.CTkFrame(head2, fg_color="transparent")
        txt2.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt2, text="当前基准尺寸", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        base_txt = f"{int(base[0])}×{int(base[1])}" if base and len(base) >= 2 else "未设置"
        ctk.CTkLabel(txt2, text=f"当前基准：{base_txt}", font=self.fonts["body"],
                     text_color=T.TEXT if base else T.WARN).pack(anchor="w", pady=(4, 0))
        sub2 = ctk.CTkLabel(
            txt2,
            text="「还原尺寸」把目标窗口按基准尺寸统一；在下方窗口列表中点「设为基准」便于把某个窗口的当前尺寸作为基准。",
            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left",
        )
        sub2.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub2)
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="还原尺寸", font=self.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER, command=self._normalize_now).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="刷新", font=self.fonts["body"], height=30, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER, command=self.refresh).pack()

        if not (base and len(base) >= 2):
            ctk.CTkLabel(c2, text="基准尺寸尚未设置：在下方窗口列表点「设为基准」即可。",
                         font=self.fonts["small"], text_color=T.WARN, justify="left").pack(
                anchor="w", padx=16, pady=(2, 0))

        ctk.CTkLabel(c2, text="检测到的窗口（点「设为基准」用该窗口的当前尺寸作为基准）：",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").pack(
            anchor="w", padx=16, pady=(8, 2))
        self._enum_empty_retries = 0
        rows_holder = ctk.CTkFrame(c2, fg_color="transparent")
        rows_holder.pack(fill="x")
        ctk.CTkLabel(rows_holder, text="正在检测窗口…", font=self.fonts["body"],
                     text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
        self._kick_enum_windows(rows_holder, base)

    def _kick_enum_windows(self, holder, base):
        cfg = self.cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        token = object()
        self._enum_token = token
        self._enum_empty_retries = 0

        def work():
            try:
                wins = win_mod.locate_all(title, offset)
                data = [(w, w.rect()) for w in wins]
            except Exception:
                data = []
            self._enum_pending = (holder, base, data, token)

        threading.Thread(target=work, daemon=True).start()

    def _drain_enum(self):
        pend = getattr(self, "_enum_pending", None)
        if pend is None:
            return
        self._enum_pending = None
        holder, base, data, token = pend
        try:
            self._fill_win_rows(holder, data, base, token)
        except Exception:
            pass

    def _fill_win_rows(self, holder, data, base, token):
        if token is not getattr(self, "_enum_token", None):
            return
        try:
            if not holder.winfo_exists():
                return
        except Exception:
            return
        if not data:
            n = getattr(self, "_enum_empty_retries", 0)
            if n < 3:
                self._enum_empty_retries = n + 1
                self.app.after(400, lambda: self._kick_enum_windows(holder, base))
                return
            try:
                for w in holder.winfo_children():
                    w.destroy()
            except Exception:
                pass
            ctk.CTkLabel(holder, text="没检测到游戏窗口，请先打开游戏再点「刷新」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
            return
        try:
            for w in holder.winfo_children():
                w.destroy()
        except Exception:
            return
        for i, (w, r) in enumerate(data):
            row = ctk.CTkFrame(holder, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.pack(fill="x", padx=12, pady=4)
            row.grid_columnconfigure(0, weight=1)
            meta = f"号{i + 1}    {r[2]}×{r[3]}    @({r[0]},{r[1]})" if r else f"号{i + 1}    （窗口已失效）"
            is_base = bool(base and r and int(base[0]) == r[2] and int(base[1]) == r[3])
            ctk.CTkLabel(row, text=meta + ("   ✓ 当前基准" if is_base else ""),
                         font=self.fonts["body"], text_color=T.SUCCESS if is_base else T.TEXT).grid(
                row=0, column=0, sticky="w", padx=12, pady=8)
            ctk.CTkButton(row, text="设为基准", font=self.fonts["small"], width=84, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER,
                          text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                          command=lambda w=w: self._set_base_from(w)).grid(row=0, column=1, padx=10)
        ctk.CTkFrame(holder, fg_color="transparent", height=6).pack()

    def _set_base_from(self, w):
        r = w.rect()
        if not r:
            self.app.toast("该窗口已失效，请点「刷新」")
            return
        cfg = cfg_mod.load_config()
        cfg.setdefault("targets", {})["base_size"] = [r[2], r[3]]
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast(f"已设基准尺寸 {r[2]}×{r[3]}（点「还原尺寸」时其它号会归一化到这个大小）")
        self.refresh()

    def pump(self):
        self._drain_enum()


class ToolsPage(CategoryPage):
    LOG_SOURCE = "工具"

    def __init__(self, master, app):
        super().__init__(
            master, app,
            tabs=[("商城", MallPage), ("窗口归一化", WindowNormalizePage), ("整理背包", OrganizeBagPage), ("拓印", TuoyingPage)],
            default=0, title="工具")
