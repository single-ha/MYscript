# -*- coding: utf-8 -*-
"""
任务运行器：在后台线程里跑一个 Task，把日志通过线程安全队列交给 GUI。
GUI 只需 start()/stop()/poll 队列，完全不必关心线程细节。

全局唯一运行锁：TaskRunner 维护一份跨实例的活跃注册表，同一时刻只允许一个
「未被停止」的任务在跑（一只鼠标只能服务一个任务）。副本队列连续刷副本时各个
runner 是依次接棒（上一个线程死后才启动下一个），不受影响。
"""

import queue
import threading

from .context import TaskContext

# 互斥拒绝时的 GUI 通知回调：由 GUI 注册（App.__init__），起始失败被全局唯一锁拒绝时回调，
# 用于弹醒目 toast。None=未注册。调用方只在自己（GUI 线程）调用 start() 时才可能拒绝，
# 故回调天然在主线程，可安全操作 UI。
_rejected_hook = None


def set_run_rejected_hook(fn):
    """GUI 注册「任务启动被互斥拒绝」的回调（回调参数=拒绝提示字符串）。传 None 取消。"""
    global _rejected_hook
    _rejected_hook = fn


class TaskRunner:
    # ---- 全局唯一运行锁：活跃 runner 注册表（跨所有页面的实例）----
    _ACTIVE = []            # list[TaskRunner]，只在 _LOCK 保护下访问
    _LOCK = threading.Lock()

    def __init__(self, task, cfg):
        self.task = task
        self.cfg = cfg
        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = None

    def _log(self, msg, level="info"):
        self.log_queue.put((level, msg))

    def is_running(self):
        return self.thread is not None and self.thread.is_alive()

    @classmethod
    def _running_others(cls, me):
        """注册表里除 self 外「正在跑且未被要求停止」的实例。
        已 stop 的实例（事件已置位、线程在收尾）不算——否则页面「停止→立即重跑」会被
        自己的旧实例挡掉。返回列表（空=可启动新任务）。"""
        with cls._LOCK:
            return [r for r in cls._ACTIVE
                    if r is not me and r.is_running() and not r.stop_event.is_set()]

    @classmethod
    def active_titles(cls):
        """当前正在运行的（未被停止的）任务 title 列表，供 GUI 侧边栏状态显示。
        全局唯一锁下通常只有 1 个；拿不到时返回 []。"""
        with cls._LOCK:
            return [r.task.title for r in cls._ACTIVE
                    if r.is_running() and not r.stop_event.is_set()
                    and r.task is not None]

    def start(self):
        """启动任务。返回 (ok, problems)。preflight 不通过不启动；已有别的任务在跑则拒绝。"""
        if self.is_running():
            return False, ["任务已在运行"]
        self.stop_event.clear()
        ctx = TaskContext(self.cfg, log_fn=self._log, stop_event=self.stop_event)
        ok, problems = self.task.preflight(ctx)
        if not ok:
            return False, problems
        others = self._running_others(self)
        if others:
            name = others[0].task.__class__.__name__
            msg = f"已有任务正在运行（{name}），请先停止它再启动新任务。"
            hook = _rejected_hook
            if hook is not None:
                try:
                    hook(msg)
                except Exception:
                    pass
            return False, [msg]
        with self._LOCK:
            self._ACTIVE.append(self)

        def _wrap():
            try:
                self.task.run(ctx)
            except Exception as e:  # 任务里任何异常都不该让线程静默死掉
                self._log(f"任务异常：{e}", "error")
            finally:
                with self._LOCK:
                    try:
                        self._ACTIVE.remove(self)
                    except ValueError:
                        pass

        self.thread = threading.Thread(target=_wrap, daemon=True)
        self.thread.start()
        return True, []

    def stop(self):
        self.stop_event.set()
