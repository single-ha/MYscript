# -*- coding: utf-8 -*-
"""分类页基类。把若干任务页嵌进一个侧边栏导航项下，顶部用 segment 子导航切换。
例如「单人任务」分类页内嵌宝图/运镖/秘境降妖三页。成员页仍沿用 (master, app) 构造、
在 body 上按 row=0,col=0 + tkraise() 叠层（与 App 管理顶层页同一套约定）。

对外（供 App 下钻）：
  - .sub_pages  dict：<tab 标题> -> 已建成的成员页实例（懒建，未点过的不在里）。
  - pump() / refresh()  对已建成成员页逐一转发。
App 的 _iter_pages()/急停/广播据此遍历分类页的已建子页。
"""

import customtkinter as ctk

from .. import theme as T


class CategoryPage(ctk.CTkFrame):
    # 子类应设 LOG_SOURCE（供孵化中的页面用）；基类本身不直接记日志
    LOG_SOURCE = None

    def __init__(self, master, app, tabs, default=0, title=""):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        # tabs: [(标题, Page类), ...]
        self._tabs = tabs
        self._titles = [t for t, _ in tabs]
        self._sub_pages = {}     # 标题 -> 已建成员页
        self._current_title = None

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        self._build_top(title)
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_columnconfigure(0, weight=1)

        # 初始选中第一个 tab
        idx = default if 0 <= default < len(tabs) else 0
        self._switch(self._titles[idx])

    @property
    def sub_pages(self):
        """已建成成员页：title -> page 实例（懒建，未点过的不在里）。"""
        return self._sub_pages

    def _build_top(self, title):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        self.top_bar = bar   # 供子类（如 MultiPage）重排：把子导航挪到共用设置卡之下
        if title:
            ctk.CTkLabel(bar, text=title, font=self.fonts["title"], text_color=T.TEXT).grid(
                row=0, column=0, sticky="w")

        seg = ctk.CTkSegmentedButton(
            bar, values=self._titles, command=self._switch,
            font=self.fonts["body_b"], corner_radius=T.RADIUS_SM,
            fg_color=T.SURFACE_2, selected_color=T.ACCENT,
            selected_hover_color=T.ACCENT_HOVER, unselected_color=T.SURFACE_2,
            unselected_hover_color=T.SURFACE, text_color=T.TEXT_DIM,
            text_color_disabled=T.TEXT_DIM)
        seg.grid(row=0, column=1, sticky="e")
        self._seg = seg

    def _switch(self, title):
        """显示指定 tab 的成员页，未建则懒建。tab 标题映射回页面类。"""
        self._current_title = title
        # CTkSegmentedButton 初建时 _current_value=""，默认没有任何 tab 高亮；
        # 这里显式 set() 让它与「当前显示哪个子页」保持一致（否则打开即错位/无选中）。
        try:
            self._seg.set(title)
        except Exception:
            pass
        self._paint_seg_selection(title)
        if title not in self._sub_pages:
            cls = dict(self._tabs)[title]
            self._build_sub(title, cls)
        page = self._sub_pages[title]
        page.tkraise()
        # 切过去时刷新该子页内容：懒建或上次数据变化后，可见即最新。
        if hasattr(page, "refresh"):
            try:
                page.refresh()
            except Exception:
                pass

    def _build_sub(self, title, cls):
        page = cls(self.body, self.app)
        page.grid(row=0, column=0, sticky="nsew")
        self._sub_pages[title] = page

    def _paint_seg_selection(self, title):
        """选中子页签用白色字（T.ON_ACCENT），未选中的回到 TEXT_DIM。
        CTkSegmentedButton 选中态只变背景不变字色，这里手动补字色。"""
        bd = getattr(self._seg, "_buttons_dict", None)
        if not bd:
            return
        for value, btn in bd.items():
            try:
                btn.configure(text_color=T.ON_ACCENT if value == title else T.TEXT_DIM)
            except Exception:
                pass

    # ---- 供 App 下钻：对已建成成员页逐一转发 ----
    def refresh(self):
        for p in self._sub_pages.values():
            if hasattr(p, "refresh"):
                try:
                    p.refresh()
                except Exception:
                    pass

    def pump(self):
        for p in self._sub_pages.values():
            if hasattr(p, "pump"):
                try:
                    p.pump()
                except Exception:
                    pass

