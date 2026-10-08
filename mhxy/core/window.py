# -*- coding: utf-8 -*-
"""
游戏窗口与屏幕截图。封装“找窗口 / 取窗口矩形 / 截图 / 窗口内坐标换算”，
让上层任务不用关心 mss / pygetwindow 细节。
"""

import os
import time
import ctypes
import ctypes.wintypes
import threading

import numpy as np
import cv2
import mss
import pygetwindow as gw


# 句柄相关的 user32 函数：必须显式声明 restype/argtypes 为 HWND(=void*)，
# 否则 64 位 Python 下默认 c_int 会把窗口句柄截断，比较/传参全错。
_user32 = ctypes.windll.user32
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
_user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
_user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
_user32.SetActiveWindow.argtypes = [ctypes.wintypes.HWND]
_user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
_user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.LPDWORD]
_user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
_user32.WindowFromPoint.restype = ctypes.wintypes.HWND
_user32.WindowFromPoint.argtypes = [ctypes.wintypes.POINT]
_user32.GetAncestor.restype = ctypes.wintypes.HWND
_user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
GA_ROOT = 0x2

_kernel32 = ctypes.windll.kernel32
_kernel32.OpenProcess.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.BOOL, ctypes.wintypes.DWORD]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD,
    ctypes.wintypes.LPWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)]
_kernel32.QueryFullProcessImageNameW.restype = ctypes.wintypes.BOOL
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ---- 游戏窗口识别：按「进程 exe 名」过滤（标题会和别的窗口撞，进程名才稳）----
#   背景（踩坑）：原先只按标题子串 "梦幻西游" 匹配，结果【终端/编辑器等标题里恰好含这几个字的窗口】
#   会被误认成游戏窗口去点击（实测把 Windows Terminal 当成了「号1」，因为它的标签名含「梦幻西游」）。
#   游戏窗口类名是【随机串】（每个窗口都不同，无法白名单），但进程 exe 名稳定，故据此过滤最可靠。
#   默认值对应《时空》客户端；若客户端 exe 改名，改 config 顶层 window_process 即可（空串=退回纯标题匹配）。
_GAME_PROCESS = "MyGame_x64r.exe"


def set_game_process(name):
    """配置「只认这个 exe 进程的窗口」。来自 config.window_process。
    传空/None=不按进程过滤（退回纯标题匹配，与旧行为一致）。进程名比对大小写不敏感。"""
    global _GAME_PROCESS
    _GAME_PROCESS = (name or "").strip() or None


def _proc_basename(hwnd):
    """返回 hwnd 所属进程的 exe basename（小写）。取不到返回 ""。"""
    try:
        pid = ctypes.wintypes.DWORD()
        _user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        hp = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not hp:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(512)
            sz = ctypes.wintypes.DWORD(512)
            if not _kernel32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(sz)):
                return ""
            return os.path.basename(buf.value or "").lower()
        finally:
            _kernel32.CloseHandle(hp)
    except Exception:
        return ""


def _match_basic(w, title_substr):
    """游戏窗口基本判定：标题含关键字 + 尺寸够大 +（若配置了 _GAME_PROCESS）所属进程匹配。
    不在此查「最小化」——locate() 允许最小化窗口当候选并排后面，locate_all() 自行另外排除。"""
    try:
        if title_substr not in (w.title or ""):
            return False
        if w.width <= 100 or w.height <= 100:
            return False
        hwnd = w._hWnd
    except Exception:
        return False
    if _GAME_PROCESS and _proc_basename(hwnd) != _GAME_PROCESS.lower():
        return False
    return True


