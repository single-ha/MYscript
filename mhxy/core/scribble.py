# -*- coding: utf-8 -*-
"""
拟人化「描摹临摹」：从绘制区画面识别出【线条图案】，生成沿图案骨架的描摹路径并拖动。

用于游戏里「按住鼠标沿图案描一遍」这类校验（如梦幻拓印）。不同于 fill_region 的无脑盖满整区：
拓印判定「完成度>60%」，描到图案**外**会拉低完成度，所以必须先认出图案像素、只沿笔画本身走。

做法：截屏绘制区 → 形态学分离前景（线条）掩码 → Zhang-Suen 细化得 1px 骨架 →
贪心把骨架拆成尽量少的连续笔画路径 → 每条笔画按住、沿骨架采样点走 + 微幅横向锯齿
（笔迹加宽但绝不拣起额外笔画），模拟人手沿字一笔画完。
走完返回 True；画面里认不出图案返回 False（调用方转手动兜底，绝不乱描）。

本模块只依赖 Mouse + numpy/cv2 —— 任何任务遇到此类临摹都可复用。
"""

import random
import time

import cv2
import numpy as np


def trace_pattern(mouse, rect, frame_bgr, speed=1.0, lateral=3, sample_step=5,
                  min_area_ratio=0.005, label="绘制区"):
    """识别 rect（屏幕绝对坐标 [x,y,w,h]）内 frame_bgr（该矩形截屏）里的线条图案并沿其描摹。

    - 先调 _extract_strokes 分离前景线条、拆成连续笔画路径（已按最长优先排序）。
    - 每笔：移进该笔起点（松开状态）→ 按住 → 沿骨架点序微幅锯齿移动 → 松开；两笔之间歇手。
    - lateral: 锯齿横向偏移上限（像素），把笔迹加宽、提高覆盖又不增加起笔。
    - sample_step: 骨架采样间距（像素），越小轨迹越贴骨架（也更慢）。
    返回 True=描完；False=没能识别出图案（调用方应转手动兜底）。
    """
    x, y, w, h = rect
    if w <= 4 or h <= 4 or frame_bgr is None or frame_bgr.size == 0:
        return False
    strokes = _extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label)
    if not strokes:
        return False
    # 最长笔优先：核心笔画先描（万一中途被停，覆盖占比已经最大）
    strokes.sort(key=len, reverse=True)
    for stroke in strokes:
        if not stroke:
            continue
        # 起笔（松开状态移进起点附近）
        s0 = stroke[0]
        mouse.human_move(x + s0[0], y + s0[1], speed=speed)
        time.sleep(random.uniform(0.05, 0.15))
        mouse.hold()
        try:
            _trace_stroke(mouse, x, y, stroke, lateral=lateral,
                          sample_step=sample_step, speed=speed)
        finally:
            try:
                mouse.release()
            except Exception:
                pass
        time.sleep(random.uniform(0.15, 0.4))     # 笔画之间歇手，像人换笔
    return True


def has_pattern(frame_bgr, min_area_ratio=0.005, label="绘制区"):
    """只判断画面里能否认出图案笔画（不描）。供「演练·只识别」/预检复用。"""
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    return bool(_extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label))


# ----------------------------------------------------------------------
# 骨架提取：灰度 → 形态学开闭(去网格噪点/接断笔) → 阈值分离前景 → Zhang-Suen 细化
# ----------------------------------------------------------------------
def _extract_strokes(frame, min_area_ratio=0.005, label="绘制区"):
    """从绘制区截屏分离出前景线条并细化成骨架笔画路径。

    图案可能亮于或暗于背景（不赌颜色）：亮/暗两个方向都试，选骨架总长更可观的一侧。
    返回笔画路径列表，每条 = [(col,row), ...]（帧内相对坐标、按行走顺序）；
    认不出图案返回 [] 空列表。
    """
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    h, w = gray.shape
    total = h * w

    # 形态学：开操作去网格/噪点，闭操作接回断笔
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))

    best, best_len = [], 0
    for invert in (False, True):
        # Otsu 二值：图案侧视 invert 而定（亮侧 or 暗侧）
        _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        if invert:
            bw = cv2.bitwise_not(bw)
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_open)
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_close)

        # 前景占比过滤：太满(整张是高亮盘面)或太空(几乎没有图案)都视为无效侧
        fg = int((bw > 0).sum())
        if not (0.002 * total < fg < 0.7 * total):
            continue
        # 减去过小的孤立噪点（面积 < 0.05% 画幅）
        bw = _drop_tiny(bw, max(1, int(total * 0.0005)))
        skel = _skeletonize(bw)
        strokes = _skeleton_to_strokes(skel)
        if strokes:
            total_len = sum(len(s) for s in strokes)
            if total_len > best_len:
                best, best_len = strokes, total_len
    return best


def _drop_tiny(bw, min_area):
    """移除面积 < min_area 的连通域（网格残片/孤立噪点）。"""
    nb, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    if nb <= 1:
        return bw
    keep = np.zeros_like(bw)
    for i in range(1, nb):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[labels == i] = 255
    return keep


