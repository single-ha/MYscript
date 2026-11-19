# -*- coding: utf-8 -*-
"""
任务录屏：开关打开后，每个任务自动录【整个虚拟屏】（所有显示器外接矩形，所有号同框）。

设计（user 2026-11-19 拍板）：
  · 零新依赖：抓帧用 mss（window 模块已在用）、编码写盘用 cv2.VideoWriter，帧天然是 BGR。
  · 不抢焦点：mss 直接抓屏幕真实像素，不走 window.grab_screen 的「遮挡才激活」逻辑，多开轮转不切台。
  · 每次「运行」归一个文件夹：任务单跑 / 日常任务链都由 TaskRunner 包一层 begin_run/end_run，
    同一次运行里的各子任务视频都落在 recordings/run_YYYYMMDD_HHMMSS/ 下，**每个任务一个视频**。
  · 多窗口做同一子任务（日常每窗口独立链）只录一个文件：注册表按任务名引用计数去重，
    谁先 acquire 谁建录制器，最后一个 release 才停录落盘。
  · 自动清理：recordings/ 只保留最近 record_keep_runs（默认 3）次运行的文件夹，更早的整轮删除。
  · 全程容错：录屏任何异常都只记日志、绝不冒泡影响任务；开关关闭时零线程、零抓帧、零 VideoWriter。

内存：边抓边写、不囤帧，任意时刻常驻仅一帧（1080p BGR≈6MB）+ 编码器缓冲，与录制时长无关。
"""

import time
import shutil
import threading
import datetime

import numpy as np
import cv2
import mss

from .config import RECORDINGS_DIR

_RUN_PREFIX = "run_"
_VIDEO_EXTS = (".mp4", ".avi")


def _virtual_monitor(sct):
    """mss.monitors[0] = 所有显示器合并的虚拟屏外接矩形 {left,top,width,height}。"""
    try:
        mon = sct.monitors[0]
        return int(mon["left"]), int(mon["top"]), int(mon["width"]), int(mon["height"])
    except Exception:
        return 0, 0, 0, 0


class ScreenRecorder:
    """一个任务一路视频：后台 daemon 线程按固定帧率抓虚拟屏并写 VideoWriter。

    尺寸在启动时按虚拟屏锁定；个别帧抓不到就跳过，尺寸变了就 resize 回锁定尺寸，
    保证写进 writer 的每帧 (w,h) 一致、文件不损坏。stop() 必定 release()（异常/急停也能正常收尾）。"""

    def __init__(self, path_no_ext, fps, log=None):
        # path_no_ext 不带扩展名；优先 mp4(mp4v)，打不开回退 avi(MJPG)
        self.path_no_ext = str(path_no_ext)
        self.fps = max(1, min(30, int(fps)))
        self._log = log or (lambda m, level="info": None)
        self._stop = threading.Event()
        self._thread = None
        self._writer = None
        self.path = None
        self.size = None              # (w, h)
        self.frames_written = 0
        self.frames_skipped = 0

    # ---- 生命周期 ----
    def start(self):
        try:
            with mss.mss() as sct:
                left, top, w, h = _virtual_monitor(sct)
            if w <= 0 or h <= 0:
                self._log("录屏启动失败：取不到虚拟屏尺寸，本次不录。", level="warn")
                return False
            self.size = (w, h)
            self._writer, self.path = self._open_writer(w, h)
            if self._writer is None:
                self._log("录屏启动失败：mp4/avi 编码器都打不开，本次不录。", level="error")
                return False
            self._thread = threading.Thread(target=self._loop, name="screen-recorder", daemon=True)
            self._thread.start()
            return True
        except Exception as e:
            self._log(f"录屏启动异常（已忽略，不影响任务）：{e}", level="warn")
            self._safe_release()
            return False

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=3.0)
        self._safe_release()

    # ---- 内部 ----
    def _open_writer(self, w, h):
        mp4 = self.path_no_ext + ".mp4"
        writer = cv2.VideoWriter(mp4, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (w, h))
        if writer.isOpened():
            return writer, mp4
        writer.release()
        avi = self.path_no_ext + ".avi"
        writer = cv2.VideoWriter(avi, cv2.VideoWriter_fourcc(*"MJPG"), self.fps, (w, h))
        if writer.isOpened():
            return writer, avi
        writer.release()
        return None, None

    def _grab_bgr(self, sct):
        left, top, w, h = _virtual_monitor(sct)
        if w <= 0 or h <= 0:
            return None
        raw = sct.grab({"left": left, "top": top, "width": w, "height": h})
        return cv2.cvtColor(np.array(raw), cv2.COLOR_BGRA2BGR)

    def _loop(self):
        interval = 1.0 / self.fps
        next_t = time.time()
        try:
            with mss.mss() as sct:
                while not self._stop.is_set():
                    now = time.time()
                    if now < next_t:
                        self._stop.wait(min(next_t - now, interval))
                        continue
                    next_t = max(next_t + interval, now)   # 卡顿时追帧，不堆积
                    try:
                        frame = self._grab_bgr(sct)
                    except Exception:
                        frame = None
                    if frame is None:
                        self.frames_skipped += 1
                        continue
                    if frame.shape[1] != self.size[0] or frame.shape[0] != self.size[1]:
                        frame = cv2.resize(frame, self.size)
                    try:
                        self._writer.write(frame)
                        self.frames_written += 1
                    except Exception as e:
                        self.frames_skipped += 1
                        self._log(f"录屏写帧异常（已忽略）：{e}", level="debug")
        except Exception as e:
            self._log(f"录屏线程异常（已忽略，不影响任务）：{e}", level="warn")
        finally:
            self._safe_release()

    def _safe_release(self):
        w = self._writer
        if w is not None:
            self._writer = None
            try:
                w.release()
            except Exception:
                pass