def _force_foreground(hwnd, tries=3):
    """把 hwnd 强制切到前台并校验。成功返回 True。

    为什么不直接用 pygetwindow.activate()：它只是 `SetForegroundWindow(hwnd)`，而 Windows 的
    『防焦点抢占』会在前台属于别的窗口/进程时【拒绝】这次调用(返回0)——多开轮转里这极常见，
    结果目标号没真正到前台，随后的点击落在后台号上被吞/点歪（曾导致秘境点「挑战」点到聊天）。
    这里用业界通行的解法：先 ShowWindow+BringWindowToTop，再 AttachThreadInput 把本线程附到
    当前前台线程后 SetForegroundWindow（绕过抢占锁），最后用 GetForegroundWindow 校验、失败重试。"""
    if not hwnd:
        return False
    hwnd = int(hwnd)
    SW_SHOW = 5
    kernel32 = ctypes.windll.kernel32
    cur_tid = kernel32.GetCurrentThreadId()
    for _ in range(max(1, tries)):
        try:
            if int(_user32.GetForegroundWindow() or 0) == hwnd:
                return True
        except Exception:
            pass
        try:
            _user32.ShowWindow(hwnd, SW_SHOW)
            _user32.BringWindowToTop(hwnd)
            fg = _user32.GetForegroundWindow()
            fg_tid = _user32.GetWindowThreadProcessId(fg, None) if fg else 0
            tgt_tid = _user32.GetWindowThreadProcessId(hwnd, None)
            attached = []
            for tid in (fg_tid, tgt_tid):
                if tid and tid != cur_tid:
                    _user32.AttachThreadInput(cur_tid, tid, True)
                    attached.append(tid)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetActiveWindow(hwnd)
            for tid in attached:
                _user32.AttachThreadInput(cur_tid, tid, False)
        except Exception:
            pass
        time.sleep(0.12)
    try:
        return int(_user32.GetForegroundWindow() or 0) == hwnd
    except Exception:
        return False


def is_foreground(hwnd):
    """只读判定：当前前台窗口是否就是 hwnd。

    _force_foreground 的判定逻辑抽成独立函数，供「弹窗守卫」等在做点击前的安全门控用——
    目标窗口没在前台就不点（多开轮转里后台号的点击会被吞/点歪，铁律：绝不在后台号瞎点）。"""
    try:
        if not hwnd:
            return False
        return int(_user32.GetForegroundWindow() or 0) == int(hwnd)
    except Exception:
        return False


# ---- 识别前自动激活 ----
# 用户拍板 2026-09-26：任务识别图标前，若窗口不在前台先激活再抓图。
# 原因：mss 抓的是屏幕真实像素，窗口被盖住时抓到的不是本窗口画面，模板识别会认错/认不到。
# 多开轮询后台号画面时会频繁切前台（闪动略多、速率略慢），故提供总开关可一键关掉：
# 任务建 ctx 时按 config.grab_auto_activate 接线（core/context.py），默认开。
_GRAB_AUTO_ACTIVATE = True


def set_grab_auto_activate(enabled):
    """配置「识别抓图前先确保前台」总开关。True=抓图前若非前台先 activate（失败返回 None，
    宁缺勿错）；False=照旧直抓（多开轮询省前台切换时用）。"""
    global _GRAB_AUTO_ACTIVATE
    _GRAB_AUTO_ACTIVATE = bool(enabled)


