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
from . import recorder

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
        # 异步启动（start_async）期间为 True：preflight 正在后台跑、任务线程尚未建立。
        # 供 GUI 的 pump 区分「启动中」与「已结束」，避免把启动中误判成跑完而复位按钮。
        self._starting = False
        # 异步启动在 preflight 阶段被请求取消（急停/关窗）。不能复用 stop_event——start() 内会
        # stop_event.clear()，会把 preflight 阻塞期间外部刚置上的停止信号清掉，导致任务无视急停照样启动。
        self._async_cancel = False

    def is_starting(self):
        """是否正处于异步 preflight/启动阶段（任务线程还没起来）。"""
        return self._starting

    def _log(self, msg, level="info"):
        # 日志来源短标签：任务可在运行中动态设 self.current_log_source（如「日常」链
        # 每步设成当前子任务），GUI 全局面板按它打来源标签；None=回退页面默认来源。
        src = getattr(self.task, "current_log_source", None)
        self.log_queue.put((level, msg, src))

    def drain_logs(self):
        """取出全部待处理日志，返回 [(level, msg, source)]。source 可空（None=页面默认标签）。
        兼容历史 2 元组条目。"""
        out = []
        q = self.log_queue
        while not q.empty():
            row = q.get()
            if isinstance(row, (tuple, list)) and len(row) >= 3:
                out.append((row[0], row[1], row[2]))
            else:
                out.append((row[0], row[1], None))
        return out

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
            # 一次「运行」的录屏归组：任务单跑/日常任务链都从这里进，begin/end 包出一个 run_时间戳 文件夹。
            recorder.begin_run(self.cfg)
            try:
                self.task.run(ctx)
            except Exception as e:  # 任务里任何异常都不该让线程静默死掉
                self._log(f"任务异常：{e}", "error")
            finally:
                recorder.end_run()
                with self._LOCK:
                    try:
                        self._ACTIVE.remove(self)
                    except ValueError:
                        pass

        self.thread = threading.Thread(target=_wrap, daemon=True)
        self.thread.start()
        return True, []

    def start_async(self, on_done, ui_marshal=None):
        """异步启动：把【preflight（含 select_windows/locate_all 的 Win32 窗口枚举）】与
        全局唯一锁检查整体挪到后台线程，避免目标窗口无响应时 EnumWindows 类调用阻塞 Tk 主线程
        （现象：点「延迟执行」到点不启动、整窗未响应只能杀进程）。

        - on_done(ok, problems)：启动结论回调。ui_marshal 给定时经它投递回主线程执行（安全操作 Tk）；
          不给则在后台线程直接回调（调用方自行保证线程安全）。
        - 立即返回，不返回启动结论（结论走回调）。重复点击由调用方置「启动中」状态自行防抖。
        - 全局锁拒绝也走 on_done（不再依赖 _rejected_hook 的「天然主线程」假设，因为这里在后台线程）。
        """
        def _marshal(fn):
            if ui_marshal is not None:
                try:
                    ui_marshal(fn)
                    return
                except Exception:
                    pass
            fn()

        self._starting = True
        self._async_cancel = False

        def worker():
            try:
                # 启动前已被取消（极快连续点开始又急停）：直接放弃，不进 start()
                if self._async_cancel:
                    ok, problems = False, ["启动已被取消"]
                else:
                    ok, problems = self.start()
                    # preflight 阻塞期间用户急停/关窗：即便 start 已成功也立即请求停止，
                    # 并把结论改成未启动，避免任务无视急停跑起来。
                    if ok and self._async_cancel:
                        self.stop_event.set()
                        ok, problems = False, ["启动过程中已被停止"]
            except Exception as e:  # preflight 自身抛错也不能让后台线程静默死掉
                ok, problems = False, [f"启动自检异常：{e}"]
            finally:
                self._starting = False
            _marshal(lambda: on_done(ok, problems))

        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        # 异步 preflight 阶段也标记取消（任务线程起来后 stop_event 照常生效）
        self._async_cancel = True
        self.stop_event.set()
