# -*- coding: utf-8 -*-
"""
拟人化「描摹临摹」：从绘制区画面识别出【线条图案】，生成沿图案骨架的描摹路径并拖动。

用于游戏里「按住鼠标沿图案描一遍」这类校验（如梦幻拓印）。不同于 fill_region 的无脑盖满整区：
拓印判定「完成度>60%」，描到图案**外**会拉低完成度，所以必须先认出图案像素、只沿笔画本身走。

做法（2026-10-17 重构，见 CLAUDE.md 约束与 memory）：
  1. **局部对比二值化**（不再用整幅 Otsu）：背景 = 中值滤波 **并上** 形态学闭/开运算，再叠一个**大核**
     （≈画幅/3）的闭/开运算。闭运算(深墨)/开运算(浅线)把小于核的明暗块整体抹掉来估计纸面，能覆盖
     **实心/粗笔画**；中值滤波补足窄于核的细线。`d=|bg-gray|`，阈值 `T=max(t_min, frac·局部std)`。
     图案是「深墨画浅纸」还是「浅线画深底」都支持（双极性都试）。
     这一步是关键：拓印图案对比度低、又嵌在对话框里，整幅 Otsu 会选中面板/边框而不是墨迹；
     只用中值滤波时实心/粗笔画会被描成空心轮廓（骨架沿边界走）；**小核**（默认 41）则会把比核还宽的
     实心块当成背景 → 只认出一条细中线（★2026-10-18 用户报「图案上半没描满」即此因），故叠大核修正。
  2. **剔除框线**：丢掉跨画幅的细线、以及**贴画幅边缘的宽扁/窄高细带**（对话框边框/面板分隔线/滚动条）。
     ⚠ 与图案**粘连**的分隔线在这里抓不到（整个连通域 bbox 不再细长），逐行游程过滤也抓不到「斜着走」
     的线（每行游程远小于阈值）→ 骨架拆出笔画后再在**笔画级**按「横贯+扁平+近直线」几何丢
     （`_drop_flat_band_strokes`，★2026-10-22 用户报「顶部横贯分隔线被当图案描」）；
     阴影/纹理类**淡色碎线**（对比度远低于真墨）按「笔画均对比 < 纸面全量程 faint_contrast_frac」丢
      （`_drop_faint_strokes`）。被丢笔画的像素必须从 mask 里同步擦掉（`_erase_dropped`，按最近骨架
      归属+局部半宽擦除、防 junction 处啃掉图案），否则后面的 `_cover_fill` 会把它们当「没盖到的
      骨架段」补回来。
  3. **形态学**：开操作去网格噪点、闭操作接回断笔；再按「相对画幅面积」丢掉小碎块（min_area_ratio 生效）。
  4. Zhang-Suen 细化得 1px 骨架（先缩放到长边 ≤skel_max_dim 再算，坐标还原）；再**剪掉短毛刺**
     （spur_len），否则碎枝会把笔画数撑爆。
  5. 贪心拆成尽量少的连续笔画路径；「又短又贴着别的笔画」的碎片**并进**那条笔画（而不是删——保覆盖、
     减笔画数；修贪心在交叉点把一条真线切成主笔+残段），孤立短笔画才保留/丢弃；再丢掉「近似直线且
     很长」的笔画（残留框线/网格）与上述扁平横带/淡色笔画；最后把笔画数压到 ≤max_strokes
     （默认 14，游戏要求「少于15笔」）。
  6. **选侧判据**：不是「骨架总长最大」（框线总是最长），而是「可信度评分」——笔画条数适中、
     不跨画幅、笔迹占比合理者胜。
  7. **走笔（描摹，trace_pattern）**：★2026-10-22 用户拍板——**不要 Z 字锯齿，走笔直接沿骨架**
     依次 glide 移动。覆盖补全 `_cover_fill` 的判定基准同步改为**骨架像素**（若仍按墨迹判覆盖，
     每条粗笔画两侧都会永远「没盖到」、被反复补出内部平行笔直到上限）：把走笔没盖到的骨架段
     （如被 min_stroke_len 丢掉的短段）各自补一笔。
      移动用 `Mouse.glide`（轻量 1~3 小步）而非 human_move——采样点很密，若每点重算贝塞尔（6~14 步）
      单遍会到分钟级；★2026-10-18 用户要求「描摹 < 2 分钟」，故走 glide。见 `_trace_stroke`。

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
    "drop_flat_bands": True,  # 是否丢弃「横贯/纵贯画幅的扁平细带」笔画——与图案粘连的面板分隔线（组件级抓不到）
    "flat_span_frac": 0.75,  # 扁平带判据：长边 ≥ 此比例·画幅
    "flat_thin_frac": 0.15,  # 扁平带判据：短边 ≤ 此比例·画幅
    "flat_straight": 0.9,    # 扁平带判据：直线度下限（分隔线常略弯，放 0.9 而不是 0.99）
    "drop_faint": True,      # 是否丢弃「对比度太弱」的笔画（阴影/纹理淡痕，非真墨）
    "faint_contrast_frac": 0.12,  # 淡痕判据：笔画均对比 < 此比例·纸面全量程（dark=median(bg)，light=255−median(bg)）
    "spur_len": 6,         # 骨架毛刺剪枝长度（像素）：短于此的分叉/噪点枝先剪掉，避免碎成一大堆笔画
    "min_stroke_len": 4,   # 单条笔画最短采样点数：短于此的碎片当噪点丢弃
    "max_strokes": 14,     # 笔画数上限（游戏要求「少于15笔」）；超了就合并最近的端点，直到 < 上限
    "absorb_span": 12,     # 碎片「bbox 对角跨度」阈值（像素）：又短又贴着别的笔画 → 并进它（不是删）
    "absorb_gap": 3,       # 碎片到最近笔画的最短像素距离上限：超过它=孤立的真·点画，保留为独立一笔
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
    "tuoying_drop_flat_bands": "drop_flat_bands",
    "tuoying_flat_span_frac": "flat_span_frac",
    "tuoying_flat_thin_frac": "flat_thin_frac",
    "tuoying_flat_straight": "flat_straight",
    "tuoying_drop_faint": "drop_faint",
    "tuoying_faint_frac": "faint_contrast_frac",
    "tuoying_spur_len": "spur_len",
    "tuoying_stroke_min_len": "min_stroke_len",
    "tuoying_max_strokes": "max_strokes",
    "tuoying_absorb_span": "absorb_span",
    "tuoying_absorb_gap": "absorb_gap",
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


def trace_pattern(mouse, rect, frame_bgr, speed=1.0, sample_step=5,
                  min_area_ratio=None, label="绘制区", polarity="auto", params=None,
                  on_stroke=None, should_stop=None):
    """识别 rect（屏幕绝对坐标 [x,y,w,h]）内 frame_bgr（该矩形截屏）里的线条图案并沿其描摹。

    - 先调 _extract_strokes 分离前景线条、拆成连续笔画路径（已按最长优先排序）。
    - 每笔：移进该笔起点（松开状态）→ 按住 → **直接沿骨架点序移动**（★2026-10-22 用户拍板：
      不要 Z 字锯齿摆动）→ 松开；两笔之间歇手。
    - sample_step: 骨架采样间距（像素），越小轨迹越贴骨架（也更慢）。
    - polarity: "dark"=深墨、"light"=浅线；见 _extract_strokes。
    - on_stroke: 可选回调 `(i, n) -> bool`，**每描完一笔**调用一次（i=第几笔从0起, n=总笔数）；
      返回 True 表示「已完成」→ 立即停止描摹、返回 True（不再描剩余笔画）。调用方借此在每笔后
      检测「完成/上传」按钮是否已出现并点击（★2026-10-18：用户要求描完一笔就查一次完成按钮）。
    - should_stop: 可选可调用 `() -> bool`，描摹途中**频繁轮询**它（每个笔画、每个采样点、覆盖补全
      每轮）。返回 True 时立即中止描摹并**返回 None**（与「认不出图案=False」区分，调用方据此识别急停）。
      不传则不可中断（★2026-10-18：用户报「拓印急停不管用」，就是此前从不查 stop）。

    返回 True=描完/被 on_stroke 判定完成；False=认不出图案（转手动兜底）；None=被 should_stop 急停。
    """
    x, y, w, h = rect
    if frame_bgr is None or frame_bgr.size == 0:
        return False
    strokes, meta = _extract_strokes(frame_bgr, min_area_ratio=min_area_ratio, label=label,
                                     polarity=polarity, params=params, want_meta=True)
    if not strokes:
        return False
    p = _merge_params(params)
    skel = (meta or {}).get("skel")
    # 覆盖补全：走笔只沿骨架，故按「骨架像素是否被走到」找漏段补笔（基准=骨架，不是墨迹）
    strokes = _cover_fill(strokes, skel, sample_step,
                          max_strokes=int(p["max_strokes"]), should_stop=should_stop)
    # 最长笔优先：核心笔画先描（万一中途被停，覆盖占比已经最大）
    strokes.sort(key=len, reverse=True)
    for idx, stroke in enumerate(strokes):
        if should_stop is not None and should_stop():
            return None                        # 急停：还没开始这笔画就停
        if not stroke:
            continue
        # 起笔（松开状态移进起点附近）
        s0 = stroke[0]
        mouse.human_move(x + s0[0], y + s0[1], speed=speed)
        time.sleep(random.uniform(0.05, 0.15))
        mouse.hold()
        try:
            _trace_stroke(mouse, x, y, stroke, sample_step=sample_step, speed=speed,
                          should_stop=should_stop)
        finally:
            try:
                mouse.release()
            except Exception:
                pass
        if should_stop is not None and should_stop():
            return None                        # 刚被急停：别再做换笔/回调（回调会点上传）
        time.sleep(random.uniform(0.15, 0.4))     # 笔画之间歇手，像人换笔
        # 每描完一笔回调一次：调用方借此检测「完成/上传」按钮是否已出现；返回 True=已完成，停止描摹
        if on_stroke is not None:
            try:
                if on_stroke(idx, len(strokes)):
                    return True
            except Exception:
                pass
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
    # 大尺度背景：当粗笔画/实心块比 kb 还宽，小核会把块自身当纸面 → 只描出轮廓/中线（★2026-10-18
    # 用户报「图案上半没描满」正是此因：图案上半是 ~80px 宽的实心块，kb=41 只认出一条细线）。
    # 再叠一个大核闭/开运算（核≈画幅/3）抹掉更宽的深/浅块来估计真正纸面。对深墨取 max、浅线取 min。
    kb_big = max(kb, (min(h, w) // 3) * 2 + 1)
    ell_big = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kb_big, kb_big))
    close_small = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, ell).astype(np.float32)
    open_small = cv2.morphologyEx(gray, cv2.MORPH_OPEN, ell).astype(np.float32)
    # 大核在画幅边缘会因边界外推估错背景 → 凭空冒出边角墨点。先补边再算、算完裁回。
    pad = kb_big
    gp = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    close_big = cv2.morphologyEx(gp, cv2.MORPH_CLOSE, ell_big)[pad:-pad, pad:-pad].astype(np.float32)
    open_big = cv2.morphologyEx(gp, cv2.MORPH_OPEN, ell_big)[pad:-pad, pad:-pad].astype(np.float32)
    # 局部标准差（boxFilter 快速估计），用于自适应阈值
    m1 = cv2.boxFilter(gf, -1, (31, 31))
    m2 = cv2.boxFilter(gf * gf, -1, (31, 31))
    std = np.sqrt(np.clip(m2 - m1 * m1, 0, None))
    thr = np.maximum(float(p["contrast_min"]), float(p["contrast_frac"]) * std)

    # 候选三元组：(极性名, 对比图 d, 纸面全量程 full)——full 供淡痕判定
    # （dark=纸面亮度 median(bg)；light=还能打亮的全量 255−median(bg)）
    if polarity == "dark":
        bg = np.maximum(np.maximum(bg_med, close_small), close_big)
        candidates = [("dark", np.clip(bg - gf, 0, 255), float(np.median(bg)))]
    elif polarity == "light":
        bg = np.minimum(np.minimum(bg_med, open_small), open_big)
        candidates = [("light", np.clip(gf - bg, 0, 255), 255.0 - float(np.median(bg)))]
    else:
        bg_dark = np.maximum(np.maximum(bg_med, close_small), close_big)
        bg_light = np.minimum(np.minimum(bg_med, open_small), open_big)
        candidates = [("dark", np.clip(bg_dark - gf, 0, 255), float(np.median(bg_dark))),
                      ("light", np.clip(gf - bg_light, 0, 255),
                       255.0 - float(np.median(bg_light)))]

    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    min_area = max(1, int(total * float(p["min_area_ratio"])))

    best_strokes, best_score, best_meta = [], -1e9, None
    for name, d, full_c in candidates:
        bw = ((d > thr) * 255).astype(np.uint8)
        if int((bw > 0).sum()) < 4:
            continue
        bw = cv2.morphologyEx(bw, cv2.MORPH_OPEN, kernel_open)
        bw = cv2.morphologyEx(bw, cv2.MORPH_CLOSE, kernel_close)
        bw = _drop_frame_components(bw)
        bw = _drop_thin_lines(bw)                    # 剔框线/分割线/贴边
        bw = _drop_tiny(bw, min_area)                      # 剔小碎块（相对画幅）
        if int((bw > 0).sum()) < min_area:
            continue
        skel, scale = _skeletonize_capped(bw, int(p["skel_max_dim"]))
        # 剪枝：去掉短毛刺/噪点枝（游戏限制「笔画数<15」，不剪会被碎枝撑爆笔画数）
        skel = _prune_spurs(skel, max(1, int(round(float(p["spur_len"]) * (scale or 1.0)))))
        strokes = _skeleton_to_strokes(skel)
        strokes = _scale_strokes(strokes, 1.0 / scale if scale else 1.0)
        strokes = [s for s in strokes if len(s) >= int(p["min_stroke_len"])]
        # 先丢「非图案」笔画（扁平横带=分隔线、淡色淡痕=阴影/纹理碎线），再做碎片并笔——
        # 免得把伪笔画并进真笔画（并进去就删不掉了）
        dropped = []
        if p["drop_flat_bands"]:
            strokes, dj = _drop_flat_band_strokes(strokes, (h, w),
                                                  float(p["flat_span_frac"]),
                                                  float(p["flat_thin_frac"]),
                                                  float(p["flat_straight"]))
            dropped += dj
        if p["drop_faint"]:
            strokes, dj = _drop_faint_strokes(strokes, d, full_c,
                                              float(p["faint_contrast_frac"]))
            dropped += dj
        # 把「短又贴着别的笔画」的碎片并进最近笔画（保覆盖、减笔画数；孤立小笔画保留）
        strokes = _absorb_short_strokes(strokes, float(p["absorb_span"]), float(p["absorb_gap"]))
        if p["drop_straight"]:
            strokes, dj = _drop_straight_strokes(strokes, (h, w),
                                                 float(p["straight_ratio"]),
                                                 float(p["straight_len_frac"]))
            dropped += dj
        if dropped:
            # 从 mask 擦掉被丢笔画的像素足迹——否则 _cover_fill 会把它们当「没盖到的墨迹块」
            # 重新补笔（笔画级删除不删 mask 等于白删）；skel 同步擦（调试图一致）
            dist0 = cv2.distanceTransform((bw > 0).astype(np.uint8), cv2.DIST_L2, 3)
            bw, erase = _erase_dropped(bw, dist0, dropped, strokes)
            if erase is not None and skel is not None:
                es = cv2.resize(erase, (skel.shape[1], skel.shape[0]),
                                interpolation=cv2.INTER_NEAREST)
                skel[(es > 0)] = 0
        strokes = _reduce_strokes(strokes, int(p["max_strokes"]))
        if not strokes:
            continue
        score = _stroke_confidence(strokes, (h, w), total)
        if want_meta:
            # 骨架存**原画幅尺寸**：strokes 已 _scale_strokes 还原到原画幅坐标，_cover_fill/
            # render_debug 都按原画幅用 skel（缩放后 skel 只在内部细化时用）。★修 2026-10-22：
            # 之前直接存缩放版 skel，画幅长边>skel_max_dim 时与 strokes 坐标系不一致 →
            # _cover_fill 画布尺寸错、补笔错位。
            skel_full = skel
            if skel is not None and scale and scale != 1.0:
                skel_full = cv2.resize(skel, (w, h), interpolation=cv2.INTER_NEAREST)
            meta = {"mask": bw, "skel": skel_full,
                    "dist": cv2.distanceTransform((bw > 0).astype(np.uint8), cv2.DIST_L2, 3),
                    "polarity": name, "scale": scale, "score": score}
        else:
            meta = None
        if score > best_score:
            best_strokes, best_score, best_meta = strokes, score, meta
        if polarity in ("dark", "light"):
            break

    if want_meta:
        return best_strokes, (best_meta or {"mask": None, "skel": None, "dist": None,
                                            "polarity": None, "scale": 1.0, "score": best_score})
    return best_strokes


def _drop_frame_components(bw):
    """丢掉「横贯/纵贯画幅的细线」连通域——对话框边框、分割线、滚动条。

    注意：**不能按「贴边」判**——拓印绘制区常被标得较紧，图案笔画本身就会触到框边；
    旧版按贴边丢，把整条斜笔画连同图案一起丢了（2026-10-17 实测踩坑）。
    两类才丢（都要求「细线」：面积/bbox 面积 < 0.5）：
      · `spans` —— 某方向跨 ≥90% 画幅（对话框外框/贯通分割线）；
      · `flat_band` —— 「宽扁」或「窄高」带状（某方向 ≥70%、垂直方向 ≤35% 画幅）且**贴画幅边缘**：
        这是绘制区标得太松、把面板顶/底分隔线（横向长条，常 80~90% 宽、几~20px 高、贴上/下边）一起圈进来。
        要求贴边是为了别误删图案里真正的宽扁笔画（如「三」的横），它们在画幅中部。"""
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
        # 宽扁/窄高带状：某方向≥70% 画幅、垂直方向≤35% 画幅
        band = (w >= 0.7 * W and h <= 0.35 * H) or (h >= 0.7 * H and w <= 0.35 * W)
        # 且贴画幅边缘（离某边 ≤15%）——把「面板分隔线」和「图案中间的宽扁笔画」区分开
        edge = (y <= 0.15 * H or (y + h) >= 0.85 * H or
                x <= 0.15 * W or (x + w) >= 0.85 * W)
        if (spans and thin) or (band and thin and edge):
            continue                      # 跨画幅的细线/贴边宽扁带 = 框/分割线
        keep[labels == i] = 255
    return keep


def _drop_thin_lines(bw, span_frac=0.7, thick_max=5):
    """逐行/逐列移除「几乎横贯/纵贯画幅且很细」的墨线——面板分割线、滚动条轨道。

    与 _drop_frame_components 互补：当分割线与图案**粘连**时，整个连通域 bbox 变大、不再满足
    「细线/带状」判据，会漏删（★2026-10-18：大核背景把 3px 分隔线又勾回来、且它紧贴图案顶端）。
    做法：用长条核开运算抽出「属于长横/长竖游程」的像素，再减去「粗块内部」像素（避免误删实心图案边缘）。
    """
    h, w = bw.shape[:2]
    Lh = max(8, int(round(w * span_frac)))
    Lv = max(8, int(round(h * span_frac)))
    hor = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (Lh, 1)))
    ver = cv2.morphologyEx(bw, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (1, Lv)))
    thinline = cv2.bitwise_or(hor, ver)
    if not thinline.any():
        return bw
    tk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (thick_max + 1, thick_max + 1))
    thick = cv2.dilate(cv2.erode(bw, tk), tk)     # 粗块（含其扩张足迹）
    remove = (thinline > 0) & (thick == 0)
    if not remove.any():
        return bw
    out = bw.copy()
    out[remove] = 0
    return out


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
    （如「从」的撇/捺），松判据会把图案一起误删（2026-10-17 实测踩坑）。
    返回 (kept, dropped)——dropped 供调用方从 mask 同步擦除（否则 _cover_fill 会补回来）。"""
    h, w = shape
    limit = len_frac * max(h, w)
    out, dropped = [], []
    for s in strokes:
        if len(s) < 8:
            out.append(s)
            continue
        (x0, y0), (x1, y1) = s[0], s[-1]
        span = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        straightness = span / max(1.0, float(len(s) - 1))
        if span >= limit and straightness >= ratio:
            dropped.append(s)
            continue
        out.append(s)
    return out, dropped


