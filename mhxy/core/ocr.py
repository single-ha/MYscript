# -*- coding: utf-8 -*-
"""OCR 文字识别封装（RapidOCR / PP-OCRv6 ONNX 模型，离线、随包携带）。

用途：读取商品价格列这种「规整数字」文本，替代逐位模板匹配（旧 core/digits.py）。
为什么不用 RapidOCR 的默认全流程：检测+方向分类+识别三模型串起来约 1.1s/帧，秒抢场景太慢；
本模块自己用 OpenCV 做行切分（价格列布局规则、横向投影即可），每行只跑识别模型：
  · 首次初始化（只加载 20MB 的 rec 模型）约 1.2s；
  · 10 行价格约 0.16s/帧（单字识别 ~17ms）。
引擎全局单例、延迟初始化：不跑本任务就不会付出加载模型的开销。

对外入口：
  is_available()                     —— 依赖/模型是否就绪（preflight 用，便宜）
  read_prices(scene_bgr, min_conf)   —— [(价格, 行中心y, 置信度), ...]
  diagnose_region(...)               —— 读价失败时的诊断图与中间量
"""

import importlib.util
import os
import re
import time

import cv2
import numpy as np

from . import vision

_engine = None
_init_error = None


def is_available():
    """依赖是否装好（只查 find_spec，不加载模型，开销极小）。"""
    for mod in ("rapidocr", "onnxruntime"):
        try:
            if importlib.util.find_spec(mod) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def get_engine():
    """惰性构造只加载识别模型的 RapidOCR 单例；失败抛 RuntimeError（带原始异常信息）。"""
    global _engine, _init_error
    if _engine is not None:
        return _engine
    if _init_error is not None:
        raise RuntimeError(_init_error)
    try:
        from rapidocr import RapidOCR
        _engine = RapidOCR(params={
            "Global.use_det": False,       # 行切分自己做，跳过检测模型
            "Global.use_cls": False,       # 价格都是正向数字，跳过方向分类
            "Global.log_level": "ERROR",   # 别把模型加载日志刷进用户日志面板
        })
    except Exception as e:
        _init_error = f"OCR 引擎初始化失败（请确认已安装 rapidocr）：{e}"
        raise RuntimeError(_init_error) from e
    return _engine


def _binarize(gray):
    """Otsu 二值化成「字=白、底=黑」；亮/暗字都兜底（极值是反色再试一次）。"""
    if gray is None or gray.size == 0:
        return None, False
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    ratio = float(np.count_nonzero(bw)) / bw.size
    if ratio < 0.002 or ratio > 0.6:
        _, inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        inv_ratio = float(np.count_nonzero(inv)) / inv.size
        if 0.002 <= inv_ratio <= 0.6 and abs(inv_ratio - ratio) > 0.05:
            bw, ratio = inv, inv_ratio
    return bw, 0.002 <= ratio <= 0.6


def _row_bands(bw, min_h=6, gap_merge=4):
    """横向投影找文字行：返回 [(y0, y1), ...]（含端点）。

    价格列的行间距远大于字内空隙；字内偶有断层（如「1」的冠部）用 gap_merge 合并。
    连通域噪点（逗号/小点）先按面积和高度剔掉，避免它们单独撑出一条伪行。
    """
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(bw, 8)
    clean = np.zeros_like(bw)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if h < min_h or area < 8 or w < 1:
            continue
        clean[y:y + h, x:x + w] = 255
    proj = (clean > 0).sum(axis=1)
    bands, start = [], None
    for y, on in enumerate(proj > 0):
        if on and start is None:
            start = y
        elif not on and start is not None:
            bands.append([start, y - 1])
            start = None
    if start is not None:
        bands.append([start, len(proj) - 1])
    merged = []
    for b in bands:
        if merged and b[0] - merged[-1][1] - 1 <= gap_merge:
            merged[-1][1] = b[1]
        else:
            merged.append(b[:])
    return [(y0, y1) for y0, y1 in merged if y1 - y0 + 1 >= min_h]


def _row_crop(scene_bgr, bw, band, pad=3, target_h=40):
    """按一条文字行的前景包围盒做紧贴裁剪并放大到 target_h，返回 (裁剪图, 行中心y)。"""
    y0, y1 = band
    ys, xs = np.where(bw[y0:y1 + 1] > 0)
    if len(xs) == 0:
        return None, None
    x0, x1 = int(xs.min()), int(xs.max())
    h, w = scene_bgr.shape[:2]
    left, right = max(0, x0 - pad), min(w, x1 + 1 + pad)
    top, bottom = max(0, y0 - pad), min(h, y1 + 1 + pad)
    crop = scene_bgr[top:bottom, left:right]
    if crop.size == 0:
        return None, None
    scale = max(1.0, float(target_h) / crop.shape[0])
    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop, (y0 + y1) / 2.0


