
class TaskComponentBase:
    LOG_SOURCE = ""

    def __init__(self, master, main_view):
        self.master = master
        self.main_view = main_view

    def refresh(self):
        pass

    def pump(self):
        pass

    def _log_line(self, msg, level="info", source=None):
        # 日志统一汇到 App 右侧全局面板；source 缺省用本页 LOG_SOURCE（「通用」），
        # 组队/整理背包在 pump 与各自的开始/停止消息里显式传「组队」「整理背包」。
        self.main_view.log_line(msg, level, source or self.LOG_SOURCE)
