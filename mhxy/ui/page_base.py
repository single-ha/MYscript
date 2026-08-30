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
        self.configure(fg_color=T.BG)
    def pump(self):
        pass
    @abstractmethod
    def get_title(self):
        pass
