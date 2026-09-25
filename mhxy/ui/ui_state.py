# -*- coding: utf-8 -*-
"""
界面状态判定（与玩法无关，所有任务通用）。

提供三个判定/动作：
  · is_main_screen()——主界面判定：在窗口画面里找「商城图标」模板，找到=当前是主界面
    （商城/活动图标在「通用」页→标定（公共区域）里标，tasks.shared.templates，见 MAIN_ICON_TPL_KEYS）。
  · is_present()——「标志是否在画面里」判定：任务级通用（如 battle_flag 战斗标志），
    运镖/宝图/秘境等都共用这一份，不再各任务复制 _present。
  · back_to_main_screen()——「确保回到主界面」：已在主界面直接返回，否则反复按 ESC 关面板
    直到回主界面；回不去/无法判断时返回 False/None 交调用方兜底。

用法（任务内）：
    from ..ui import ui_state
    if ui_state.is_main_screen(cfg, ctx.window):
        已回主界面…
    if ui_state.is_present(scene, self.flags, "battle_flag", threshold):
        正在战斗（战斗期暂停「静止/结束」判定）…
    if ui_state.back_to_main_screen(cfg, ctx.window):
        已确保回到主界面（原不在主界面则逐层 ESC 关掉弹窗/活动/背包）…
"""

import time

from ..core import config as cfg_mod
from ..core import input as input_mod
from ..core import vision
from ..core import window as win_mod


def is_present(scene, flags, flag_key, threshold):
    """判断「标志模板是否出现在场景里」（如 battle_flag 战斗标志），任务级通用的 _present。

    scene: 窗口截图（BGR ndarray），可为 None。
    flags: 任务用 _load_flags 加载好的标志模板字典（key → 已加载模板，可为 None）。
    flag_key: 标志名（战斗标识等用共享键 battle_flag，模板在 tasks.shared.templates）。
    threshold: 模板匹配相似度门槛（各任务 loop.match_threshold，默认 0.85）。

    返回：
      True  = 场景里找到该标志模板（如正在战斗）
      False = 没找到，或 scene/flags/该标志模板缺失——一律当「不在」处理，不抛错。

    与 is_main_screen 的区别：is_main_screen 自己读 cfg 拿共享模板并抓图；
    这里由任务把已抓的场景和已加载的 flags 传进来（任务循环里通常已抓好场景，
    不必再抓一次），只做「匹配判定」这一层。
    """
    if scene is None or not flags:
        return False
    tpl = flags.get(flag_key)
    if tpl is None:
        return False
    return vision.match(scene, tpl, threshold) is not None


def is_main_screen(cfg, window, threshold=0.8, region=None):
    """判断「当前界面是否主界面」：能在窗口画面里找到商城图标（tasks.shared.templates.shop_icon）即主界面。

    cfg: 整份配置（App.cfg / TaskContext.cfg）。
    window: core/window.GameWindow 实例（要已 locate/bind 到一个窗口）。
    threshold: 模板匹配相似度门槛（默认 0.8）。
    region: 可选窗口内 [x,y,w,h]——只在那一块里找（不传=整窗找）。
    area: 同一窗口里活动列表区等地方不可能出商城图标时，可传小区域加速。

    返回：
      True  = 找到商城图标 → 主界面
      False = 没找到（但商城图标已标定、读取/抓图正常）→ 非主界面
      None  = 无法判断（未标定商城图标 / 窗口没定位 / 抓图失败）——调用方自行兜底，别当「不是主界面」处理。
    """
    try:
        shared = ((cfg or {}).get("tasks", {}) or {}).get("shared", {}) or {}
        tpl_path = (shared.get("templates") or {}).get("shop_icon")
    except AttributeError:
        return None
    if not tpl_path:
        return None
    tpl = vision.load_template(tpl_path)
    if tpl is None:
        return None
    rect = window.region_to_screen_rect(region) if region else window.rect()
    if rect is None:
        return None
    scene = win_mod.grab(rect)
    if scene is None:
        return None
    return vision.match(scene, tpl, threshold) is not None


def find_popup_close(cfg, window, threshold=0.85):
    """弹窗守卫的探测原语：在当前窗口画面里找活动/公告弹窗右上角的「×」关闭按钮
    （tasks.shared.templates.popup_close，可选共享模板，在「通用」页「标定（公共区域）」里标）。

    返回：命中 = 按钮中心的【屏幕绝对坐标】(cx, cy)；未命中/不可用 = None。
    未标模板 / 窗口未定位 / 抓图失败 一律返回 None——调用方（base._defuse_popup）据此静默跳过，
    绝不当作「有弹窗」去按 Esc 乱来（和 is_main_screen 的 None 语义一致）。
    """
    try:
        shared = ((cfg or {}).get("tasks", {}) or {}).get("shared", {}) or {}
        tpl_path = (shared.get("templates") or {}).get("popup_close")
    except AttributeError:
        return None
    if not tpl_path:
        return None
    tpl = vision.load_template(tpl_path)
    if tpl is None:
        return None
    rect = window.rect()
    if rect is None:
        return None
    scene = win_mod.grab(rect)
    if scene is None:
        return None
    hit = vision.match(scene, tpl, threshold)
    if hit is None:
        return None
    cx, cy, _score = hit
    return (rect[0] + int(cx), rect[1] + int(cy))


def back_to_main_screen(cfg, window, max_tries=6, settle=0.6, threshold=0.8):
    """确保回到主界面：已在主界面直接返回 True，否则反复按 ESC（SendInput）关面板直到回主界面。

    cfg: 整份配置（透传给 is_main_screen）。
    window: core/window.GameWindow 实例。
    max_tries: 最多按几层 ESC 还回不去就当失败（默认 6）。
    settle: 每次按 ESC 后等画面落定的秒数。
    threshold: 主界面判定门槛（透传 is_main_screen，默认 0.8）。

    返回：
      True  = 已回主界面（含一开始就在）
      False = 连按 max_tries 次 ESC 还没在主界面（多半有模态弹窗关不掉、或商城图标没认到）
      None  = 无法判断（shop_icon 未标定 / 窗口没定位 / 抓图失败）——与 is_main_screen 一致，
              不去盲按 ESC，交调用方兜底。
    """
    keyboard = input_mod.Mouse()
    for _ in range(max(1, max_tries)):
        st = is_main_screen(cfg, window, threshold=threshold)
        if st is True:
            return True
        if st is None:
            return None
        # 不在主界面 → 按 ESC 关掉当前面板一层，等画面落定再判
        keyboard.press_key("esc")
        time.sleep(max(0.1, settle))
    return False