def _drop_flat_band_strokes(strokes, shape, span_frac, thin_frac, straight_min):
    """丢掉「横贯(或纵贯)画幅的扁平细带」笔画——面板分隔线/装饰横线（★2026-10-22）。

    分隔线常与图案**粘连**成一个连通域 → 组件级 `_drop_frame_components` 判据失效；
    它又常「斜着走/中段下沉」→ `_drop_thin_lines` 的逐行游程、`_drop_straight_strokes` 的
    0.99 直线度都抓不到（实测某帧分隔线 span=0.81·画幅、直线度仅 0.92）。
    本规则在笔画级按整体几何判定，与图案形状无关：bbox 某方向 ≥ span_frac·画幅 且垂直方向
    ≤ thin_frac·画幅，且直线度(span/路径长) ≥ straight_min。返回 (kept, dropped)。"""
    h, w = shape
    kept, dropped = [], []
    for s in strokes:
        if len(s) < 2:
            kept.append(s)
            continue
        a = np.asarray(s, int)
        bwd = int(a[:, 0].max() - a[:, 0].min())
        bhd = int(a[:, 1].max() - a[:, 1].min())
        span = float(np.hypot(a[-1, 0] - a[0, 0], a[-1, 1] - a[0, 1]))
        plen = float(np.sum(np.hypot(np.diff(a[:, 0]), np.diff(a[:, 1]))))
        straight = span / max(1.0, plen)
        band = ((bwd >= span_frac * w and bhd <= thin_frac * h) or
                (bhd >= span_frac * h and bwd <= thin_frac * w))
        if band and straight >= straight_min:
            dropped.append(s)
        else:
            kept.append(s)
    return kept, dropped


