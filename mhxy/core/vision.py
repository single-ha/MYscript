# -*- coding: utf-8 -*-
"""
图像识别。模板匹配 + 兼容中文路径的图片读写。与具体玩法无关，所有任务通用。
"""

import os
import time
import datetime
import json

import numpy as np
import cv2

from .config import PROJECT_ROOT, CAPTURES_DIR, CAPTURES_DEBUG_DIR


def _abspath(path):
    if os.path.isabs(path):
        return path
    return str(PROJECT_ROOT / path)


def _with_daily_dir(path):
    """captures/captures_debug 下的输出自动落到 YYYY-MM-DD 子目录。"""
    try:
        p = os.path.abspath(path)
        np = os.path.normcase(p)
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        for root in (CAPTURES_DIR, CAPTURES_DEBUG_DIR):
            r = os.path.normcase(os.path.abspath(str(root)))
            if os.path.commonpath([np, r]) == r:
                rel = os.path.relpath(p, str(root))
                return str(root / day / rel)
    except Exception:
        pass
    return path


def get_captures_dir(debug: bool = False):
    """返回截图目录：debug=True 用 captures_debug/，否则用 captures/"""
    return CAPTURES_DEBUG_DIR if debug else CAPTURES_DIR


def dated_path(path):
    """返回该路径按日期归档后的**真实**落盘路径（captures/captures_debug 下会插入 YYYY-MM-DD 子目录）。
    与 save_image 实际写入的位置一致，供日志/返回给调用方显示。"""
    return _with_daily_dir(_abspath(path))


