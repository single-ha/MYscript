# -*- coding: utf-8 -*-
"""
拟人化「描摹临摹」：从绘制区画面识别出【线条图案】，生成沿图案骨架的描摹路径并拖动。

用于游戏里「按住鼠标沿图案描一遍」这类校验（如梦幻拓印）。不同于 fill_region 的无脑盖满整区：
拓印判定「完成度>60%」，描到图案**外**会拉低完成度，所以必须先认出图案像素、只沿笔画本身走。

做法（2026-10-17 重构，见 CLAUDE.md 约束与 memory）：
  1. **局部对比二值化**（不再用整幅 Otsu）：背景 = 中值滤波 **并上** 形态学闭/开运算。
     闭运算(深墨)/开运算(浅线)把小于核的明暗块整体抹掉来估计纸面，能覆盖**实心/粗笔画**；
     中值滤波补足窄于核的细线。`d=|bg-gray|`，阈值 `T=max(t_min, frac·局部std)`。
     图案是「深墨画浅纸」还是「浅线画深底」都支持（双极性都试）。
     这一步是关键：拓印图案对比度低、又嵌在对话框里，整幅 Otsu 会选中面板/边框而不是墨迹；
     且若只用中值滤波，实心/粗笔画会被描成空心轮廓（骨架沿边界走）。
  2. **剔除框线**：丢掉贴边连通域、以及 bbox 占画幅 ≥90% 宽/高的连通域（对话框边框、分割线、滚动条）。
  3. **形态学**：开操作去网格噪点、闭操作接回断笔；再按「相对画幅面积」丢掉小碎块（min_area_ratio 生效）。
  4. Zhang-Suen 细化得 1px 骨架（先缩放到长边 ≤skel_max_dim 再算，坐标还原）；再**剪掉短毛刺**
     （spur_len），否则碎枝会把笔画数撑爆。
  5. 贪心拆成尽量少的连续笔画路径；丢掉短碎片、以及「近似直线且很长」的笔画（残留的框线/网格）；
     最后把笔画数压到 ≤max_strokes（默认 14，游戏要求「少于15笔」）——超了就合并最近端点。
  6. **选侧判据**：不是「骨架总长最大」（框线总是最长），而是「可信度评分」——笔画条数适中、
     不跨画幅、笔迹占比合理者胜。

返回 True=描完；认不出图案返回 False（调用方转手动兜底，绝不乱描）。
本模块只依赖 Mouse + numpy/cv2 —— 任何任务遇到此类临摹都可复用。
"""

import os
import random
import time
import datetime

import cv2
import numpy as np


def debug_dir():
    """识别失败留证目录（captures_debug/拓印描摹/；vision.save_image 会自动再落 YYYY-MM-DD 子目录）。"""
    from . import vision
    return str(vision.get_captures_dir(debug=True) / "拓印描摹")


def debug_tag(prefix):
    """给一组调试图打时间戳标签（同一次留证的 5 张图共用一个 tag、便于对照）。"""
    ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
    return f"{prefix}_{ts}"


# 提取参数默认值（任务侧可用 config 覆盖，见 tasks.tuoying.loop.tuoying_*）
DEFAULTS = {
    "bg_kernel": 41,       # 背景估计核（像素）：中值滤波 + 形态学闭/开运算共用；越大越能覆盖更粗的实心笔画
    "contrast_min": 12.0,  # 绝对对比下限（灰度差）；低于此不算笔画
    "contrast_frac": 0.25,  # 相对局部std的比例阈值：T=max(contrast_min, frac*std)
    "min_area_ratio": 0.0004,  # 连通域面积下限 / 画幅面积（滤孤立噪点；相对画幅故随框大小自适应）
    "skel_max_dim": 480,   # 骨架化前的长边上限（超了先缩放再算，坐标还原）
    "drop_straight": True,  # 是否丢弃「近似完美直线且几乎横贯整幅」的笔画（框线/网格残留）
    "straight_ratio": 0.99,  # 直线度阈值：|端点距|/路径长 ≥ 此值视为直线（放很严，别误删图案斜笔）
    "straight_len_frac": 0.8,  # 且长度 ≥ 此比例·max(画幅w,h) 才算「框线」丢弃
    "spur_len": 6,         # 骨架毛刺剪枝长度（像素）：短于此的分叉/噪点枝先剪掉，避免碎成一大堆笔画
    "min_stroke_len": 4,   # 单条笔画最短采样点数：短于此的碎片当噪点丢弃
    "max_strokes": 14,     # 笔画数上限（游戏要求「少于15笔」）；超了就合并最近的端点，直到 < 上限
}