def _drop_faint_strokes(strokes, d_map, full_contrast, frac):
    """丢掉「对比度太弱」的笔画——阴影/纹理类淡痕，不是真墨（★2026-10-22）。

    沿笔画点 3×3 膨胀采样对比图 d 取均值；`d_mean < frac × full_contrast`（纸面全量程：
    dark=median(bg)、light=255−median(bg)，由调用方按极性算好传入）判为淡痕丢弃。
    参照系是「纸面全量程」而非「最浓笔画」——后者会被单笔特别浓的图案带偏、误伤其他真笔。
    实测某帧：真墨 d_mean=37~47、伪碎线 16~22、阈值 0.12×238≈28.6，区分干净。
    返回 (kept, dropped)。"""
    if full_contrast is None or full_contrast <= 0 or frac <= 0:
        return strokes, []
    thr = float(frac) * float(full_contrast)
    kept, dropped = [], []
    for s in strokes:
        if len(s) < 2:
            kept.append(s)
            continue
        a = np.asarray(s, int)
        m = np.zeros(d_map.shape, np.uint8)
        m[np.clip(a[:, 1], 0, d_map.shape[0] - 1),
          np.clip(a[:, 0], 0, d_map.shape[1] - 1)] = 1
        m = cv2.dilate(m, np.ones((3, 3), np.uint8))
        vals = d_map[m > 0]
        md = float(vals.mean()) if vals.size else 0.0
        if md < thr:
            dropped.append(s)
        else:
            kept.append(s)
    return kept, dropped


