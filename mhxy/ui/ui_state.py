# -*- coding: utf-8 -*-
"""
界面状态判定（与玩法无关，所有任务通用）。

提供两个判定：
  · is_main_screen()——主界面判定：在窗口画面里找「商城图标」模板，找到=当前是主界面
    （商城/活动图标在「通用」页→标定（公共区域）里标，tasks.shared.templates，见 MAIN_ICON_TPL_KEYS）。
  · is_present()——「标志是否在画面里」判定：任务级通用（如 battle_flag 战斗标志），
    运镖/宝图/秘境等都共用这一份，不再各任务复制 _present。

用法（任务内）：
    from ..ui import ui_state
    if ui_state.is_main_screen(cfg, ctx.window):
        已回主界面…
    if ui_state.is_present(scene, self.flags, "battle_flag", threshold):
        正在战斗（战斗期暂停「静止/结束」判定）…
"""

from ..core import config as cfg_mod
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