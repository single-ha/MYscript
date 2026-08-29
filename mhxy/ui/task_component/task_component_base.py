class TaskComponentBase:
    LOG_SOURCE = ""

    def __init__(self, master, main_view):
        self.master = master
        self.main_view = main_view
        self.fonts = main_view.fonts

    def refresh(self):
        pass

    def calib_singleton(self, attr, only, fail_msg, exclude=None):
        self.main_view.calib_singleton(attr, only, fail_msg, exclude)

    def open_window_picker(self, after=None, captain_ns=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。
        captain_ns: 传入则多开模式下可在卡片上直接指定队长，写入 tasks.<captain_ns>.captain_index。"""
        from ..window_picker import WindowPickerDialog

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

    def log_line(self, msg, level="info", source=None):
        # 日志统一汇到 App 右侧全局面板；source 缺省用本页 LOG_SOURCE（「通用」），
        # 组队/整理背包在 pump 与各自的开始/停止消息里显式传「组队」「整理背包」。
        self.main_view.log_line(msg, level, source or self.LOG_SOURCE)
