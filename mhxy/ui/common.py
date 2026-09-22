# -*- coding: utf-8 -*-
"""
GUI 跨页共用件：既含小型 UI 小组件（Card/Pill/load_thumb），也含全局急停热键的
常量与纯函数（App 主页与设置页 SettingsPage 共用）。从 app.py 拆出，避免页面文件互相 import 宿主。

只放「被多个页面/App 复用」的东西；某个页面独有的辅助函数留在该页面文件里。
"""

import os
import ctypes

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod


# ----------------------------------------------------------------------
# 全局快捷键（GetAsyncKeyState 轮询，无需额外依赖，游戏在前台也能触发）
# ----------------------------------------------------------------------
# 可选键 -> Windows 虚拟键码。只放不易和游戏冲突的功能键。
HOTKEY_VK = {
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74, "F6": 0x75,
    "F7": 0x76, "F8": 0x77, "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    "Pause": 0x13, "ScrollLock": 0x91, "Home": 0x24, "End": 0x23,
    "Insert": 0x2D, "Delete": 0x2E, "`(~)": 0xC0,
}
HOTKEY_NAMES = list(HOTKEY_VK.keys())

# 全局【急停】热键 = 若干修饰键 + 一个主键（上面的功能键），存成 "ctrl+alt+F12" 这样的字符串。
# 默认 Ctrl+Alt+F12：组合键比单键更不易和游戏内操作误撞，按一下立刻停止一切正在跑的任务。
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
DEFAULT_STOP_HOTKEY = "ctrl+alt+F12"


def _parse_stop_hotkey(s):
    """'ctrl+alt+F12' -> (修饰键VK列表, 主键VK)。主键缺失/不认得返回 ([], None)。修饰键名不区分大小写。"""
    mods, key_vk = [], None
    for part in str(s or "").split("+"):
        p = part.strip()
        if not p:
            continue
        low = p.lower()
        if low in MODIFIER_VK:
            mods.append(MODIFIER_VK[low])
            continue
        for name, vk in HOTKEY_VK.items():
            if name.lower() == low:
                key_vk = vk
                break
    return mods, key_vk


def _compose_stop_hotkey(ctrl, alt, shift, key):
    """(ctrl,alt,shift 勾选 + 主键名) -> 'ctrl+alt+F12' 字符串（修饰键在前、主键在后）。"""
    parts = []
    if ctrl:
        parts.append("ctrl")
    if alt:
        parts.append("alt")
    if shift:
        parts.append("shift")
    parts.append(key)
    return "+".join(parts)


def _split_stop_hotkey(s):
    """'ctrl+alt+F12' -> (ctrl:bool, alt:bool, shift:bool, 主键名)，供设置 UI 回填；主键认不得回退 F12。"""
    mods, key_vk = _parse_stop_hotkey(s)
    key = "F12"
    for name, vk in HOTKEY_VK.items():
        if vk == key_vk:
            key = name
            break
    return (MODIFIER_VK["ctrl"] in mods, MODIFIER_VK["alt"] in mods,
            MODIFIER_VK["shift"] in mods, key)


# 失控急停：任务运行时把鼠标甩到屏幕某个角(撞到角落)即停。内部值 <-> 中文显示，off=关闭。
FAILSAFE_CORNERS = {
    "top_right": "右上角", "top_left": "左上角",
    "bottom_right": "右下角", "bottom_left": "左下角", "off": "关闭",
}
FAILSAFE_LABELS = list(FAILSAFE_CORNERS.values())
FAILSAFE_VALUE_OF = {v: k for k, v in FAILSAFE_CORNERS.items()}
DEFAULT_FAILSAFE = "top_right"


def _in_failsafe_corner(cx, cy, corner, margin=3):
    """光标 (cx,cy) 是否撞到所选屏幕角（off/未知一律 False）。用主屏像素尺寸判断。"""
    if corner == "off" or cx is None or cy is None:
        return False
    try:
        u = ctypes.windll.user32
        W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)   # SM_CXSCREEN / SM_CYSCREEN
    except Exception:
        return False
    left, right = cx <= margin, cx >= W - 1 - margin
    top, bottom = cy <= margin, cy >= H - 1 - margin
    return {
        "top_left": left and top, "top_right": right and top,
        "bottom_left": left and bottom, "bottom_right": right and bottom,
    }.get(corner, False)


