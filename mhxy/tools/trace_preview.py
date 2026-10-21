# -*- coding: utf-8 -*-
"""离线预览描摹：拿一张【拓印临摹界面】截图，跑一遍图案提取，把中间结果落盘成诊断图，
并在控制台打印 ASCII 缩略图——供人眼核对「认出来的图案是不是那两个字」以及阈值是否合适。

不需要游戏前台、不驱动鼠标，纯离线（trace_pattern 的走笔路径也能画出来看）。

用法：
    python -m mhxy.tools.trace_preview <图片路径> [选项]
    python -m mhxy.tools.trace_preview C:\\shots\\tuoying.png --area 173,128,587,469
    python -m mhxy.tools.trace_preview C:\\shots\\tuoying.png --polarity dark --ascii-width 100

选项：
    --area x,y,w,h   绘制区（图片内像素）。不给则读 config tasks.tuoying.regions.tuoying_area；再不给=整图。
    --frame dx,dy    边框补偿：把 config 读来的区域减去 (dx,dy)（客户区截图=少了标题栏/左边框时用）。
    --client-shot    自动定位游戏窗口、取其客户区偏移做边框补偿（等价于 --frame，但自动）。
    --out DIR        诊断图输出目录。默认 captures_debug/拓印描摹（vision 会再落 YYYY-MM-DD 子目录）。
    --polarity       深墨画浅底=dark / 浅线画深底=light / 自动=auto（不给则用 config 的
                     tasks.tuoying.loop.tuoying_polarity，缺省 dark）。
    --sample-step N  骨架采样间距（画走笔路径用），默认 5。
    --ascii-width N  控制台 ASCII 缩略图宽度，默认 96；0=不打印。
    --no-save        只打印、不落盘诊断图。
"""
import os
import sys

import numpy as np
import cv2

from ..core import scribble
from ..core import config as cfg_mod


def _load_image(path):
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    data = np.fromfile(path, dtype=np.uint8)      # 兼容中文路径
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("无法解码图片：%s" % path)
    return img


def _parse_area(text):
    if not text:
        return None
    parts = [p for p in text.replace("，", ",").split(",") if p.strip() != ""]
    if len(parts) != 4:
        raise ValueError("--area 需 4 个整数：x,y,w,h")
    return [int(float(p)) for p in parts]


def _config_area():
    try:
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "tuoying")
        area = (tc.get("regions") or {}).get("tuoying_area")
        return list(area) if area else None
    except Exception:
        return None


def _config_params():
    try:
        cfg = cfg_mod.load_config()
        loop = (((cfg.get("tasks", {}) or {}).get("tuoying", {}) or {}).get("loop", {}) or {})
        return scribble.params_from_loop(loop)
    except Exception:
        return None


def _config_polarity():
    try:
        cfg = cfg_mod.load_config()
        loop = (((cfg.get("tasks", {}) or {}).get("tuoying", {}) or {}).get("loop", {}) or {})
        return scribble.polarity_from_loop(loop, default="dark")
    except Exception:
        return "dark"


def detect_client_frame():
    """定位游戏窗口并返回其客户区【相对窗口 rect】的偏移 (dx, dy)=左边框+标题栏。

    离线预览常拿到的是**客户区截图**（不含标题栏/边框），而 config 里的区域是相对整窗 rect 存的，
    二者原点差这一截；把区域减去它即可对上。定位不到游戏窗口返回 None。
    """
    try:
        from ..core import window as win_mod
        cfg = cfg_mod.load_config()
        win_mod.set_game_process(cfg.get("window_process", "MyGame_x64r.exe"))
        gw = win_mod.GameWindow(cfg.get("window_title", "梦幻西游"),
                                cfg.get("window_offset", [0, 0]))
        if gw.locate():
            return gw.client_frame()
    except Exception:
        pass
    return None


def config_client_frame():
    """config 里配的边框补偿兜底值 (dx, dy)（tasks.tuoying.loop.tuoying_client_frame，缺省 (8,31)）。
    游戏没在跑、detect_client_frame() 拿不到时用它——用户的离线截图通常是不含标题栏/边框的客户区。"""
    try:
        cfg = cfg_mod.load_config()
        v = (((cfg.get("tasks", {}) or {}).get("tuoying", {}) or {}).get("loop", {}) or {}).get("tuoying_client_frame")
        if v and len(v) == 2:
            return (int(v[0]), int(v[1]))
    except Exception:
        pass
    return (8, 31)


