from abc import ABC, abstractmethod
import customtkinter as ctk
from . import  theme as T


class PageBase(ABC, ctk.CTkFrame):
    LOG_SOURCE = ""

    def __init__(self, master, main_view):
        super().__init__(master)
        self.master = master
        self.main_view = main_view
        self.main_view = main_view
        self.cfg = main_view.cfg
        self.fonts = main_view.fonts
        self.configure(fg_color=T.BG)

    @abstractmethod
    def get_title(self):
        pass