def _skeletonize(bw):
    """Zhang-Suen 细化：1px 骨架。输入/输出均 0/255 二值图。"""
    img = (bw > 0).astype(np.uint8)
    img = cv2.copyMakeBorder(img, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    changed = True
    while changed:
        changed = False
        for sub_pass in (0, 1):
            mark = []
            h, _w = img.shape
            for r in range(1, h - 1):
                for c in range(1, _w - 1):
                    if img[r, c] == 0:
                        continue
                    p = img[r - 1:r + 2, c - 1:c + 2]
                    p2 = int(p[0, 1]); p3 = int(p[0, 2]); p4 = int(p[1, 2])
                    p5 = int(p[2, 2]); p6 = int(p[2, 1]); p7 = int(p[2, 0])
                    p8 = int(p[1, 0]); p9 = int(p[0, 0])
                    n = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
                    transitions = sum(int(a) and not int(b) for a, b in
                                      ((p2, p3), (p3, p4), (p4, p5), (p5, p6),
                                       (p6, p7), (p7, p8), (p8, p9), (p9, p2)))
                    m2 = p2 * p4 * p6
                    m4 = p4 * p6 * p8
                    if 2 <= n <= 6 and transitions == 1:
                        if sub_pass == 0 and not m2 and not m4:
                            mark.append((r, c))
                        elif sub_pass == 1 and not (p2 * p4 * p8) and not (p2 * p6 * p8):
                            mark.append((r, c))
            for (r, c) in mark:
                img[r, c] = 0
                changed = True
    return img[1:-1, 1:-1]


# ----------------------------------------------------------------------
# 骨架 → 连续笔画路径：从端点出发贪心前进（可选分支），走到尽为止
# ----------------------------------------------------------------------
def _skeleton_to_strokes(skel):
    """把 1px 骨架拆成若干条连续路径。每条 = [(col,row) 按行走顺序]。

    确定性分割（不引入随机，笔画数可复现、便于满足游戏「限制笔画数」）：
    - 总是从端点(8邻域邻点数==1)起笔；没有端点(纯环)就从任意点起。
    - 前进到分支点优先选「与当前走向夹角最小」的邻点（顺着笔画走，尽量一笔贯通主干）。
    - 结果笔画数 ≈ 端点数/2。"""
    pts = set((int(c), int(r)) for r, c in np.argwhere(skel > 0))
    if not pts:
        return []
    strokes = []
    unvisited = set(pts)
    moves = [(-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)]

    def neighbors(cx, cy):
        return [(cx + dx, cy + dy) for dx, dy in moves
                if (cx + dx, cy + dy) in unvisited]

    while unvisited:
        start = None
        for (cx, cy) in unvisited:
            if len(neighbors(cx, cy)) == 1:
                start = (cx, cy)
                break
        if start is None:
            start = sorted(unvisited)[0]     # 全环：从最左上点起
        path = [start]
        unvisited.discard(start)
        while True:
            cx, cy = path[-1]
            cands = neighbors(cx, cy)
            if not cands:
                break
            if len(path) >= 2:
                # 沿上一个步进方向走（构成直线延伸）
                dirx = path[-1][0] - path[-2][0]
                diry = path[-1][1] - path[-2][1]
                along = [(x, y) for x, y in cands
                         if (x - cx) * dirx + (y - cy) * diry > 0]   # 夹角<90°=顺走
                cands = along or cands
            nxt = min(cands, key=lambda p: (p[0], p[1]))     # 确定性
            path.append(nxt)
            unvisited.discard(nxt)
        if len(path) >= 2:
            strokes.append(path)
    return strokes


# ----------------------------------------------------------------------
# 沿一条笔画拖动：分段采样 + 横向锯齿（笔迹加宽、不额外起笔）
# ----------------------------------------------------------------------
def _trace_stroke(mouse, ox, oy, stroke, lateral=3, sample_step=5, speed=1.7):
    """按住状态下，沿 stroke（帧内相对坐标）依次移动；每采样点加横向偏移仿笔宽。
    ox/oy 为绘制区屏幕绝对左上角，把帧内坐标换算回屏幕坐标。"""
    prev = None
    for i in range(0, len(stroke), max(1, sample_step)):
        if prev is not None:
            # 横向锯齿：垂直当前段走向随机偏多少，笔迹加宽
            dx = stroke[min(i + 2, len(stroke) - 1)][0] - prev[0]
            dy = stroke[min(i + 2, len(stroke) - 1)][1] - prev[1]
            ln = max(1.0, (dx * dx + dy * dy) ** 0.5)
            nx, ny = -dy / ln, dx / ln          # 法向
            l = random.uniform(-lateral, lateral)
            sx = prev[0] + dx + nx * l
            sy = prev[1] + dy + ny * l
        else:
            sx, sy = stroke[i][0], stroke[i][1]
        mouse.human_move(ox + sx, oy + sy, speed=speed)
        prev = (sx, sy)
        time.sleep(random.uniform(0.004, 0.014) / speed if speed else 0.004)