def resolve_client_frame():
    """给离线预览用：优先实测游戏窗口，其次用 config 兜底。返回 ((dx,dy), 来源字符串)。"""
    f = detect_client_frame()
    if f:
        return f, "实测游戏窗口"
    return config_client_frame(), "config 兜底"


def _ascii(img, width=96, title=""):
    """把二值/灰度图降采样成字符画（#=前景，空=背景），供人眼/日志快速判断形状。"""
    if img is None or img.size == 0 or width <= 0:
        return ""
    h, w = img.shape[:2]
    if w > width:
        nh = max(1, int(h * width / w * 0.5))     # 字符高宽比 ~2:1
        small = cv2.resize(img, (width, nh), interpolation=cv2.INTER_AREA)
    else:
        small = img
    if small.ndim == 3:
        small = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    lines = []
    if title:
        lines.append(title)
    for row in small:
        lines.append("".join("#" if int(v) > 127 else ("." if int(v) > 40 else " ") for v in row))
    return "\n".join(lines)


def _strokes_ascii(strokes, shape, width=96, title=""):
    """把笔画路径画到黑底上再转字符画——直接看「认出来的骨架长什么样」。"""
    if not strokes or width <= 0:
        return title or ""
    h, w = shape
    canvas = np.zeros((h, w), dtype=np.uint8)
    for s in strokes:
        pts = np.array([[int(c), int(r)] for (c, r) in s], dtype=np.int32)
        if len(pts) >= 2:
            cv2.polylines(canvas, [pts], False, 255, 1)
    return _ascii(canvas, width=width, title=title)


def _draw_grid(img, step=50):
    """在图上画带坐标标注的网格（每 step 像素一条），便于人眼看图报出图案像素范围。"""
    out = img.copy()
    h, w = out.shape[:2]
    for x in range(0, w, step):
        cv2.line(out, (x, 0), (x, h), (0, 220, 0), 1)
        cv2.putText(out, str(x), (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 0), 1, cv2.LINE_AA)
    for y in range(0, h, step):
        cv2.line(out, (0, y), (w, y), (0, 220, 0), 1)
        cv2.putText(out, str(y), (2, y + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 200, 0), 1, cv2.LINE_AA)
    return out