def _vk_down(vk):
    """该虚拟键当前是否按下（最高位为按下状态）。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


# ----------------------------------------------------------------------
# 通用小组件
# ----------------------------------------------------------------------
def Card(master, **kw):
    """一张卡片容器。"""
    opts = dict(fg_color=T.SURFACE, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER)
    opts.update(kw)
    return ctk.CTkFrame(master, **opts)


def Pill(master, fonts):
    """状态小药丸（圆角标签）。"""
    lbl = ctk.CTkLabel(master, text="", font=fonts["small"], corner_radius=T.RADIUS_PILL,
                       fg_color=T.SURFACE_2, text_color=T.TEXT_DIM,
                       padx=12, pady=4)
    return lbl


class Tooltip:
    """悬停提示浮窗：鼠标进入控件后延时弹出小窗，移出即销毁。

    只绑 <Enter>/<Leave>，不抢控件自身事件；浮窗宽度按 small 字体实测文字决定（不写死），
    用于把页面里的说明文本收成悬停提示。一控件一个实例，销毁时随窗口被销毁，无需显式清理。
    """

    _delay_ms = 400
    _max_w = 380
    _min_w = 140

    def __init__(self, widget, text, fonts):
        self.widget = widget
        self.text = text
        self.fonts = fonts
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)

    def _on_enter(self, _event):
        self._cancel_pending()
        self._after_id = self.widget.after(self._delay_ms, self._popup)

    def _on_leave(self, _event):
        self._cancel_pending()
        self._hide()

    def _cancel_pending(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _popup(self):
        self._after_id = None
        self._hide()
        if not self.text:
            return
        try:
            root = self.widget.winfo_toplevel()
            f = self.fonts.get("small")
            try:
                w = int(f.measure(self.text)) + 24
            except Exception:
                w = self._max_w
            w = max(self._min_w, min(self._max_w, w))
            tip = ctk.CTkToplevel(root)
            tip.overrideredirect(True)
            tip.attributes("-topmost", True)
            tip.configure(fg_color=T.SURFACE_2)
            lbl = ctk.CTkLabel(tip, text=self.text, font=f, text_color=T.TEXT,
                               fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM,
                               wraplength=w - 24, justify="left", anchor="w")
            lbl.pack(padx=12, pady=8)
            tip.update_idletasks()
            pw, ph = tip.winfo_reqwidth(), tip.winfo_reqheight()
            x, y = root.winfo_pointerx() + 14, root.winfo_pointery() + 14
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            if x + pw > sw:
                x = max(0, x - pw - 28)
            if y + ph > sh:
                y = max(0, y - ph - 28)
            tip.geometry(f"{pw}x{ph}+{x}+{y}")
            tip.lift()
            self._tip = tip
        except Exception:
            self._tip = None

    def _hide(self):
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# bind_wraplength 现统一定义在 theme 里（window_picker / calibrate_dialog 也复用，避免循环依赖）。
def bind_wraplength(label, padding=4):
    """自动换行助手，见 theme.bind_wraplength（此处只是转发，避免各页面直接再 import theme 的写法差异）。"""
    return T.bind_wraplength(label, padding)


def load_thumb(template_rel, thumbs_list, max_h=40):
    """把模板图按高缩放成缩略图 CTkImage，引用 append 进 thumbs_list 防 GC，兼容中文/打包路径。
    不存在/失败返回 None。多页复用（SniperPage 清单、队长ID 库与行内按钮都调它）。"""
    if not template_rel:
        return None
    try:
        from PIL import Image
        path = template_rel if os.path.isabs(template_rel) else str(cfg_mod.PROJECT_ROOT / template_rel)
        if not os.path.exists(path):
            return None
        try:
            img = Image.open(path)
            img.load()
        except Exception:
            # 中文/异常路径兜底：走 cv2 imdecode（np.fromfile，兼容中文路径）读出再转回 PIL。
            # watchlist 装备图是 templates/<中文名>.png，个别环境 PIL.open 不保险，缺这条会“有图却显示不出”。
            import cv2
            from ..core import vision
            arr = vision.load_template(template_rel)
            if arr is None:
                return None
            img = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))
        w, h = img.size
        scale = max_h / max(1, h)
        size = (max(1, int(w * scale)), max_h)
        cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        thumbs_list.append(cimg)
        return cimg
    except Exception:
        return None


# ----------------------------------------------------------------------
# 公共区域（全任务共用标定）：任务页做就绪提示时，若公共区域没标齐，指路到「通用」页
# ----------------------------------------------------------------------
def shared_region_hint(regions, task_name):
    """返回公共区域未标齐的提示文本（regions 应为已叠加 tasks.shared 的 task_config）。
    已标齐返回 ""。"""
    missing = [k for k in cfg_mod.TASK_SHARED_REQ.get(task_name, ()) if not regions.get(k)]
    if not missing:
        return ""
    names = "、".join(cfg_mod.SHARED_REGION_LABELS.get(k, k) for k in missing)
    return f"；公共区域「{names}」未标定 —— 请到「通用」页点「标定（公共区域）」（全任务共用）"


def shared_template_hint(templates, task_name):
    """返回公共共享模板未标齐的提示文本（templates 应为已叠加 tasks.shared 模板的 task_config）。
    战斗标识/小闹钟等已统一移到「通用」页「标定（公共区域）」标定（见 TASK_SHARED_TPL_REQ）。已标齐返回 ""。"""
    missing = [k for k in cfg_mod.TASK_SHARED_TPL_REQ.get(task_name, ()) if not templates.get(k)]
    if not missing:
        return ""
    label = "、".join(cfg_mod.SHARED_TPL_LABELS.get(k, k) for k in missing)
    return f"；公共模板「{label}」未标定 —— 请到「通用」页点「标定（公共区域）」框选（全任务共用）"


def calib_status(*, regions, templates, task_name,
                 region_keys=(), tpl_keys=(), tpl_opt_keys=(), label="标定", extra="", extra_ready=None):
    """统一的任务页「标定」状态文本格式。

    regions/templates 应为已叠加 tasks.shared 的 task_config（见 core/config.task_config）。
    region_keys/tpl_keys 只传任务**自身**的必标区域/模板（公共区域不传，由内部按
    TASK_SHARED_REQ 自动补判并附加提示）；tpl_opt_keys 传**可选**模板键——缺失不影响就绪，
    仅作提示「另有可选 n 未标」（可选模板与必选模板应来自同一 CALIBRATION spec 的
    required_templates/optional_templates 推导，勿手写）。
    label 默认「标定」；extra 为可选追加说明（如 sanjie 的答题点位缺失提示，用「，」开头）。
    extra_ready 可选：调用方的额外就绪条件（如组队/答题点位），给定时会并入最终显示的
    「✓ 可运行/（还需标定）」判定；不给则只看自身必标＋公共区域。
    返回 (ready, text, color)：
      - ready：自身必标（不算可选）＋公共区域＋(extra_ready 若给定) 都齐。
      - text：统一格式「{label}：必要模板 x/y（另有可选 n 未标）{extra}　✓ 可运行/（还需标定）
         ＋公共区域未标定提示」。无区域时省略「必要区域」段、无模板时省略「必要模板」段；
        可选模板全标齐则省略「另有可选」段。
      - color：已就绪=SUCCESS（绿）/ 还需标定=WARN（黄），供标定状态标签着色的主题令牌。"""
    region_keys = list(region_keys)
    tpl_keys = list(tpl_keys)
    tpl_opt_keys = [k for k in tpl_opt_keys if k not in tpl_keys]
    rdone = sum(1 for k in region_keys if regions.get(k))
    tdone = sum(1 for k in tpl_keys if templates.get(k))
    tdone_opt = sum(1 for k in tpl_opt_keys if templates.get(k))
    r_ok = (not region_keys) or rdone == len(region_keys)
    t_ok = (not tpl_keys) or tdone == len(tpl_keys)
    shared_msg = shared_region_hint(regions, task_name)
    shared_tpl_msg = shared_template_hint(templates, task_name)
    ready = r_ok and t_ok and not shared_msg and not shared_tpl_msg
    if extra_ready is not None:
        ready = ready and bool(extra_ready)
    seg = []
    if region_keys:
        seg.append(f"必要区域 {rdone}/{len(region_keys)}")
    if tpl_keys:
        seg.append(f"必要模板 {tdone}/{len(tpl_keys)}")
    opt_txt = ""
    if tpl_opt_keys and tdone_opt < len(tpl_opt_keys):
        opt_txt = f"（另有可选 {len(tpl_opt_keys) - tdone_opt} 未标）"
    body = "，".join(seg) if seg else "无必标项"
    text = (f"{label}：{body}{opt_txt}{extra}"
            + ("　✓ 可运行" if ready else "　（还需标定）")
            + shared_msg + shared_tpl_msg)
    color = T.SUCCESS if ready else T.WARN
    return ready, text, color


# ----------------------------------------------------------------------
# 从任务 CALIBRATION spec 推导「必选 / 可选」集合（单一来源）
# ----------------------------------------------------------------------
def required_regions(spec):
    """从任务的 CALIBRATION spec 取「非可选区域」键（第 4 元素为真 = 可留空，如 scene 主识别区）。"""
    return [t[0] for t in spec.get("regions", []) if not _row_optional(t)] if spec else []


def required_templates(spec):
    """从任务的 CALIBRATION spec 取「必选模板」键（第 4 元素为真 = 可选，不参与就绪）。"""
    return [t[0] for t in spec.get("templates", []) if not _row_optional(t)] if spec else []


def optional_templates(spec):
    """从任务的 CALIBRATION spec 取「可选模板」键（第 4 元素为真；缺失不影响就绪）。"""
    return [t[0] for t in spec.get("templates", []) if _row_optional(t)] if spec else []


def _row_optional(t):
    """spec 行是否标「可选」：第 4 个及以上元素存在且为真。"""
    return len(t) >= 4 and t[3]


# ----------------------------------------------------------------------
# 组队设置（多人任务公用）：读写共享 tasks.teaming 命名空间
# ----------------------------------------------------------------------
def teaming_ns(cfg):
    """取 tasks.teaming 命名空间 dict（不存在则给空 dict，别改动主 cfg 结构）。"""
    return (cfg.get("tasks", {}).get("teaming", None) or {})


def teaming_ready(team_tc):
    """组队标定是否齐全（区域内+模板均满足）。team_tc 是 tasks.teaming 的 dict。"""
    from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
    treg = team_tc.get("regions", {})
    ttpl = team_tc.get("templates", {})
    rdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
    tdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
    return (rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_REQUIRED_TEMPLATES))


def teaming_summary(team_tc):
    """组队标定摘要文本：'区域 x/y，模板 a/b'。未标齐全返回带未完提示的字符串。"""
    from ..core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
    treg = team_tc.get("regions", {})
    ttpl = team_tc.get("templates", {})
    rdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
    tdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
    if rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_REQUIRED_TEMPLATES):
        return f"组队标定：齐全 ✓"
    return f"组队标定：区域 {rdone}/{len(TEAM_REQUIRED_REGIONS)}，模板 {tdone}/{len(TEAM_REQUIRED_TEMPLATES)}"


class TeamSettingsCard(ctk.CTkFrame):
    """多人任务共用的「组队设置」卡片：「已组队」开关（队长/队长ID 统一在通用页选）。
    「跑完解散队伍」开关已迁到「日常一条龙」页（见 DailyPage.var_disband，存共享 tasks.teaming.auto_disband）。
    控件读写共享 tasks.teaming 命名空间，故各多人任务页只需嵌这一张卡、零重复；
    on_change 回调让宿主页刷新自己的标定状态。"""

    def __init__(self, master, app, fonts, on_change=None):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = fonts
        self.on_change = on_change

        # —— 已组队 开关 ——
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", anchor="w")
        self.switch_skip = ctk.CTkSwitch(row, text="已组队（跳过组队，直接开刷）", font=fonts["body"],
                                         progress_color=T.ACCENT, command=self._on_skip)
        self.switch_skip.pack(anchor="w")

    # —— 读共享 teaming 命名空间 ——
    def _read(self):
        return teaming_ns(self.app.cfg)

    def _write(self, **kw):
        cfg = cfg_mod.load_config()
        tc = cfg["tasks"].setdefault("teaming", {})
        tc.update(kw)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _fire(self):
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # —— 从配置文件回灌开关（宿主页 refresh 时调用）——
    def refresh(self):
        tc = self._read()
        (self.switch_skip.select if tc.get("skip_team", False) else self.switch_skip.deselect)()

    # —— 读当前组队设置（供宿主页拼标定行/运行前回写）——
    def values(self):
        return {
            "skip_team": bool(self.switch_skip.get()),
        }

    # —— 回调：写入共享 teaming ——
    def _on_skip(self):
        skip = bool(self.switch_skip.get())
        self._write(skip_team=skip)
        self._fire()
