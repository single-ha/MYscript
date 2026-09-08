# -*- coding: utf-8 -*-
"""关于页。独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import customtkinter as ctk

from .. import theme as T
from ..common import Card, bind_wraplength


class AboutPage(ctk.CTkFrame):
    def __init__(self, master, app):
        super().__init__(master, fg_color="transparent")
        self.fonts = app.fonts
        ctk.CTkLabel(self, text="关于", font=self.fonts["title"], text_color=T.TEXT).pack(
            anchor="w", padx=4, pady=(2, 14))
        card = Card(self)
        card.pack(fill="x", padx=4)
        text = (
            "梦幻西游 · 时空  辅助助手\n\n"
            "· 原理：截屏 + 图像识别 + 拟人化模拟点击，不读内存、不注入进程。\n"
            "· 输入走 Windows SendInput 底层接口，配合贝塞尔移动/随机抖动，尽量不像机器。\n"
            "· 模块化架构：core 基础设施 / tasks 任务 / gui 界面，便于后续扩展。\n\n"
            "⚠ 风险提示：使用任何第三方脚本都违反《梦幻西游》用户协议，可能被封号（含永封）。\n"
            "   请务必用小号测试，自负风险。本工具仅供学习交流。"
        )
        lbl = ctk.CTkLabel(card, text=text, font=self.fonts["body"], text_color=T.TEXT,
                     justify="left")
        lbl.pack(fill="x", padx=16, pady=16)
        bind_wraplength(lbl, padding=32)