def _erase_dropped(bw, dist, dropped, kept):
    """从 mask 里擦掉「被丢笔画」的像素足迹，返回 (新bw, erase掩膜 或 None)。

    笔画级删除若不清 mask，`_cover_fill` 会把这些像素当「没盖到的墨迹块」重新补笔
    （实测：分隔线被删后又被原样补回来）。擦除判据（★2026-10-22 两轮实测修正）：
      · `distD ≤ 被丢骨架最近点处的墨半宽+1`——只删贴着被丢线「本身粗细」的像素。
        （不能按统一大半径画盘：分隔线在 junction 处 dist 可达 14+px，大圆盘会啃掉
        图案 7% 并把它切碎；也不能按中位数半径：粗区盖不住、留残段照样被补笔。）
      · 且 `distD < distK`（离被丢骨架比离保留骨架更近）——junction 附近按最近骨架归属，
        图案一侧保住。与被丢线**直接相贴**的几个图案像素在像素级无法区分、会被擦掉，
        但那几像素落在保留笔画描摹时的横向铺宽覆盖内，不影响完成度。
    """
    if not dropped:
        return bw, None
    H, W = bw.shape[:2]
    dm = np.zeros((H, W), np.uint8)
    for s in dropped:
        for (c, r) in s:
            r0, c0 = int(r), int(c)
            if 0 <= r0 < H and 0 <= c0 < W:
                dm[r0, c0] = 1
    if not dm.any():
        return bw, None
    src = np.where(dm > 0, 0, 255).astype(np.uint8)
    distD, labels = cv2.distanceTransformWithLabels(src, cv2.DIST_L2, 3,
                                                    labelType=cv2.DIST_LABEL_PIXEL)
    # 标签 → 该被丢骨架点的「墨半宽+1」。★OpenCV 4.13 实测：DIST_LABEL_PIXEL 的标签是
    # 按**零像素的光栅顺序**从 1 顺序编号（不是行优先像素序号），np.where 恰好就是这个顺序。
    rs, cs = np.where(dm > 0)
    half = np.zeros(len(rs) + 1, np.float32)
    for i, (r0, c0) in enumerate(zip(rs.tolist(), cs.tolist())):
        half[i + 1] = float(dist[r0, c0]) + 1.0
    rd = half[labels]
    nearer = np.ones((H, W), bool)
    km = np.zeros((H, W), np.uint8)
    for s in kept:
        for (c, r) in s:
            r0, c0 = int(r), int(c)
            if 0 <= r0 < H and 0 <= c0 < W:
                km[r0, c0] = 1
    if km.any():
        distK = cv2.distanceTransform(np.where(km > 0, 0, 255).astype(np.uint8),
                                      cv2.DIST_L2, 3)
        nearer = distD < distK
    erase = (bw > 0) & (distD <= rd) & nearer
    if not erase.any():
        return bw, None
    out = bw.copy()
    out[erase] = 0
    return out, erase.astype(np.uint8)


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