def parse_price(text):
    """把 OCR 文本转成整数价格；认不出返回 None。

    支持千分位逗号（1,500）、货币符号（¥500）、「万」后缀（12万 / 1.5万）。
    只取连续数字部分，避免 OCR 把边框/图标认成字母时整行作废。
    """
    if not text:
        return None
    t = str(text).strip().replace(",", "").replace("，", "").replace(" ", "")
    if not t:
        return None
    try:
        if "万" in t:
            head = t.split("万", 1)[0]
            m = re.search(r"\d+(?:\.\d+)?", head)
            if not m:
                return None
            return int(round(float(m.group()) * 10000))
        digits = re.sub(r"\D", "", t)
        return int(digits) if digits else None
    except (ValueError, OverflowError):
        return None


def read_prices(scene_bgr, min_conf=0.5):
    """识别价格区域里的所有数字行。

    返回 [(price:int, row_cy:float, confidence:float), ...]，按行从上到下；
    置信度低于 min_conf 或解析不出数字的行直接跳过（宁可不买也不瞎买）。
    """
    if scene_bgr is None or scene_bgr.size == 0:
        return []
    gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    bw, has_ink = _binarize(gray)
    if not has_ink or bw is None:
        return []
    bands = _row_bands(bw)
    if not bands:
        return []
    engine = get_engine()
    out = []
    for band in bands:
        crop, cy = _row_crop(scene_bgr, bw, band)
        if crop is None:
            continue
        try:
            res = engine(crop)
        except Exception:
            continue
        if not res or not res.txts:
            continue
        text, score = res.txts[0], float(res.scores[0])
        if score < min_conf:
            continue
        price = parse_price(text)
        if price is None:
            continue
        out.append((price, cy, score))
    return out


def diagnose_region(scene_bgr, min_conf=0.5, save_dir=None):
    """读价失败时的诊断：返回中间量 + 落盘标注图（每行画框并写 OCR 原文/分数）。"""
    info = {"n_rows": 0, "rows": [], "annotated": None, "raw_image": None, "reason": ""}
    if scene_bgr is None or scene_bgr.size == 0:
        info["reason"] = "场景图为空"
        return info
    gray = cv2.cvtColor(scene_bgr, cv2.COLOR_BGR2GRAY)
    bw, has_ink = _binarize(gray)
    if not has_ink or bw is None:
        info["reason"] = "二值化无前景（价格区域可能框错/无数字）"
        return info
    bands = _row_bands(bw)
    info["n_rows"] = len(bands)
    if not bands:
        info["reason"] = "横向投影没切出文字行"
        return info
    annotated = scene_bgr.copy()
    engine = None
    for y0, y1 in bands:
        crop, cy = _row_crop(scene_bgr, bw, (y0, y1))
        if crop is None:
            continue
        text, score, price = "", 0.0, None
        try:
            engine = engine or get_engine()
            res = engine(crop)
            if res and res.txts:
                text, score = res.txts[0], float(res.scores[0])
                price = parse_price(text)
        except Exception as e:
            text = f"ERR:{e}"
        ys, xs = np.where(bw[y0:y1 + 1] > 0)
        x0, x1 = (int(xs.min()), int(xs.max())) if len(xs) else (0, scene_bgr.shape[1] - 1)
        color = (0, 200, 0) if (price is not None and score >= min_conf) else (0, 0, 255)
        cv2.rectangle(annotated, (x0, y0), (x1, y1), color, 1)
        cv2.putText(annotated, f"{text[:14]} {score:.2f}", (x0, max(10, y0 - 2)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.32, color, 1, cv2.LINE_AA)
        info["rows"].append((int(cy), text, round(score, 3), price))
    info["reason"] = "已逐行识别，见标注图"
    if save_dir is not None:
        try:
            os.makedirs(str(save_dir), exist_ok=True)
            stamp = time.strftime("%H%M%S")
            ap = os.path.join(str(save_dir), f"ocr_diag_{stamp}_box.png")
            rp = os.path.join(str(save_dir), f"ocr_diag_{stamp}_raw.png")
            if vision.save_image(ap, annotated):
                info["annotated"] = vision.dated_path(ap)
            if vision.save_image(rp, scene_bgr):
                info["raw_image"] = vision.dated_path(rp)
        except Exception as e:
            info["save_error"] = str(e)
    return info
