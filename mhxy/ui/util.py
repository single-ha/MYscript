import ctypes
import os
import customtkinter as ctk

from . import theme as T

from ..core import config as cfg_mod

# 全局【急停】热键 = 若干修饰键 + 一个主键（上面的功能键），存成 "ctrl+alt+F12" 这样的字符串。
# 默认 Ctrl+Alt+F12：组合键比单键更不易和游戏内操作误撞，按一下立刻停止一切正在跑的任务。
MODIFIER_VK = {"ctrl": 0x11, "alt": 0x12, "shift": 0x10}
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


def card(master, **kw):
    """一张卡片容器。"""
    opts = dict(fg_color=T.SURFACE, corner_radius=T.RADIUS, border_width=1, border_color=T.BORDER)
    opts.update(kw)
    return ctk.CTkFrame(master, **opts)


def pill(master):
    """状态小药丸（圆角标签）。"""
    lbl = ctk.CTkLabel(master, text="", font=T.build_fonts()["small"], corner_radius=T.RADIUS_PILL,
                       fg_color=T.SURFACE_2, text_color=T.TEXT_DIM,
                       padx=12, pady=4)
    return lbl


def in_failsafe_corner(cx, cy, corner, margin=3):
    """光标 (cx,cy) 是否撞到所选屏幕角（off/未知一律 False）。用主屏像素尺寸判断。"""
    if corner == "off" or cx is None or cy is None:
        return False
    try:
        u = ctypes.windll.user32
        W, H = u.GetSystemMetrics(0), u.GetSystemMetrics(1)  # SM_CXSCREEN / SM_CYSCREEN
    except Exception:
        return False
    left, right = cx <= margin, cx >= W - 1 - margin
    top, bottom = cy <= margin, cy >= H - 1 - margin
    return {
        "top_left": left and top, "top_right": right and top,
        "bottom_left": left and bottom, "bottom_right": right and bottom,
    }.get(corner, False)


def parse_stop_hotkey(s):
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


def vk_down(vk):
    """该虚拟键当前是否按下（最高位为按下状态）。"""
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


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