def _cover_fill(strokes, skel, sample_step, blob_min=8.0, tol=3, max_strokes=15,
                max_rounds=3, should_stop=None):
    """覆盖补全：把「走笔（沿骨架直走）没盖到的**骨架段**」各自补一笔。

    ★2026-10-22 用户拍板走笔直接沿骨架、不摆动后，覆盖判定基准从「墨迹像素」改为**骨架像素**——
    若仍按墨迹判，每条粗笔画两侧都会永远「没盖到」、被反复补出内部平行笔直到上限。
    仍兜的场景：拆笔/剪毛刺时被丢掉的短骨架段（实心块细化退化出的零散点等）没进任何笔画路径。
    skel 为空（无 meta）时原样返回。"""
    if skel is None:
        return strokes
    H, W = skel.shape
    target = skel > 0
    work = list(strokes)
    step = max(1, int(sample_step))

    def _raster():
        c = np.zeros((H, W), np.uint8)
        for s in work:
            n = len(s)
            prev = None
            for i in range(0, n, step):
                bx, by = s[i]
                if prev is not None:
                    cv2.line(c, (int(prev[0]), int(prev[1])), (int(bx), int(by)), 255, 1)
                prev = (bx, by)
            if n:
                bx, by = s[-1]
                if prev is not None:
                    cv2.line(c, (int(prev[0]), int(prev[1])), (int(bx), int(by)), 255, 1)
        return c

    for _ in range(max_rounds):
        if should_stop is not None and should_stop():
            break
        if len(work) >= max_strokes:
            break
        covered = cv2.dilate(_raster(),
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * tol + 1, 2 * tol + 1))) > 0
        unc = ((target & ~covered).astype(np.uint8)) * 255
        n, lab, st, _ = cv2.connectedComponentsWithStats(unc, 8)
        add = []
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] < blob_min:   # 骨架像素计数（不是墨迹面积）
                continue
            if len(work) + len(add) >= max_strokes:
                break
            comp = np.zeros((H, W), np.uint8)
            comp[lab == i] = 255                     # 本来就是骨架，直接拆笔画
            add += [x for x in _skeleton_to_strokes(comp) if len(x) >= 4]
        if not add:
            break
        work = work + add
    return work