def set_dpi_aware():
    """让脚本按真实像素工作，避免 Win 缩放(125%/150%)导致坐标错位。进程级，调一次即可。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class GameWindow:
    """对一个游戏窗口的封装。"""

    def __init__(self, title_substr, offset=(0, 0)):
        self.title_substr = title_substr
        self.offset = tuple(offset)
        self._win = None

    # ---- 查找与激活 ----
    def locate(self):
        """按标题关键字找窗口，找到返回 True。"""
        candidates = []
        for w in gw.getAllWindows():
            if _match_basic(w, self.title_substr):
                candidates.append(w)
        if not candidates:
            self._win = None
            return False
        candidates.sort(key=lambda x: (not x.isMinimized, x.width * x.height), reverse=True)
        self._win = candidates[0]
        return True

    @property
    def found(self):
        return self._win is not None

    @property
    def title(self):
        return self._win.title if self._win else ""

    def bind(self, win):
        """直接绑定一个已找到的窗口对象（多开枚举用），返回 self。
        绑定后 rect()/activate() 等都作用在这个固定窗口上，不再自动选最大。"""
        self._win = win
        return self

    def rect(self):
        """[left, top, width, height]（屏幕绝对坐标，已叠加 offset）。未定位/窗口已关返回 None。"""
        if not self._win:
            return None
        try:
            return [self._win.left + self.offset[0], self._win.top + self.offset[1],
                    self._win.width, self._win.height]
        except Exception:
            # 绑定的窗口被关闭后，访问 .left/.width 会抛异常（win32 句柄失效）。
            return None

    def activate(self):
        """把本窗口切到前台并【校验确实成功】，成功返回 True、失败返回 False。

        关键：多开时若没真正切到前台就点击，点击会落在后台号上被吞/点歪。故这里用 _force_foreground
        （AttachThreadInput 绕过焦点抢占锁 + GetForegroundWindow 校验 + 重试），调用方据返回值决定是否点击。"""
        if not self._win:
            return False
        try:
            if self._win.isMinimized:
                self._win.restore()
                time.sleep(0.15)
        except Exception:
            pass
        try:
            hwnd = self._win._hWnd
        except Exception:
            hwnd = None
        if not hwnd:
            # 拿不到句柄时退回 pygetwindow 的 activate（尽力而为）
            try:
                self._win.activate()
                time.sleep(0.2)
                return True
            except Exception:
                return False
        ok = _force_foreground(hwnd)
        time.sleep(0.15 if ok else 0.05)
        return ok

    def is_foreground(self):
        """只读判定：本窗口当前是否在前台（activate 的校验逻辑抽出，供弹窗守卫等安全门控用）。
        后台号不做任何点击（点击会被吞/点歪），故守卫等介入前先查它。"""
        if not self._win:
            return False
        try:
            hwnd = self._win._hWnd
        except Exception:
            return False
        if not hwnd:
            return False
        return is_foreground(hwnd)

    def _occluded(self, rect):
        """抽查 grab 矩形内 5 个点，判定该区域是否被别的窗口遮住（用户拍板 2026-09-26「方案A」）。
        WindowFromPoint 是纯只读顶层窗口查询，不发输入不抢焦点；
        采样点取中心+四角（内缩若干像素，避开边框圆角）。判定规则：
          - 某点查不到/越屏/取到别的窗口 → 一律按「被挡」返回 True（保守：宁肯多切一次前台）；
          - 全部采样点都属于本窗口顶层（GA_ROOT 比对，兼容客户端子窗口）→ 画面完整可见，返回 False。
        目的：多开后台号多半只是「没焦点」而画面完全可见，识别不必为它反复抢焦点。"""
        if not self._win:
            return True
        try:
            hwnd = self._win._hWnd
        except Exception:
            return True
        if not hwnd:
            return True
        root = _user32.GetAncestor(hwnd, GA_ROOT)
        if not root:
            return True
        x0, y0, w, h = rect
        if w <= 0 or h <= 0:
            return True
        ins = max(1, min(12, w // 6, h // 6))
        pts = [(x0 + ins, y0 + ins), (x0 + w - ins, y0 + ins),
               (x0 + ins, y0 + h - ins), (x0 + w - ins, y0 + h - ins),
               (x0 + w // 2, y0 + h // 2)]
        for px, py in pts:
            wf = _user32.WindowFromPoint(ctypes.wintypes.POINT(px, py))
            if not wf:
                return True
            if _user32.GetAncestor(wf, GA_ROOT) != root:
                return True
        return False

    def grab_screen(self, rect, activate=None):
        """任务识别抓图的统一入口：先确保读到的画面是本窗口的，返回 BGR 图；抓不到/被停用返回 None。

        rect: 屏幕绝对 [left, top, w, h]（用 region_to_screen_rect 换好的）。
        activate: 本调用是否先切前台；默认 None=按全局开关 _GRAB_AUTO_ACTIVATE（config.grab_auto_activate），
        显式 True/False 可单点覆盖。

        用户拍板 2026-09-26「识别图标时窗口被挡住先激活再识别」：mss 抓的是屏幕真实像素，
        窗口被盖住时画面是别的窗口的，模板会认错/认不到。规则（方案A，遮挡感知，同日升级）：
          · 已在台（is_foreground 快判）→ 直接抓，零额外开销（该号自己的回合/单开几乎不加耗时）；
          · 不在台 → 先 WindowFromPoint 抽查 grab 矩形 5 点判定是否真被遮：
              - 画面完整可见（多开不重叠排布的后台号常态）→ 直接抓，不抢焦点、零切台；
              - 真被遮 → 再 activate()（含校验重试），激活失败返回 None——宁可跳过本次识别，
                也不用可能被遮的错误画面去匹配（再误点）；
          · 全局开关关掉时完全跳过激活，保持旧行为。
        GUI 侧的抓图（标定向导/窗口缩略图）不走这里，用模块级 grab()（框选时窗口本身就可见）。"""
        if rect is None:
            return None
        if activate is None:
            activate = _GRAB_AUTO_ACTIVATE
        if activate and not self.is_foreground():
            if self._occluded(rect):
                if not self.activate():
                    return None
        return grab(rect)

    def grab_window(self, region=None, activate=None):
        """按窗口（或窗口内 [x,y,w,h] 区域）抓图的便捷入口：内部换算成屏幕矩形后走 grab_screen。"""
        rect = self.region_to_screen_rect(region) if region else self.rect()
        return self.grab_screen(rect, activate=activate)

    def resize_to(self, w, h, move_to=None):
        """把窗口尺寸还原到 [w, h]（可选 move_to=(left,top) 一并复位位置）。
        成功(尺寸误差≤4px)返回 True；否则返回 False（游戏锁分辨率档位时 resize 会被忽略）。
        窗口失效/异常也返回 False。"""
        if not self._win:
            return False
        try:
            if self._win.isMinimized:
                self._win.restore()
            self._win.resizeTo(int(w), int(h))
            if move_to is not None:
                self._win.moveTo(int(move_to[0]), int(move_to[1]))
            time.sleep(0.12)
            r = self.rect()
            if r is None:
                return False
            if abs(r[2] - int(w)) > 4 or abs(r[3] - int(h)) > 4:
                # 差太多再试一次（个别窗口首帧未跟上）
                self._win.resizeTo(int(w), int(h))
                time.sleep(0.12)
                r = self.rect()
                if r is None:
                    return False
            return abs(r[2] - int(w)) <= 4 and abs(r[3] - int(h)) <= 4
        except Exception:
            return False

    # ---- 坐标换算 ----
    def region_to_screen_rect(self, region):
        """窗口内 [x,y,w,h] -> 屏幕绝对 [left,top,w,h]。"""
        r = self.rect()
        if r is None or not region:
            return None
        return [r[0] + region[0], r[1] + region[1], region[2], region[3]]

    def region_center_screen(self, region):
        """窗口内 [x,y,w,h] 的中心点 -> 屏幕绝对 (x,y)。"""
        sr = self.region_to_screen_rect(region)
        if sr is None:
            return None
        return (sr[0] + sr[2] // 2, sr[1] + sr[3] // 2)


# ---- 多窗口枚举与目标选择（多开/选择窗口基础特性）----
def locate_all(title_substr, offset=(0, 0), max_n=0):
    """枚举所有标题含 title_substr、非最小化的窗口，按屏幕位置排序后各包一个 GameWindow 返回。

    用于「选择窗口/多开」：用户把多个号并排摆在桌面上，这里把它们稳定地认成 号1/号2/号3…
    排序规则：先按上边缘分行（每 120px 一带），同一行内按左边缘左→右——和肉眼「从左到右数」一致。
    max_n>0 时最多取前 max_n 个。找不到返回空列表。
    """
    found = []
    for w in gw.getAllWindows():
        try:
            if _match_basic(w, title_substr) and not w.isMinimized:
                found.append(w)
        except Exception:
            continue
    found.sort(key=lambda x: (int(x.top) // 120, int(x.left)))
    if max_n and max_n > 0:
        found = found[:max_n]
    out = []
    for gi, w in enumerate(found):
        gwin = GameWindow(title_substr, offset).bind(w)
        gwin._g_index = gi           # 全局序号：全部检测窗口里左→右第几个（供任务日志显示真实「号N」）
        out.append(gwin)
    return out


def global_no(w, fallback):
    """窗口的真实号数（1 起）：优先用 locate_all 给的全局序号 _g_index；拿不到时退回 fallback+1。
    单开选了号2 → 返回 2，而不是"选中列表里的第1个"。"""
    gi = getattr(w, "_g_index", None)
    return (gi + 1) if isinstance(gi, int) and gi >= 0 else fallback + 1


def _hwnd_of(w):
    """取 GameWindow / pygetwindow 窗口对象的句柄（int）；取不到返回 None。"""
    try:
        raw = getattr(w, "_win", None) or w
        h = getattr(raw, "_hWnd", None)
        return int(h) if h else None
    except Exception:
        return None


def snapshot_hwnds():
    """当前所有顶层窗口的句柄集合（启动客户端「前」拍一张）。

    登录任务靠它认「哪个窗口是刚启动出来的」——多开时桌面上已有一堆号，按标题找最大那个
    会挑到旧号的窗口上去。返回 set[int]（拿不到句柄的窗口忽略）。"""
    out = set()
    for w in gw.getAllWindows():
        h = _hwnd_of(w)
        if h:
            out.add(h)
    return out


def wait_new_game_window(before, title_substr, offset=(0, 0), timeout=60.0, poll=0.5,
                         accept_any=False, min_size=(400, 300), should_stop=None, sleep=None):
    """等一个「启动前还不存在」的窗口出现（按 hwnd 差集认新窗口），返回 GameWindow 或 None。

    before: 启动前的 snapshot_hwnds()。
    accept_any: True 时，只要出现一个「不在 before 里、非最小化、够大(≥min_size)」的窗口就认下它，
        用于客户端登录窗标题与 window_title 不一致的情况；多个候选取面积最大的那个。
    should_stop: 可打断的回调（True=停止，立即返回 None）。sleep: 可打断的睡眠（默认 time.sleep）。"""
    end = time.time() + max(0.0, float(timeout))
    while True:
        if should_stop is not None and should_stop():
            return None
        for w in locate_all(title_substr, offset):
            if _hwnd_of(w) not in before:
                return w
        if accept_any:
            cands = []
            for w in gw.getAllWindows():
                if _hwnd_of(w) in before:
                    continue
                try:
                    if w.isMinimized or w.width < min_size[0] or w.height < min_size[1]:
                        continue
                except Exception:
                    continue
                cands.append(w)
            if cands:
                cands.sort(key=lambda x: x.width * x.height, reverse=True)
                return GameWindow(title_substr, offset).bind(cands[0])
        if time.time() >= end:
            return None
        if sleep is not None:
            sleep(poll)
        else:
            time.sleep(poll)


def resolve_targets(title_substr, offset, targets):
    """按 targets 配置从 locate_all 结果里选出要操作的窗口列表（纯函数，供任务与 GUI 共用）。

    targets 结构见 config.DEFAULT_CONFIG["targets"]：
      - 单开(multi=False)：返回 [第 single_index 个窗口]（序号越界自动回退 0）。
      - 多开(multi=True) ：按 multi_indices 选子集（空=自动跟踪全部，此时才受 max_windows 上限）。
        手动勾选的一组序号是权威，不再被 max_windows 截断（勾 5 个就跑 5 个）。
    找不到任何窗口返回 []。
    """
    targets = targets or {}
    wins = locate_all(title_substr, offset)
    if not wins:
        return []
    if targets.get("multi"):
        idxs = targets.get("multi_indices") or []
        if idxs:
            sel = [wins[i] for i in idxs if 0 <= i < len(wins)]
        else:                             # 空=自动全部：只有这个兜底分支受 max_windows 限制
            sel = wins
        if not sel:                       # 选中的序号全失效 → 兜底用全部
            sel = wins
        cap = targets.get("max_windows", 0)
        if cap and cap > 0 and not idxs:
            sel = sel[:cap]
        return sel
    i = targets.get("single_index", 0)
    if not (isinstance(i, int) and 0 <= i < len(wins)):
        i = 0
    return [wins[i]]


def window_at_point(title_substr, offset, x, y):
    """返回屏幕坐标 (x,y) 落在其内的游戏窗口（标定时按「框在哪个号上」定位参照窗口，不必激活）。
    多个窗口重叠都含该点时，优先当前前台窗口，否则取面积最小（最贴合）的那个。找不到返回 None。"""
    cands = []
    for w in locate_all(title_substr, offset):
        r = w.rect()
        if r and r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3]:
            cands.append((w, r))
    if not cands:
        return None
    try:
        fg = int(_user32.GetForegroundWindow() or 0)
    except Exception:
        fg = 0
    if fg:
        for w, _r in cands:
            try:
                if int(w._win._hWnd) == fg:
                    return w
            except Exception:
                pass
    cands.sort(key=lambda wr: wr[1][2] * wr[1][3])   # 面积最小=最贴合
    return cands[0][0]


def restore_targets_size(title_substr, offset, targets, base_size):
    """把当前选中的目标窗口（单开1个/多开多个）逐个还原到 base_size=[w,h]。

    复用 resolve_targets 选窗，保证和任务实际操作的是同一批号。操作每个号前先 activate()
    切前台再 resize。返回 (ok_count, total, actual_sizes)：
      - ok_count : 成功还原(尺寸误差≤4px)的号数
      - total    : 选中的号数
      - actual_sizes : 各号 resize 后的实际 [w,h]（窗口失效为 None），供上层判断是否真生效。
    base_size 非法(空/非两元素)时返回 (0, 0, [])。
    """
    if not base_size or len(base_size) < 2:
        return (0, 0, [])
    w, h = int(base_size[0]), int(base_size[1])
    wins = resolve_targets(title_substr, offset, targets)
    ok = 0
    actual = []
    for win in wins:
        win.activate()
        success = win.resize_to(w, h)
        r = win.rect()
        actual.append([r[2], r[3]] if r else None)
        if success:
            ok += 1
    return (ok, len(wins), actual)


def work_area():
    """返回可用工作区（排除任务栏）矩形 [left, top, width, height]；取不到回退全屏。
    供「调整窗口」按屏幕可用区域排布多开号：第一排贴屏幕顶、最后一行贴任务栏、第5个居中。"""
    try:
        rect = ctypes.wintypes.RECT()
        # SPI_GETWORKAREA = 0x0030：得到「排除任务栏后的工作区」屏幕矩形。
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        left, top = int(rect.left), int(rect.top)
        return [left, top, int(rect.right) - left, int(rect.bottom) - top]
    except Exception:
        try:
            sw = int(ctypes.windll.user32.GetSystemMetrics(0))   # SM_CXSCREEN
            sh = int(ctypes.windll.user32.GetSystemMetrics(1))   # SM_CYSCREEN
            return [0, 0, sw, sh]
        except Exception:
            return [0, 0, 1920, 1080]


def arrange_windows(cfg, app):
    """把所选窗口（最多 5 个）调到基准尺寸并按 2列×2行 排布在屏幕上——
    第一排（窗口1、2）上边贴屏幕/工作区顶、第二排（窗口3、4）下边贴任务栏，第 5 个窗口放屏幕正中；
    最左列距左侧留 arrange_left_margin 像素空白（config 可改，默认 100）。
    用 resolve_targets 保证和任务实操是同一批号；点数不足 5 就只排排到的。
    返回调整成功数；不负责页面刷新（调用方自己 refresh）。"""
    base = (cfg.get("targets") or {}).get("base_size")
    if not base or len(base) < 2:
        app.toast("请先设置基准尺寸（在窗口列表点「设为基准」）")
        return 0
    bw, bh = int(base[0]), int(base[1])
    title = cfg.get("window_title", "梦幻西游")
    offset = cfg.get("window_offset", [0, 0])
    targets = cfg.get("targets", {})
    try:
        wins = resolve_targets(title, offset, targets)[:5]
    except Exception:
        wins = []
    if not wins:
        app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
        return 0
    wa = work_area()
    wx, wy, ww, wh = wa[0], wa[1], wa[2], wa[3]
    margin = int(cfg.get("arrange_left_margin", 100))
    col1 = wx + margin             # 最左列距左侧留 margin 空白（config.arrange_left_margin 可调）
    col2 = wx + ww - bw            # 右列贴工作区右
    row1y = wy                   # 第一排上边贴屏幕/工作区顶
    row2y = wy + wh - bh         # 第二排（最后一行）下边贴任务栏(=工作区底)
    cx = wx + ww // 2
    cy = wy + wh // 2
    slots = [
        (col1, row1y),           # 号1 上左
        (col2, row1y),           # 号2 上右
        (col1, row2y),           # 号3 下左
        (col2, row2y),           # 号4 下右
        (cx - bw // 2, cy - bh // 2),   # 号5 屏幕正中
    ]
    ok = 0
    for w, (x, y) in zip(wins, slots):
        w.activate()
        if w.resize_to(bw, bh, move_to=(x, y)):
            ok += 1
    app._game_connected = None     # 尺寸/位置变了，强制下次 tick 刷新药丸
    extra = ", 第 5 个居中放屏幕正中" if len(wins) >= 5 else ""
    tip = ("已调整 {}/{} 个窗口到基准尺寸 {}×{}，并按 2列×2行 排布（第1排贴顶、最后1排贴任务栏"
           "{extra}）。分辨率锁档的号可能未移动。").format(ok, len(wins), bw, bh, extra=extra)
    app.toast(tip)
    return ok


# ---- 截图 ----
# mss 用 GDI，srcdc 等句柄存在「线程本地」里：在 A 线程建的实例不能在 B 线程用，
# 否则报 'object has no attribute srcdc'。任务跑在后台线程，故每个线程各持一份。
_tls = threading.local()


def _get_sct():
    sct = getattr(_tls, "sct", None)
    if sct is None:
        sct = mss.mss()
        _tls.sct = sct
    return sct


def grab(rect):
    """截取屏幕矩形 [left, top, w, h]，返回 OpenCV BGR 图像。"""
    left, top, w, h = rect
    raw = _get_sct().grab({"left": int(left), "top": int(top),
                           "width": int(w), "height": int(h)})
    img = np.array(raw)  # BGRA
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