# config 键 → params 键（tasks.tuoying.loop.tuoying_xxx）
_CONFIG_KEYMAP = {
    "tuoying_bg_kernel": "bg_kernel",
    "tuoying_ink_contrast": "contrast_min",
    "tuoying_ink_frac": "contrast_frac",
    "tuoying_min_area_ratio": "min_area_ratio",
    "tuoying_skel_max_dim": "skel_max_dim",
    "tuoying_drop_straight": "drop_straight",
    "tuoying_straight_ratio": "straight_ratio",
    "tuoying_straight_len_frac": "straight_len_frac",
    "tuoying_spur_len": "spur_len",
    "tuoying_stroke_min_len": "min_stroke_len",
    "tuoying_max_strokes": "max_strokes",
}


def params_from_loop(loop):
    """从 tasks.tuoying.loop（或任意含 tuoying_* 键的 dict）抽取出提取参数，供 trace_pattern 的 params。
    缺键走 DEFAULTS；传 None/空则返回 None（等于用 DEFAULTS）。"""
    if not loop:
        return None
    out = {}
    for ck, pk in _CONFIG_KEYMAP.items():
        v = loop.get(ck)
        if v is not None:
            out[pk] = v
    return out or None


def debug_enabled(loop):
    """是否在识别失败时落调试图（tasks.tuoying.loop.tuoying_save_debug，默认 True）。"""
    if not loop:
        return True
    v = loop.get("tuoying_save_debug")
    return True if v is None else bool(v)


def polarity_from_loop(loop, default="dark"):
    """图案极性：深墨画浅底="dark"（拓印默认）/ 浅线画深底="light" / 两侧都试="auto"。
    （tasks.tuoying.loop.tuoying_polarity，非法值回退 default。）"""
    if not loop:
        return default
    v = loop.get("tuoying_polarity")
    return v if v in ("dark", "light", "auto") else default


def trace_pattern(mouse, rect, frame_bgr, speed=1.0, lateral=3, sample_step=5,
                  min_area_ratio=None, label="绘制区", polarity="auto", params=None):
    """识别 rect（屏幕绝对坐标 [x,y,w,h]）内 frame_bgr（该矩形截屏）里的线条图案并沿其描摹。

    - 先调 _extract_strokes 分离前景线条、拆成连续笔画路径（已按最长优先排序）。
    - 每笔：移进该笔起点（松开状态）→ 按住 → 沿骨架点序微幅锯齿移动 → 松开；两笔之间歇手。
    - lateral: 锯齿横向偏移上限（像素），把笔迹加宽、提高覆盖又不增加起笔。
    - sample_step: 骨架采样间距（像素），越小轨迹越贴骨架（也更慢）。
    - polarity: "dark"=深墨画浅底 / "light"=浅线画深底 / "auto"=两侧都算、按可信度选（默认）。
    - params: 覆盖 DEFAULTS 的提取参数字典（来自 config）。
    返回 True=描完；False=没能识别出图案（调用方应转手动兜底）。
    """
    x, y, w, h = rect
    if w <= 4 or h <= 4 or frame_bgr is None or frame_bgr.size == 0:
        return False
    strokes = _extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label,
                               polarity=polarity, params=params)
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


def has_pattern(frame_bgr, min_area_ratio=None, label="绘制区", polarity="auto", params=None):
    """只判断画面里能否认出图案笔画（不描）。供「演练·只识别」/预检复用。"""
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    return bool(_extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label,
                                 polarity=polarity, params=params))


