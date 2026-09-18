# -*- coding: utf-8 -*-
"""
界面状态判定（与玩法无关，所有任务通用）。

目前提供主界面判定：is_main_screen()——在窗口画面里找「商城图标」模板，找到了=当前是主界面。
商城/活动图标在「通用」页→标定（公共区域）里标（tasks.shared.templates，见 MAIN_ICON_TPL_KEYS）。

用法（任务内）：
    from ..core import ui_state
    if ui_state.is_main_screen(cfg, ctx.window):
        已回主界面…
"""

from . import config as cfg_mod
from . import vision
from . import window as win_mod


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