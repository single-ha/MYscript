import customtkinter as ctk

from .task_component.team import Team
from .task_component.normalize import Normalize
from .page_base import PageBase
from . import theme as T


class GeneralPage(PageBase):
    LOG_SOURCE = "通用"

    def __init__(self, master, main_view):
        super().__init__(master, main_view)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.runner = None
        self._leader_thumbs = []  # 行内队长ID缩略图防 GC
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=0, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)
        self.team = None
        self.normalize = None
        self.allTask = []
        self.init()

    def init(self):
        self.normalize = Normalize(self.body, self.main_view)
        self.allTask.append(self.normalize)
        self.team = Team(self.body, self.main_view)
        self.allTask.append(self.team)


    def pump(self):
        for task in self.allTask:
            task.pump()

    def get_title(self):
        return "通用 / 工具", "跨任务的通用功能：组队标定 + 一键组队 / 一键解散 / 还原窗口尺寸。各任务专属的标定与「选择窗口」仍在对应任务页。"