def extract_debug(frame_bgr, min_area_ratio=None, label="绘制区", polarity="auto", params=None):
    """调试入口：返回 (strokes, meta)。meta 含 mask/skel/polarity/kept 供渲染诊断图。"""
    if frame_bgr is None or frame_bgr.size == 0:
        return [], {"mask": None, "skel": None, "polarity": None}
    p = _merge_params(params)
    return _extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label,
                            polarity=polarity, params=p, want_meta=True)


# ----------------------------------------------------------------------
# 图案提取主流程
# ----------------------------------------------------------------------
def _merge_params(params):
    p = dict(DEFAULTS)
    if params:
        for k, v in params.items():
            if v is not None and k in p:
                p[k] = v
    return p


def _extract_strokes(frame, min_area_ratio=None, label="绘制区", polarity="auto",
                     params=None, want_meta=False):
    """从绘制区截屏分离出前景线条并细化成骨架笔画路径。

    返回笔画路径列表，每条 = [(col,row), ...]（帧内相对坐标、按行走顺序）；认不出图案返回 []。
    want_meta=True 时返回 (strokes, meta)。
    """
    p = _merge_params(params)
    if min_area_ratio is not None:
        p["min_area_ratio"] = min_area_ratio
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    total = float(h * w)
    # 背景估计：中值滤波 **并上** 形态学闭/开运算。
    #   中值滤波适合「窄于核的细线」；但对**实心/粗笔画**，核内背景被笔画自身主导，
    #   d=bg-gray 只在笔画边界非零 → mask 成空心轮廓 → 骨架沿边界走（2026-10-17 实测踩坑：
    #   拓印「从」是实心粗笔画，中值滤波只描出边界）。闭运算(深墨)/开运算(浅线)把小于核的
    #   明暗块整体抹掉来估计纸面，能覆盖实心/粗笔画，故两路取并（深墨取 max、浅线取 min）。
    kb = int(p["bg_kernel"]) | 1
    kb = max(5, min(kb, (min(h, w) // 2) * 2 + 1))
    gf = gray.astype(np.float32)
    bg_med = cv2.medianBlur(gray, kb).astype(np.float32)
    ell = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kb, kb))
    # 局部标准差（boxFilter 快速估计），用于自适应阈值
    m1 = cv2.boxFilter(gf, -1, (31, 31))
    m2 = cv2.boxFilter(gf * gf, -1, (31, 31))
    std = np.sqrt(np.clip(m2 - m1 * m1, 0, None))
    thr = np.maximum(float(p["contrast_min"]), float(p["contrast_frac"]) * std)

    if polarity == "dark":
        bg = np.maximum(bg_med, cv2.morphologyEx(gray, cv2.MORPH_CLOSE, ell).astype(np.float32))
        candidates = [("dark", np.clip(bg - gf, 0, 255))]
    elif polarity == "light":
        bg = np.minimum(bg_med, cv2.morphologyEx(gray, cv2.MORPH_OPEN, ell).astype(np.float32))
        candidates = [("light", np.clip(gf - bg, 0, 255))]
    else:
        bg_dark = np.maximum(bg_med, cv2.morphologyEx(gray, cv2.MORPH_CLOSE, ell).astype(np.float32))
        bg_light = np.minimum(bg_med, cv2.morphologyEx(gray, cv2.MORPH_OPEN, ell).astype(np.float32))
        candidates = [("dark", np.clip(bg_dark - gf, 0, 255)),
                      ("light", np.clip(gf - bg_light, 0, 255))]

    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    min_area = max(1, int(total * float(p["min_area_ratio"])))

    best_strokes, best_score, best_meta = [], -1e9, None
    for name, d in candidates:
        bw = ((d > thr) * 255).astype(np.uint8)
        if int((bw > 0).sum()) < 4:
            continue
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_open)
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_close)
        bw = _drop_frame_components(bw)                    # 剔框线/分割线/贴边
        bw = _drop_tiny(bw, min_area)                      # 剔小碎块（相对画幅）
        if int((bw > 0).sum()) < min_area:
            continue
        skel, scale = _skeletonize_capped(bw, int(p["skel_max_dim"]))
        # 剪枝：去掉短毛刺/噪点枝（游戏限制「笔画数<15」，不剪会被碎枝撑爆笔画数）
        skel = _prune_spurs(skel, max(1, int(round(float(p["spur_len"]) * (scale or 1.0)))))
        strokes = _skeleton_to_strokes(skel)
        strokes = _scale_strokes(strokes, 1.0 / scale if scale else 1.0)
        strokes = [s for s in strokes if len(s) >= int(p["min_stroke_len"])]
        if p["drop_straight"]:
            strokes = _drop_straight_strokes(strokes, (h, w),
                                             float(p["straight_ratio"]),
                                             float(p["straight_len_frac"]))
        strokes = _reduce_strokes(strokes, int(p["max_strokes"]))
        if not strokes:
            continue
        score = _stroke_confidence(strokes, (h, w), total)
        if want_meta:
            meta = {"mask": bw, "skel": skel, "polarity": name,
                    "scale": scale, "score": score}
        else:
            meta = None
        if score > best_score:
            best_strokes, best_score, best_meta = strokes, score, meta
        if polarity in ("dark", "light"):
            break

    if want_meta:
        return best_strokes, (best_meta or {"mask": None, "skel": None, "polarity": None,
                                            "scale": 1.0, "score": best_score})
    return best_strokes