class RecorderRegistry:
    """全局单例：管「运行」归组 + 每任务录制器引用计数 + 自动清理。线程安全（日常在工作线程跑）。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._run_depth = 0
        self._enabled = False
        self._cfg = None
        self._run_id = None
        self._run_dir = None
        # task_name -> [ScreenRecorder, 引用计数]
        self._tasks = {}

    def _new_run_dir_locked(self):
        """建一个本次运行独有的 run 文件夹：秒级时间戳，撞名（同一秒连开多次）则加 _2/_3 后缀。"""
        stamp = datetime.datetime.now().strftime(_RUN_PREFIX + "%Y%m%d_%H%M%S")
        run_id = stamp
        d = RECORDINGS_DIR / run_id
        n = 1
        while d.exists():
            n += 1
            run_id = f"{stamp}_{n}"
            d = RECORDINGS_DIR / run_id
        d.mkdir(parents=True, exist_ok=True)
        return run_id, d

    # ---- 运行（run）生命周期：由 TaskRunner 包一层 ----
    def begin_run(self, cfg):
        """一次 TaskRunner 运行开始。嵌套安全（深度计数），最外层才真正建 run 文件夹并清旧。"""
        with self._lock:
            self._run_depth += 1
            if self._run_depth == 1:
                self._enabled = bool(cfg.get("record_screen", False))
                self._cfg = cfg
                if self._enabled:
                    # 只建本次 run 文件夹，不在开跑时清理——清理统一在 end_run 做，
                    # 避免同进程连开多次运行时把上一次还没结束/刚落盘的 run 删掉。
                    self._run_id, self._run_dir = self._new_run_dir_locked()
            return self._run_id

    def end_run(self):
        """一次运行结束：停掉本运行所有录制器（兜底），最外层退出时清理旧运行。"""
        with self._lock:
            if self._run_depth <= 0:
                return
            self._run_depth -= 1
            if self._run_depth == 0:
                for name in list(self._tasks.keys()):
                    self._close_task_locked(name)
                self._tasks.clear()
                if self._enabled:
                    self._prune_locked()
                self._enabled = False
                self._cfg = None
                self._run_id = None
                self._run_dir = None

    # ---- 任务级 acquire/release（由 Task.run 与日常链调用）----
    def acquire(self, cfg, task_name, title=None):
        """开始录某任务。同任务名已在录则仅计数 +1（多窗口去重）。返回是否真的在录。"""
        with self._lock:
            if not cfg.get("record_screen", False):
                return False
            run_dir = self._ensure_run_dir_locked(cfg)   # 防御：非 TaskRunner 路径也能落盘
            entry = self._tasks.get(task_name)
            if entry is not None:
                entry[1] += 1
                return True
            fps = self._fps(cfg)
            stamp = datetime.datetime.now().strftime("%H%M%S")
            base = run_dir / f"{task_name}_{stamp}"
            n = 1
            while any((base.with_suffix(ext)).exists() for ext in _VIDEO_EXTS):
                n += 1
                base = run_dir / f"{task_name}_{stamp}_{n}"
            rec = ScreenRecorder(base, fps, log=lambda m, level="info": self._cfg_log(m, level))
            if not rec.start():
                return False
            self._tasks[task_name] = [rec, 1]
            self._cfg_log(f"开始录屏：{title or task_name} → {rec.path}（{fps}fps，整个屏幕）", "info")
            return True

    def release(self, task_name):
        """某任务录完一次：计数归零才真正停录落盘。"""
        with self._lock:
            entry = self._tasks.get(task_name)
            if entry is None:
                return
            entry[1] -= 1
            if entry[1] <= 0:
                self._close_task_locked(task_name)
                self._tasks.pop(task_name, None)

    # ---- 内部 ----
    def _ensure_run_dir_locked(self, cfg):
        if self._run_dir is not None:
            return self._run_dir
        # 没有活跃 run（理论上任务都被 TaskRunner 包着；这里做防御性兜底）
        self._enabled = bool(cfg.get("record_screen", False))
        self._cfg = cfg
        self._run_id, self._run_dir = self._new_run_dir_locked()
        return self._run_dir

    def _close_task_locked(self, task_name):
        entry = self._tasks.get(task_name)
        if entry is None:
            return
        rec = entry[0]
        try:
            rec.stop()
            self._cfg_log(f"录屏结束：{task_name}（写 {rec.frames_written} 帧、跳过 {rec.frames_skipped} 帧）→ {rec.path}",
                          "info")
        except Exception as e:
            self._cfg_log(f"录屏收尾异常（已忽略）：{e}", "warn")

    def _fps(self, cfg):
        try:
            return max(1, min(30, int(cfg.get("record_fps", 5))))
        except (TypeError, ValueError):
            return 5

    def _cfg_log(self, msg, level="info"):
        # 注册表本身不持有 log_fn；录屏生命周期日志在调用点用 ctx.log 打更合适，
        # 这里仅在后台线程异常等没有 ctx 的场合静默（避免反向依赖 GUI）。
        return None

    def _prune_locked(self):
        """只保留最近 record_keep_runs 个 run_* 文件夹（按修改时间），更早的整轮删除。keep<=0 不清理。"""
        try:
            keep = int((self._cfg or {}).get("record_keep_runs", 3))
        except (TypeError, ValueError):
            keep = 3
        if keep <= 0 or not RECORDINGS_DIR.exists():
            return
        runs = sorted((p for p in RECORDINGS_DIR.iterdir()
                       if p.is_dir() and p.name.startswith(_RUN_PREFIX)),
                      key=lambda p: p.stat().st_mtime, reverse=True)
        for old in runs[keep:]:
            shutil.rmtree(old, ignore_errors=True)


# 进程级单例
_REGISTRY = RecorderRegistry()


def begin_run(cfg):
    return _REGISTRY.begin_run(cfg)


def end_run():
    return _REGISTRY.end_run()


def acquire(cfg, task_name, title=None):
    return _REGISTRY.acquire(cfg, task_name, title)


def release(task_name):
    return _REGISTRY.release(task_name)
