import customtkinter as ctk
from .splash import Splash


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.main_view = None
        self.fonts = None
        self.splash = Splash(self)
        self.init()
        self.withdraw()  # 隐藏真正的根窗口，不显示

    def init(self):
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 1360, 720
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        self.title("梦幻 · 时空 助手")
        self.minsize(1180, 640)
        self.grid_columnconfigure(1, weight=1)  # 中间内容区随窗口拉伸
        self.grid_columnconfigure(2, weight=0)  # 右侧全局日志列固定宽
        self.grid_rowconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        import threading
        done = threading.Event()

        def _warm():
            try:
                import mhxy.ui.main_view
                from . import theme
                from .data import data
                from ..core import config as cfg_mod
                theme.init()
                data.cfg = cfg_mod.load_config()
            except Exception:
                pass  # 预热失败无妨：主线程随后会再 import 并暴露真实错误
            finally:
                done.set()

        threading.Thread(target=_warm, daemon=True).start()

        def _poll():
            if done.is_set():
                self.after(0, self.show_main)
            else:
                self.after(40, _poll)

        _poll()

    def show_main(self):
        from .main_view import MainView
        self.main_view = MainView(self)
        self.splash.on_close()
        self.splash = None
        self.deiconify()  #显示主窗口
        self.lift()

    def calib_singleton(self, attr, only, fail_msg, exclude=None):
        """打开一个标定窗并按 attr 去重：已开着就 lift 回来，不叠开多个写同一处 teaming 的窗
        （叠开会「后关的覆盖先关的」，让用户以为没生效）。"""
        existing = getattr(self, attr, None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from .calibrate_dialog import CalibrateDialog

        def _after():
            setattr(self, attr, None)
            self.refresh()

        try:
            setattr(self, attr, CalibrateDialog(self, task_name="teaming",
                                                only=only, exclude=exclude, on_done=_after))
        except Exception as e:
            setattr(self, attr, None)
            self.main_view.toast(f"{fail_msg}：{e}")

    def refresh(self):
        self.main_view.refresh()

    def open_window_picker(self, after=None, captain_ns=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。
        captain_ns: 传入则多开模式下可在卡片上直接指定队长，写入 tasks.<captain_ns>.captain_index。"""
        from .window_picker import WindowPickerDialog

        def _done():
            self._game_connected = None  # 选择可能变了，强制下次 tick 刷新药丸
            if callable(after):
                try:
                    after()
                except Exception:
                    pass

        try:
            WindowPickerDialog(self, on_done=_done, captain_ns=captain_ns)
        except Exception:
            pass

    def _on_close(self):
        if self.main_view:
            self.main_view.on_close()
        if self.splash:
            self.splash.on_close()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