def _drop_frame_components(bw):
    """丢掉「横贯/纵贯画幅的细线」连通域——对话框边框、分割线、滚动条。

    注意：**不能按「贴边」判**——拓印绘制区常被标得较紧，图案笔画本身就会触到框边；
    旧版按贴边丢，把整条斜笔画连同图案一起丢了（2026-10-17 实测踩坑）。
    改为：bbox 在某方向跨 ≥90% 画幅、且是**细线**（面积/bbox 面积 < 0.5）才算框线。"""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(bw, connectivity=8)
    if n <= 1:
        return bw
    H, W = bw.shape
    keep = np.zeros_like(bw)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if w <= 0 or h <= 0:
            continue
        spans = (w >= 0.9 * W) or (h >= 0.9 * H)
        thin = area < 0.5 * w * h
        if spans and thin:
            continue                      # 跨画幅的细线 = 框/分割线
        keep[labels == i] = 255
    return keep


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


def _stroke_confidence(strokes, shape, total):
    """笔画可信度评分：越像「一个图案」越高。

    规则：笔画数 1..12 加分；每条长到「能看见」的笔画加分；跨画幅的、占比过大的减分。
    （不用总骨架长——框线总是最长，正是旧版的坑。）
    """
    h, w = shape
    diag = (h * h + w * w) ** 0.5
    score = 0.0
    n = len(strokes)
    if 1 <= n <= 12:
        score += 3.0
    elif n > 12:
        score -= (n - 12) * 1.5
    big = 0
    for s in strokes:
        L = len(s)
        if L < 6:
            continue
        big += 1
        x0, y0 = s[0]
        x1, y1 = s[-1]
        span = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        score += min(L, 200) / 20.0                       # 长一点更好认（封顶）
        if span >= 0.8 * diag:                            # 对角线级别的长线 = 框
            score -= 6.0
        if L >= 0.6 * diag:                               # 太长 = 框
            score -= 4.0
    score += min(big, 6) * 0.5
    return score


def _drop_straight_strokes(strokes, shape, ratio, len_frac):
    """丢掉「近似**完美直线**且**几乎横贯/纵贯整幅**」的笔画（残留框线/网格线）。

    判据放得很严（默认 直线度≥0.99 且 长度≥0.8·max(w,h)）：拓印图案本身常有近乎笔直的斜笔画
    （如「从」的撇/捺），松判据会把图案一起误删（2026-10-17 实测踩坑）。"""
    h, w = shape
    limit = len_frac * max(h, w)
    out = []
    for s in strokes:
        if len(s) < 8:
            out.append(s)
            continue
        (x0, y0), (x1, y1) = s[0], s[-1]
        span = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        straightness = span / max(1.0, float(len(s) - 1))
        if span >= limit and straightness >= ratio:
            continue
        out.append(s)
    return out


