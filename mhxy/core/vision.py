# -*- coding: utf-8 -*-
"""
图像识别。模板匹配 + 兼容中文路径的图片读写。与具体玩法无关，所有任务通用。
"""

import os

import numpy as np
import cv2

from .config import PROJECT_ROOT


def _abspath(path):
    if os.path.isabs(path):
        return path
    return str(PROJECT_ROOT / path)


def load_template(path):
    """读取模板图（兼容中文路径）。失败返回 None。"""
    p = _abspath(path)
    if not os.path.exists(p):
        return None
    data = np.fromfile(p, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_image(path, img):
    """保存图片（兼容中文路径）。"""
    p = _abspath(path)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ext = os.path.splitext(p)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(p)
    return ok


def match(scene_bgr, template_bgr, threshold):
    """
    在 scene 里找 template。命中返回 (cx, cy, score)，cx/cy 为命中中心相对 scene 左上角；
    未命中返回 None。
    """
    if scene_bgr is None or template_bgr is None:
        return None
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return None
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    if max_val >= threshold:
        return (max_loc[0] + tw // 2, max_loc[1] + th // 2, float(max_val))
    return None


def match_multi(scene_bgr, template_bgr, threshold, max_hits=32, nms_iou=0.4, sort_origin_top_left=True):
    """在 scene 里找 template 的【所有】命中点（多个同款按钮——如进副本列表里几个长一样的「进入」）。

    - 用 TM_CCOEFF_NORMED 全图扫，取所有 >= threshold 的局部峰值（非极大值抑制 NMS 防同一按钮重复圈中）
    - 每个命中返回 (cx, cy, score)，相对 scene 左上角
    - sort_origin_top_left=True 时按【先 y 后 x（先上后下、同行先左后右）】排序——即按行列排好，
      界面依此「第 N 个命中=第 N 个同款按钮」来定位。
    - 没超 threshold 返回空列表 []。
    """
    if scene_bgr is None or template_bgr is None:
        return []
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return []
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)

    # 局部极大值（邻居不比自己大才算峰值），再把低于阈值的滤掉
    ys, xs = np.where(res >= threshold)
    if ys.size == 0:
        return []
    peaks = []
    for y, x in zip(ys, xs):
        v = float(res[y, x])
        y0, y1 = max(0, y - 1), min(res.shape[0], y + 2)
        x0, x1 = max(0, x - 1), min(res.shape[1], x + 2)
        if v >= float(res[y0:y1, x0:x1].max()):
            peaks.append((x + tw / 2, y + th / 2, v))
    if not peaks:
        return []

    # NMS：命中框按得分从高到低，凡与已接受结果重叠过大(IoU 超 nms_iou)就丢弃
    hits = sorted(peaks, key=lambda p: p[2], reverse=True)
    accepted = []
    for (cx, cy, s) in hits:
        ok = True
        for (ax, ay, _) in accepted:
            ix = min(cx + tw / 2, ax + tw / 2) - max(cx - tw / 2, ax - tw / 2)
            iy = min(cy + th / 2, ay + th / 2) - max(cy - th / 2, ay - th / 2)
            if ix > 0 and iy > 0:
                inter = ix * iy
                union = tw * th * 2 - inter
                if union > 0 and inter / union > nms_iou:
                    ok = False
                    break
        if ok:
            accepted.append((int(cx), int(cy), s))
        if len(accepted) >= max_hits:
            break
    if sort_origin_top_left:
        # 先 y（上→下）、再 x（左→右），即按行列排好
        accepted.sort(key=lambda p: (p[1], p[0]))
    else:
        accepted.sort(key=lambda p: p[2], reverse=True)
    return accepted


def frame_diff(a, b):
    """两帧平均像素绝对差。形状不一致返回大值（视为仍在变化/不静止）。
    用于「画面是否静止」和「列表滚不动了=到顶/到底」判定。"""
    if a is None or b is None or a.shape != b.shape:
        return 999.0
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def best_score(scene_bgr, template_bgr):
    """诊断用：返回 template 在 scene 里的【最高匹配分】(不卡阈值)及命中中心 (score, (cx, cy))。
    尺寸不符/空图返回 (0.0, None)。用来判断「模板根本不在画面里(分很低)」还是「在画面里但阈值太高」。"""
    if scene_bgr is None or template_bgr is None:
        return (0.0, None)
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return (0.0, None)
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return (float(max_val), (max_loc[0] + tw // 2, max_loc[1] + th // 2))