def _bbox_all(strokes):
    pts = [p for s in strokes for p in s]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def run(image_path, area=None, out_dir=None, polarity=None, sample_step=5.0,
        params=None, ascii_width=96, save=True, tag_prefix="preview", grid=False, stream=None,
        client_frame=None):
    """跑一遍提取 + 落盘 + ASCII 预览。返回 summary dict。stream=None 时打印到 stdout。
    polarity=None 时用 config 的 tasks.tuoying.loop.tuoying_polarity（默认 dark）。
    client_frame=(dx,dy) 时：对【从 config 读来的区域】减去该偏移，把它从「整窗 rect 坐标」换算到
    「客户区截图坐标」（离线预览给的常是不含标题栏/边框的客户区截图）。显式传 area= 时不套用。"""
    out = stream if stream is not None else sys.stdout

    def _p(msg=""):
        print(msg, file=out)

    img = _load_image(image_path)
    H, W = img.shape[:2]
    from_config = area is None
    if from_config:
        area = _config_area()
    applied_frame = None
    if from_config and area and client_frame:
        fx, fy = int(client_frame[0]), int(client_frame[1])
        area = [int(area[0]) - fx, int(area[1]) - fy, int(area[2]), int(area[3])]
        applied_frame = (fx, fy)
    use_full = area is None
    if use_full:
        rect = [0, 0, W, H]
    else:
        x, y, w, h = [int(v) for v in area]
        x = max(0, min(x, W - 1))
        y = max(0, min(y, H - 1))
        w = max(1, min(w, W - x))
        h = max(1, min(h, H - y))
        rect = [x, y, w, h]
    x, y, w, h = rect
    frame = img[y:y + h, x:x + w]

    if params is None:
        params = _config_params()
    if polarity is None:
        polarity = _config_polarity()
    strokes, meta = scribble.extract_debug(frame, polarity=polarity, params=params)

    _p(f"图片：{image_path}  ({W}x{H})")
    _p(f"绘制区：{'整图' if use_full else rect}"
       + (f"  [边框补偿 -{applied_frame[0]},-{applied_frame[1]}]" if applied_frame else ""))
    _p(f"提取：polarity={meta.get('polarity')}  笔画数={len(strokes)}  "
       f"mask前景占比={0.0 if meta.get('mask') is None else float((meta['mask'] > 0).mean()):.4f}")
    for i, s in enumerate(sorted(strokes, key=len, reverse=True)):
        _p(f"  笔画#{i}: 采样点={len(s)}  起点={s[0]}  终点={s[-1]}")
    if strokes:
        _p(f"  合并包围盒(x,y,w,h)={_bbox_all(strokes)}")

    if save:
        od = out_dir or scribble.debug_dir()
        tag = scribble.debug_tag(tag_prefix)
        files = scribble.save_debug(od, tag, frame, strokes, meta,
                                    trace_path=True, sample_step=sample_step)
        if grid:
            try:
                from ..core import vision
                g = _draw_grid(scribble.render_debug(frame, strokes, meta, trace_path=False))
                gp = os.path.join(od, tag + "_grid.png")
                if vision.save_image(gp, g):
                    files.append(gp)
            except Exception:
                pass
        for f in files:
            _p("已写出：" + f)
    else:
        files = []

    if ascii_width and ascii_width > 0:
        _p("")
        _p(_ascii(meta.get("mask"), width=ascii_width, title="== mask 前景(局部对比) =="))
        _p("")
        _p(_strokes_ascii(strokes, frame.shape[:2], width=ascii_width, title="== 识别出的笔画骨架 =="))

    return {"rect": rect, "polarity": meta.get("polarity"),
            "strokes": len(strokes), "bbox": _bbox_all(strokes) if strokes else None,
            "files": files if save else []}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    image_path = argv[0]
    opts = {"area": None, "out_dir": None, "polarity": None,
            "sample_step": 5.0, "ascii_width": 96, "save": True, "tag_prefix": "preview",
            "params": None, "grid": False, "client_frame": None}
    overrides = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--area":
            opts["area"] = _parse_area(argv[i + 1]); i += 2
        elif a == "--out":
            opts["out_dir"] = argv[i + 1]; i += 2
        elif a == "--polarity":
            opts["polarity"] = argv[i + 1]; i += 2
        elif a == "--sample-step":
            opts["sample_step"] = float(argv[i + 1]); i += 2
        elif a == "--ascii-width":
            opts["ascii_width"] = int(argv[i + 1]); i += 2
        elif a == "--no-save":
            opts["save"] = False; i += 1
        elif a == "--frame":
            parts = [int(float(p)) for p in argv[i + 1].replace("，", ",").split(",") if p.strip() != ""]
            if len(parts) != 2:
                print("--frame 需 2 个整数：dx,dy"); return 2
            opts["client_frame"] = (parts[0], parts[1]); i += 2
        elif a == "--client-shot":
            opts["client_frame"] = resolve_client_frame()[0]; i += 1
        elif a == "--grid":
            opts["grid"] = True; i += 1
        elif a == "--tag":
            opts["tag_prefix"] = argv[i + 1]; i += 2
        elif a == "--bg-kernel":
            overrides["bg_kernel"] = int(float(argv[i + 1])); i += 2
        elif a == "--ink-contrast":
            overrides["contrast_min"] = float(argv[i + 1]); i += 2
        elif a == "--ink-frac":
            overrides["contrast_frac"] = float(argv[i + 1]); i += 2
        elif a == "--min-area-ratio":
            overrides["min_area_ratio"] = float(argv[i + 1]); i += 2
        elif a == "--skel-max-dim":
            overrides["skel_max_dim"] = int(float(argv[i + 1])); i += 2
        else:
            print("未知选项：" + a); return 2
    if overrides:
        base = dict(_config_params() or {})
        base.update(overrides)
        opts["params"] = base
    try:
        run(image_path, **opts)
    except Exception as e:
        print("出错：%s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