def _prune_spurs(skel, iters):
    """迭代剪掉骨架端点 iters 次——把短于 iters 的毛刺/噪点枝整段削掉（顺带把主笔画两端各削 iters 像素，
    可忽略）。返回 0/1 骨架（uint8）。避免碎枝被拆成一大堆笔画（游戏限制「笔画数<15」）。"""
    s = (skel > 0).astype(np.uint8).copy()
    if iters <= 0:
        return s
    k = np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8)
    for _ in range(iters):
        nb = cv2.filter2D(s, -1, k, borderType=cv2.BORDER_CONSTANT)
        ends = (s > 0) & (nb <= 1)
        if not ends.any():
            break
        s[ends] = 0
    return s


def _reduce_strokes(strokes, max_n):
    """把笔画数压到 ≤ max_n：反复把「端点距离最近」的两条并成一条（顺接，中间直连一小段）。

    游戏要求「笔画数少于15」；并接只连最近端点（通常原本就相邻/同属一个图案），故连接段很短、
    基本落在图案内。max_n<=0 视为不限制。"""
    if max_n <= 0:
        return strokes
    work = [list(s) for s in strokes if len(s) >= 2]
    while len(work) > max_n:
        best = None
        for i in range(len(work)):
            for j in range(i + 1, len(work)):
                a, b = work[i], work[j]
                for ea, rev_a in ((0, True), (-1, False)):
                    for eb, rev_b in ((0, False), (-1, True)):
                        pa, pb = a[ea], b[eb]
                        d = (pa[0] - pb[0]) ** 2 + (pa[1] - pb[1]) ** 2
                        if best is None or d < best[0]:
                            best = (d, i, j, rev_a, rev_b)
        if best is None:
            break
        _, i, j, rev_a, rev_b = best
        a = work[i][::-1] if rev_a else work[i]
        b = work[j][::-1] if rev_b else work[j]
        merged = a + b
        work = [work[k] for k in range(len(work)) if k not in (i, j)]
        work.append(merged)
    return work


def _skeletonize_capped(bw, max_dim):
    """骨架化：长边超 max_dim 先等比缩小（纯 Python 细化全分辨率会卡死），返回 (skel, scale)。"""
    h, w = bw.shape
    scale = 1.0
    if max_dim > 0 and max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        bw = cv2.resize(bw, (max(1, int(w * scale)), max(1, int(h * scale))),
                        interpolation=cv2.INTER_AREA)
    skel = _skeletonize(bw)
    return skel, scale


def _scale_strokes(strokes, factor):
    if not factor or factor == 1.0:
        return strokes
    out = []
    for s in strokes:
        if not s:
            continue
        out.append([(int(round(c * factor)), int(round(r * factor))) for (c, r) in s])
    return out


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
    step = max(1, int(sample_step))          # 配置里可能是 float（如 5.0），range 步长必须 int
    for i in range(0, len(stroke), step):
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


# ----------------------------------------------------------------------
# 调试图渲染：把提取中间结果画出来，便于人工核对（我看不了图，靠人眼闭环）
# ----------------------------------------------------------------------
_STROKE_COLORS = [
    (0, 0, 255), (0, 160, 255), (0, 220, 220), (60, 220, 60),
    (255, 200, 0), (255, 120, 0), (255, 80, 160), (200, 80, 255),
    (140, 140, 255), (255, 0, 0), (0, 255, 160), (160, 160, 0),
]