def load_template(path):
    """读取模板图（兼容中文路径）。失败返回 None。"""
    p = _abspath(path)
    if not os.path.exists(p):
        return None
    data = np.fromfile(p, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def save_image(path, img):
    """保存图片（兼容中文路径）。"""
    p = _with_daily_dir(_abspath(path))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    ext = os.path.splitext(p)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(p)
    return ok


# 匹配失败诊断留证的限流：同（任务, 模板）秒内最多一张（防轮询轰炸）。
_DIAG_THROTTLE_SEC = 8.0
_diag_last_ts = {}


def _save_match_diagnostic(scene_bgr, template_name, task_name, threshold, window_rect, max_val):
    """匹配失败时保存诊断截图到 captures/，并打印日志。
    不受 debug_log 控制，仅关键路径（diagnose_on_miss=True）且未命中时触发。

    同一（任务, 模板）在 _DIAG_THROTTLE_SEC 秒内最多留证一张，避免轮询循环里每帧拿一张淋水账。"""
    if scene_bgr is None:
        return
    _now = time.time()
    _key = (task_name, template_name)
    if _now - _diag_last_ts.get(_key, 0.0) < _DIAG_THROTTLE_SEC:
        return
    _diag_last_ts[_key] = _now
    try:
        day = datetime.datetime.now().strftime("%Y-%m-%d")
        ts = datetime.datetime.now().strftime("%H%M%S_%f")[:-3]
        safe_template = template_name.replace("/", "_").replace("\\", "_")
        safe_task = task_name.replace("/", "_").replace("\\", "_")
        fname = f"match_fail_{safe_task}_{safe_template}_{day}_{ts}.png"
        fpath = str(CAPTURES_DIR / day / fname)
        os.makedirs(os.path.dirname(fpath), exist_ok=True)
        
        ext = ".png"
        ok, buf = cv2.imencode(ext, scene_bgr)
        if ok:
            buf.tofile(fpath)
            # 同时写元数据 JSON
            meta = {
                "template": template_name,
                "task": task_name,
                "threshold": float(threshold),
                "max_val": float(max_val),
                "window_rect": window_rect,
                "timestamp": datetime.datetime.now().isoformat(),
            }
            meta_path = fpath.replace(".png", ".json")
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            print(f"[诊断] 匹配失败已留证：{fpath}  (模板={template_name}, 任务={task_name}, 阈值={threshold:.2f}, 最高分={max_val:.3f})")
    except Exception as e:
        print(f"[诊断] 保存匹配失败截图异常：{e}")


def match(scene_bgr, template_bgr, threshold, template_name="", task_name="", window_rect=None,
           diagnose_on_miss=False, **_):
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
    # 匹配失败 → 按需诊断留证（仅关键路径 diagnose_on_miss=True 才截图）
    if diagnose_on_miss:
        _save_match_diagnostic(scene_bgr, template_name, task_name, threshold, window_rect, max_val)
    return None


def match_multi(scene_bgr, template_bgr, threshold, max_hits=32, nms_iou=0.4, sort_origin_top_left=True,
                template_name="", task_name="", window_rect=None, diagnose_on_miss=False, **_):
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
        # 完全无峰值 → 按需诊断留证
        if diagnose_on_miss:
            _save_match_diagnostic(scene_bgr, template_name, task_name, threshold, window_rect, 0.0)
        return []
    peaks = []
    for y, x in zip(ys, xs):
        v = float(res[y, x])
        y0, y1 = max(0, y - 1), min(res.shape[0], y + 2)
        x0, x1 = max(0, x - 1), min(res.shape[1], x + 2)
        if v >= float(res[y0:y1, x0:x1].max()):
            peaks.append((x + tw / 2, y + th / 2, v))
    if not peaks:
        if diagnose_on_miss:
            _save_match_diagnostic(scene_bgr, template_name, task_name, threshold, window_rect, 0.0)
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
    if not accepted:
        # NMS 后全被过滤 → 按需诊断留证
        if diagnose_on_miss:
            _save_match_diagnostic(scene_bgr, template_name, task_name, threshold, window_rect, 0.0)
        return []
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


def sqdiff_score(scene_bgr, template_bgr):
    """返回 TM_SQDIFF_NORMED 的最小值（0=逐像素相同，越小越像）。场景/模板不可用返回 1.0。

    用途：区分**同一元素的两态外观**（如「拓印」按钮的未完成↔完成态）。两态差异往往只是**局部亮度**
    变化（未完成处偏灰、完成处偏白），整体亮度近乎一样——TM_CCOEFF_NORMED 会减均值、对均匀亮度不敏感，
    **分不开两态**（实测两个模板在两种状态下都能 ~0.98 命中）。SQDIFF 直接比像素值、对亮度敏感，可清晰判别。

    ⚠ SQDIFF 对像素对齐极敏感，需模板与画面渲染逐像素一致（同分辨率窗口、无损截屏）。
    """
    if scene_bgr is None or template_bgr is None:
        return 1.0
    th, tw = template_bgr.shape[:2]
    if scene_bgr.shape[0] < th or scene_bgr.shape[1] < tw:
        return 1.0
    res = cv2.matchTemplate(scene_bgr, template_bgr, cv2.TM_SQDIFF_NORMED)
    return float(cv2.minMaxLoc(res)[0])


def save_debug_image(scene, path, col_x0=None, col_x1=None,
                     card_xy=None, hits=None, tpl_wh=None, note="",
                     click_kind=None, card_color=None,
                     row_y0=None, row_y1=None):
    """诊断用：在 scene 副本上画标注并存到 path（绝对/相对均可）。坐标均为 scene 局部坐标。
    只画几何 + ASCII 文本（cv2 不支持中文）。返回是否保存成功。
      col_x0/col_x1：卡片所在列左右边界 → 黄色竖带（列内找「参加」的范围）
      row_y0/row_y1：本行带上下边界 → 青色横带（只认该行按钮的纵向容差范围）
      card_xy：卡片中心/点击点 → 十字（默认红；click_kind/card_color 可覆盖）
      hits：[(cx,cy,score)] 低阈值候选 → 框（≥0.7 绿、否则橙）+ 分数
      tpl_wh：(w,h) 命中框尺寸，缺省不画框
      note：左上角 ASCII 备注"""
    if scene is None:
        return False
    img = scene.copy()
    h, w = img.shape[:2]
    click_colors = {
        "left": (0, 255, 0),     # 绿
        "double": (0, 255, 255), # 黄
        "right": (0, 165, 255),  # 橙
    }
    if col_x0 is not None and col_x1 is not None:
        cv2.rectangle(img, (int(col_x0), 0), (int(col_x1) - 1, h - 1), (0, 255, 255), 1)
    if row_y0 is not None and row_y1 is not None:
        bx0 = int(col_x0) if col_x0 is not None else 0
        bx1 = int(col_x1) - 1 if col_x1 is not None else w - 1
        cv2.rectangle(img, (max(0, bx0), max(0, int(row_y0))),
                      (min(w - 1, bx1), min(h - 1, int(row_y1))), (255, 255, 0), 1)
    if tpl_wh and hits:
        tw, th = int(tpl_wh[0]), int(tpl_wh[1])
        for (cx, cy, s) in hits:
            x0, y0 = int(cx - tw / 2), int(cy - th / 2)
            x1, y1 = int(cx + tw / 2), int(cy + th / 2)
            color = (0, 200, 0) if s >= 0.7 else (255, 120, 0)
            cv2.rectangle(img, (x0, y0), (x1, y1), color, 1)
            cv2.putText(img, f"{s:.2f}", (x0, max(12, y0 - 2)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)
    if card_xy is not None:
        marker_color = card_color or click_colors.get(click_kind, (0, 0, 255))
        cv2.drawMarker(img, (int(card_xy[0]), int(card_xy[1])),
                       marker_color, cv2.MARKER_CROSS, 18, 2)
    if note:
        cv2.putText(img, note, (4, max(12, h - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)
    return save_image(path, img)
