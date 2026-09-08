# -*- coding: utf-8 -*-
"""
拟人化「区域描摹填扫」：按住左键、多条带抖动的贝塞尔行扫把一块矩形区域盖满。

用于游戏里「按住鼠标沿图案描一遍」这类临摹校验（图案随机但绘制区固定）：
不识别图案形状，只按坐标把绘制区密铺描一遍，靠「覆盖面达标」通过宽松校验。
所有轨迹走 input.Mouse 的拟人化移动（贝塞尔+加减速+落点抖动）；行距/起点/方向/速度/停顿随机化，
避免机械扫描感。若游戏校验严格到必须贴合图案轮廓，需升级为「截图案→提轮廓→沿轮廓描」。

本模块与玩法无关，只依赖 Mouse —— 任何任务遇到此类临摹都可复用。
"""

import random
import time


def fill_region(mouse, rect, passes=2, spacing=8, speed=1.7, label="绘制区"):
    """在屏幕绝对坐标 rect=(x,y,w,h) 内按住左键扫 passes 遍把区域盖满。

    - 每遍是一笔：「移进区域内→按住→连续行扫折返→松开」，再落到新落点起下一笔。
    - 行扫主方向每遍随机（横/竖），起边/走向随机，行距带抖动（spacing 为基准像素）。
    - speed 传给 human_move：填扫较长，适当提速避免全程拖太久（仍带加减速）。
    全程不抛异常：被外部强停/窗口失效时最多松开即返回。
    """
    x, y, w, h = rect
    if w <= 4 or h <= 4:
        return
    if passes is None or passes < 1:
        passes = 1
    if spacing is None or spacing < 3:
        spacing = 8
    # 先移到区域内随机落点（落点在按住之前，用于起笔定位）
    mouse.human_move(x + random.uniform(0.15, 0.85) * w,
                     y + random.uniform(0.15, 0.85) * h, speed=speed)
    time.sleep(random.uniform(0.05, 0.15))
    for _ in range(int(passes)):
        mouse.hold()
        try:
            _sweep(mouse, x, y, w, h,
                   horizontal=random.random() < 0.5, spacing=spacing, speed=speed)
        finally:
            try:
                mouse.release()
            except Exception:
                pass
        time.sleep(random.uniform(0.2, 0.6))     # 笔画之间歇手，像人分两笔
        mouse.human_move(x + random.uniform(0.2, 0.8) * w,
                         y + random.uniform(0.2, 0.8) * h, speed=speed)
        time.sleep(random.uniform(0.04, 0.12))


def _sweep(mouse, x, y, w, h, horizontal, spacing, speed):
    """一笔：从选定边缘出发，按 spacing 行距折返行扫直到越过对边，端点过扫防留缝。"""
    overshoot = max(6, int(spacing * 0.9))
    if horizontal:
        min_edge, max_edge = y - overshoot, y + h + overshoot
    else:
        min_edge, max_edge = x - overshoot, x + w + overshoot
    step_down = random.random() < 0.5              # 向前推进方向
    cur = (min_edge if step_down else max_edge) + random.uniform(0, spacing)
    forward = random.random() < 0.5                # 首行走向（左右/上下）
    while min_edge <= cur <= max_edge:
        if horizontal:
            p0 = (x - overshoot if forward else x + w + overshoot, cur)
            p1 = (x + w + overshoot if forward else x - overshoot,
                  cur + random.uniform(-spacing * 0.2, spacing * 0.2))
        else:
            p0 = (cur, y - overshoot if forward else y + h + overshoot)
            p1 = (cur + random.uniform(-spacing * 0.2, spacing * 0.2),
                  y + h + overshoot if forward else y - overshoot)
        mouse.human_move(p0[0], p0[1], speed=speed)
        mouse.human_move(p1[0], p1[1], speed=speed)
        forward = not forward
        cur += random.uniform(spacing * 0.9, spacing * 1.15) * (1 if step_down else -1)