def render_debug(frame_bgr, strokes, meta=None, trace_path=False, lateral=3, sample_step=5,
                 seed=0):
    """生成一张诊断图：底色=原图，mask 半透明叠加，骨架/笔画异色描出、标起点(圆)/终点(方)、
    按长度排序编号。trace_path=True 时再画一遍「实际走笔路径」（含横向锯齿）。
    返回 BGR 图。"""
    base = frame_bgr.copy()
    meta = meta or {}
    mask = meta.get("mask")
    if mask is not None and mask.shape[:2] == base.shape[:2]:
        overlay = base.copy()
        overlay[mask > 0] = (0, 255, 0)
        base = cv2.addWeighted(overlay, 0.35, base, 0.65, 0)
    skel = meta.get("skel")
    if skel is not None:
        ys, xs = np.where(skel > 0)
        for (cx, cy) in zip(xs.tolist(), ys.tolist()):
            cv2.circle(base, (cx, cy), 1, (255, 0, 255), -1)
    rng = random.Random(seed)
    ordered = sorted(strokes, key=len, reverse=True)
    for idx, s in enumerate(ordered):
        if len(s) < 2:
            continue
        color = _STROKE_COLORS[idx % len(_STROKE_COLORS)]
        pts = [(int(c), int(r)) for (c, r) in s]
        for a, b in zip(pts, pts[1:]):
            cv2.line(base, a, b, color, 2, cv2.LINE_AA)
        cv2.circle(base, pts[0], 4, color, -1, cv2.LINE_AA)
        cv2.rectangle(base, (pts[-1][0] - 3, pts[-1][1] - 3),
                      (pts[-1][0] + 3, pts[-1][1] + 3), color, 1, cv2.LINE_AA)
        cv2.putText(base, str(idx), pts[0], cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    if trace_path:
        step = max(1, int(sample_step))
        for s in ordered:
            prev = None
            for i in range(0, len(s), step):
                if prev is not None:
                    dx = s[min(i + 2, len(s) - 1)][0] - prev[0]
                    dy = s[min(i + 2, len(s) - 1)][1] - prev[1]
                    ln = max(1.0, (dx * dx + dy * dy) ** 0.5)
                    nx, ny = -dy / ln, dx / ln
                    l = rng.uniform(-lateral, lateral)
                    sx = prev[0] + dx + nx * l
                    sy = prev[1] + dy + ny * l
                else:
                    sx, sy = s[i][0], s[i][1]
                if prev is not None:
                    cv2.line(base, (int(prev[0]), int(prev[1])),
                             (int(sx), int(sy)), (0, 0, 0), 1, cv2.LINE_AA)
                prev = (sx, sy)
    text = "polarity=%s strokes=%d" % (meta.get("polarity"), len(ordered))
    cv2.putText(base, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1, cv2.LINE_AA)
    return base


def save_debug(out_dir, tag, frame_bgr, strokes, meta=None, trace_path=True,
               lateral=3, sample_step=5):
    """把一帧的提取中间结果落盘：{tag}_frame/_mask/_skel/_strokes/_trace.png。
    返回**真实落盘路径**列表（captures_debug 下 vision 会自动插 YYYY-MM-DD 子目录，这里回传带日期的真路径）；
    任何异常都吞掉（诊断不该影响主流程）。"""
    try:
        from . import vision
        written = []
        def _save(name, img):
            raw = os.path.join(out_dir, "%s_%s.png" % (tag, name))
            if vision.save_image(raw, img):          # 内部按日期归档并自建目录
                written.append(vision.dated_path(raw))
        _save("frame", frame_bgr)
        mask = (meta or {}).get("mask")
        if mask is not None:
            _save("mask", cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR))
        skel = (meta or {}).get("skel")
        if skel is not None:
            _save("skel", cv2.cvtColor(((skel > 0) * 255).astype("uint8"), cv2.COLOR_GRAY2BGR))
        _save("strokes", render_debug(frame_bgr, strokes, meta, trace_path=False))
        if trace_path:
            _save("trace", render_debug(frame_bgr, strokes, meta, trace_path=True,
                                        lateral=lateral, sample_step=sample_step))
        return written
    except Exception:
        return []


def dump_failure(out_dir, tag, frame_bgr, params=None, polarity="auto", lateral=3, sample_step=5):
    """识别失败时的一键留证：重跑提取取 meta，落 frame/mask/skel/strokes/trace 到 out_dir。
    返回写出的文件路径列表（供日志打印）。异常吞掉。"""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return []
    try:
        strokes, meta = extract_debug(frame_bgr, params=params, polarity=polarity)
        return save_debug(out_dir, tag, frame_bgr, strokes, meta,
                          trace_path=True, lateral=lateral, sample_step=sample_step)
    except Exception:
        return []