def _absorb_short_strokes(strokes, span_max, gap_max):
    """把「几何跨度很小、又紧贴别的笔画」的碎片**并进那条最近笔画**（在最近点处把碎片编入其路径：
    沿主笔走到分叉点 → 拐进碎片描一遍 → 折返回分叉点继续），既**保住覆盖**、又减少笔画数。

    ⚠ 只处理「bbox 对角跨度 < span_max 且到最近笔画的最短像素距离 ≤ gap_max」的碎片；孤立的小笔画
    （真·点画，离别的笔画较远）保留为独立一笔——这正是它区别于「无脑删短笔画」的关键：拓印判
    「完成度>60%」，把真笔画删了会掉完成度，并进去则照样描到。修的是贪心拆路径在**交叉点**处
    把一条真线切成主笔+小残段（残段两端贴着主笔，跨度小但采样点够多，躲过了 min_stroke_len）。"""
    def span_of(s):
        arr = np.asarray(s, dtype=np.float32)
        dx = float(arr[:, 0].max() - arr[:, 0].min())
        dy = float(arr[:, 1].max() - arr[:, 1].min())
        return float(np.hypot(dx, dy))
    work = [list(s) for s in strokes if len(s) >= 2]
    if not work or span_max <= 0:
        return work
    changed = True
    while changed:
        changed = False
        for i in range(len(work)):
            if len(work) <= 1:
                break
            if span_of(work[i]) >= span_max:
                continue
            si = np.asarray(work[i], dtype=np.float32)
            best = None                       # (dist, j, ki, kj)
            for j in range(len(work)):
                if j == i:
                    continue
                sj = np.asarray(work[j], dtype=np.float32)
                d2 = ((si[:, None, :] - sj[None, :, :]) ** 2).sum(axis=2)
                kf = int(np.argmin(d2))
                ki, kj = kf // d2.shape[1], kf % d2.shape[1]
                dist = float(np.sqrt(d2[ki, kj]))
                if dist <= gap_max and (best is None or dist < best[0]):
                    best = (dist, j, ki, kj)
            if best is None:                  # 孤立碎片：保留
                continue
            _, j, ki, kj = best
            frag = work[i] if ki <= len(work[i]) - 1 - ki else work[i][::-1]  # 近端在前，少折返
            owner = work[j]
            merged = owner[:kj + 1] + frag + owner[kj:]   # 折返回分叉点再续走
            work = [work[k] for k in range(len(work)) if k not in (i, j)]
            work.append(merged)
            changed = True
            break
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
    """Zhang-Suen 细化：1px 骨架。输入/输出均 0/255 二值图。

    旧版纯 Python 双重循环逐像素判断，在 480×480 画幅上极慢；
    本版用 numpy 对整幅图一次性计算每个像素的删除条件，仅保留
    Python 层面的「迭代直到收敛」外层循环，内部全是 C 速度的
    向量化运算，速度提升一个数量级以上，与旧版结果等价。
    """
    img = (bw > 0).astype(np.uint8)
    img = cv2.copyMakeBorder(img, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    changed = True
    while changed:
        changed = False
        for sub_pass in (0, 1):
            p1 = img[1:-1, 1:-1]
            p2 = img[0:-2, 1:-1]      # 上
            p3 = img[0:-2, 2:]        # 右上
            p4 = img[1:-1, 2:]        # 右
            p5 = img[2:, 2:]          # 右下
            p6 = img[2:, 1:-1]        # 下
            p7 = img[2:, 0:-2]        # 左下
            p8 = img[1:-1, 0:-2]      # 左
            p9 = img[0:-2, 0:-2]      # 左上

            center = (p1 == 1)
            if not center.any():
                continue

            # 条件 1：邻居数 2..6
            N = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            cond_count = (N >= 2) & (N <= 6)

            # 条件 2：8 邻域黑白转换数 == 1
            trans = (
                ((p2 == 1) & (p3 == 0)).astype(np.uint8)
                + ((p3 == 1) & (p4 == 0)).astype(np.uint8)
                + ((p4 == 1) & (p5 == 0)).astype(np.uint8)
                + ((p5 == 1) & (p6 == 0)).astype(np.uint8)
                + ((p6 == 1) & (p7 == 0)).astype(np.uint8)
                + ((p7 == 1) & (p8 == 0)).astype(np.uint8)
                + ((p8 == 1) & (p9 == 0)).astype(np.uint8)
                + ((p9 == 1) & (p2 == 0)).astype(np.uint8)
            )
            cond_trans = (trans == 1)

            # 条件 3：子迭代特定约束
            if sub_pass == 0:
                cond_sp = (~((p2 == 1) & (p4 == 1) & (p6 == 1))
                           & ~((p4 == 1) & (p6 == 1) & (p8 == 1)))
            else:
                cond_sp = (~((p2 == 1) & (p4 == 1) & (p8 == 1))
                           & ~((p2 == 1) & (p6 == 1) & (p8 == 1)))

            mark = center & cond_count & cond_trans & cond_sp
            if mark.any():
                img[1:-1, 1:-1] = np.where(mark, 0, p1)
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
# 沿一条笔画拖动：分段采样、直接沿骨架走（★2026-10-22 用户拍板：不要 Z 字锯齿）
# ----------------------------------------------------------------------
def _trace_stroke(mouse, ox, oy, stroke, sample_step=5, speed=1.7, should_stop=None):
    """按住状态下，沿 stroke（帧内相对坐标）骨架点序**直接**依次移动。
    ox/oy 为绘制区屏幕绝对左上角，把帧内坐标换算回屏幕坐标。
    （旧版在每个采样点做法向摆动仿笔宽/铺满粗区；★2026-10-22 用户拍板「不要Z字临摹，
    走笔直接按照骨架走」——去掉全部横向偏移。）"""
    step = max(1, int(sample_step))          # 配置里可能是 float（如 5.0），range 步长必须 int
    n = len(stroke)
    for i in range(0, n, step):
        if should_stop is not None and should_stop():
            return                            # 急停：尽快交出控制权（每个采样点查一次）
        bx, by = stroke[i]
        # glide=轻量平滑移动（1~3 小步）；描摹采样点很密，用 human_move 的 6~14 步会慢到分钟级
        mouse.glide(ox + bx, oy + by, speed=speed)
        time.sleep(random.uniform(0.001, 0.004) / speed if speed else 0.001)
    if n and (n - 1) % step != 0:            # 收尾补到终点
        bx, by = stroke[-1]
        mouse.glide(ox + bx, oy + by, speed=speed)


# ----------------------------------------------------------------------
# 调试图渲染：把提取中间结果画出来，便于人工核对（我看不了图，靠人眼闭环）
# ----------------------------------------------------------------------
_STROKE_COLORS = [
    (0, 0, 255), (0, 160, 255), (0, 220, 220), (60, 220, 60),
    (255, 200, 0), (255, 120, 0), (255, 80, 160), (200, 80, 255),
    (140, 140, 255), (255, 0, 0), (0, 255, 160), (160, 160, 0),
]


def render_debug(frame_bgr, strokes, meta=None, trace_path=False, sample_step=5):
    """生成一张诊断图：底色=原图，mask 半透明叠加，骨架/笔画异色描出、标起点(圆)/终点(方)、
    按长度排序编号。trace_path=True 时再画一遍「实际走笔路径」（沿骨架直走，无摆动）。
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
            pts = [(int(s[i][0]), int(s[i][1])) for i in range(0, len(s), step)]
            if len(s) and (len(s) - 1) % step != 0:
                pts.append((int(s[-1][0]), int(s[-1][1])))
            for a, b in zip(pts, pts[1:]):
                cv2.line(base, a, b, (0, 0, 0), 1, cv2.LINE_AA)
    text = "polarity=%s strokes=%d" % (meta.get("polarity"), len(ordered))
    cv2.putText(base, text, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1, cv2.LINE_AA)
    return base


def save_debug(out_dir, tag, frame_bgr, strokes, meta=None, trace_path=True, sample_step=5):
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
                                        sample_step=sample_step))
        return written
    except Exception:
        return []


def dump_failure(out_dir, tag, frame_bgr, params=None, polarity="auto", sample_step=5):
    """识别失败时的一键留证：重跑提取取 meta，落 frame/mask/skel/strokes/trace 到 out_dir。
    返回写出的文件路径列表（供日志打印）。异常吞掉。"""
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return []
    try:
        strokes, meta = extract_debug(frame_bgr, params=params, polarity=polarity)
        return save_debug(out_dir, tag, frame_bgr, strokes, meta,
                          trace_path=True, sample_step=sample_step)
    except Exception:
        return []
