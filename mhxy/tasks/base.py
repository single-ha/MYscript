# -*- coding: utf-8 -*-
"""
任务基类与注册表。

约定：每个任务继承 Task，实现 _run(ctx)（run 由基类统一：先确保目标窗口回到主界面，再进 _run；
若任务启动需要非主界面状态，覆盖 ENSURE_MAIN_ON_START=False 跳过那步），并在 _run 的循环里频繁检查
ctx.should_stop()。任务通过 ctx.log() 输出日志、ctx.window/ctx.mouse 操作游戏，绝不直接引用 GUI。

Task 基类还提供一组「与玩法无关」的纯工具方法（可被停止的等待、帧差判静止、点区域、
存截图、抖动、管理员检测），供所有任务复用，避免每个任务各抄一份。
"""

import time
import ctypes
import datetime
import random

import numpy as np

from ..core import vision
from ..core import window as win_mod
from ..core import rotation
from ..core.config import CAPTURES_DIR

_REGISTRY = {}


def register(cls):
    """类装饰器：把任务登记进注册表。"""
    _REGISTRY[cls.name] = cls
    return cls


def get_task(name):
    return _REGISTRY.get(name)


def all_tasks():
    """按注册顺序返回任务类列表。"""
    return list(_REGISTRY.values())


def dungeon_tasks():
    """按注册顺序返回所有「副本」任务类（is_dungeon=True）。
    刷副本页据此自动列出可选副本——新增副本只需在任务类上标 is_dungeon=True 即自动出现，
    GUI 不用改。以后做「连续刷多个副本」时也以此为候选清单。"""
    return [c for c in _REGISTRY.values() if getattr(c, "is_dungeon", False)]


class Task:
    name = "base"          # 唯一标识（英文，作为 config.tasks 的键）
    title = "基础任务"      # 界面显示名
    description = ""        # 一句话说明
    is_dungeon = False      # True=可在「刷副本」页被当作一个副本选中运行（见 dungeon_tasks）
    # True=支持「日常一条龙·多开每窗口独立链」：实现 make_chain_driver(wctx) 暴露
    #   「每窗口一份 record + 单步推进函数」，与本任务自己的 run()/轮转共用同一套状态机。
    #   需跨窗口协作的任务（如组队副本）保持 False，由一条龙当「集体屏障」处理。
    CHAINS_PER_WINDOW = False
    # True=在「日常一条龙·多开」里该任务按【逐号顺序执行】：一个号把它从头到尾做完
    # （record done）才轮到下一个号做，不做跨号轮转——配合 daily 的 _drive_chain_until_yield(blocking=True)。
    #   适合「短而快、频繁切号反而低效/凌乱」的任务（三界奇缘/趣味鉴赏）；
    #   只影响日常一条龙的链推进，任务自己单独跑的行为不变（见各任务 _run）。
    CHAIN_SEQUENTIAL = False
    # True=开跑前先把目标窗口带回主界面（run() 入口统一做；找不到主界面只打日志不拦任务）。
    #   特殊任务（如拓印「演练」需拓印弹窗已在前台）覆盖为 False，启动时绝不 ESC 关它。
    ENSURE_MAIN_ON_START = True

    # 标定向导（calibrate_dialog）按此 spec 驱动渲染。子类覆盖：
    #   {"regions":  [(key, 显示名, 说明), ...],     # 框选区域，写入 tc["regions"][key]
    #    "templates":[(key, 显示名, 说明), ...],     # 框选裁图存模板，写入 tc["templates"][key]
    #    "watchlist": bool}                          # 是否显示「装备清单」卡片（秒装备专用）
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    def run(self, ctx):
        """任务入口（子类不要覆盖它）：开跑前先把目标窗口带回主界面（ENSURE_MAIN_ON_START=False 除外），
        再进 self._run(ctx) 干正事。子类实现 _run 而不是 run。"""
        if self.ENSURE_MAIN_ON_START:
            try:
                self._ensure_main_screen(ctx)
            except Exception as e:
                ctx.log(f"启动前确保主界面异常（已忽略，继续）：{e}", level="warn")
        return self._run(ctx)

    def _run(self, ctx):
        """任务主体。会在后台线程里执行；需自行在循环中检查 ctx.should_stop()。"""
        raise NotImplementedError

    def _ensure_main_screen(self, ctx):
        """启动前把选中/已绑定的目标窗口带回主界面：已绑定直接用；没绑定先选一个（_acquire_target_window），
        切前台后走 ui_state.back_to_main_screen 逐层 ESC 关面板直到主界面。失败只打日志，不拦任务。"""
        from ..ui import ui_state
        if ctx.window.rect() is None and not self._acquire_target_window(ctx):
            ctx.log("启动前没有可用窗口，先不确保主界面，任务自行处理。", level="warn")
            return
        if not ctx.window.activate():
            ctx.log("启动前切前台失败，本次跳过确保主界面（任务流程仍继续）。", level="warn")
            return
        self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
        st = ui_state.back_to_main_screen(ctx.cfg, ctx.window)
        if st is True:
            ctx.log("已确认在主界面，开跑。", level="info")
        elif st is None:
            ctx.log("无法判断主界面（大概率没标定「商城图标」）——可在「通用」页标定公共区域，本次照旧开跑。",
                    level="warn")
        else:
            # 回不去：存一张现场截图，方便核对到底是「面板真没关掉」还是「其实已主界面但商城图标没认到」。
            rect = ctx.window.rect()
            scene = win_mod.grab(rect) if rect is not None else None
            cap = self._save_capture(scene, "main_screen_fail") if scene is not None else None
            ctx.log(f"未能回到主界面（面板没关掉或商城图标没认到），截图 {cap} 供核对，按任务原流程继续。",
                    level="warn")

    def preflight(self, ctx):
        """启动前自检。返回 (ok: bool, problems: list[str])。默认通过。"""
        return True, []

    # ------------------------------------------------------------------
    # 与玩法无关的共享工具（秒装备/刷副本等都用）
    # ------------------------------------------------------------------
    def _is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return True  # 非 Windows 或查询失败时不打扰

    @staticmethod
    def _window_no(w, fallback):
        """窗口的真实号数（1 起）：优先用全局序号（单开选了号2→报'号2'），拿不到退回 fallback+1。
        标签统一入口，避免日志里把选中列表当号数。"""
        return win_mod.global_no(w, fallback)

    def _acquire_target_window(self, ctx):
        """基础特性：把 ctx.window 指向「选择窗口」里选中的目标窗口，并保持有效。

        - 已绑定且窗口仍有效 → 直接返回 True（快路径，不重复枚举）。
        - 否则按 targets 配置重新选择并绑定（单开=选中那个号；多开暂取第一个，
          多号顺序跑后续支持）。找不到任何目标窗口返回 False。

        供有状态任务（运镖/宝图）替代原来每轮 `ctx.window.locate()`（那会自动选最大、
        无法指定号），让它们也走「选择窗口」这条基础路径。"""
        if ctx.window.rect() is not None:
            return True
        wins = ctx.select_windows()
        if not wins:
            return False
        ctx.window = wins[0]
        return ctx.window.rect() is not None

    def _jitter(self, base, ctx):
        r = ctx.cfg.get("humanize", {}).get("interval_jitter", 0.4)
        return max(0.05, base * (1 + random.uniform(-r, r)))

    def _interruptible_sleep(self, ctx, seconds):
        """可被停止打断的等待。"""
        end = time.time() + seconds
        while time.time() < end:
            if ctx.should_stop():
                return
            time.sleep(min(0.05, max(0.0, end - time.time())))

    def _find_join_ready(self, ctx, rec, list_region, entry_xy, threshold, loop, entry_tpl=None):
        """找「参加」按钮。认出卡片但按钮没匹配上，最常见原因是卡片贴着列表区域上/下缘只露了半张、
        按钮那半在裁剪线外（或按钮与条目中心有纵向错位、没进查找条带）。别猜精确几何：只要没找到
        按钮，就朝「让被裁那半滚进来」的方向微滚一格再找（方向按条目在区域上/下半区粗判），返回
        "nudged" 让调用方原地重试；连续微滚 nudge_max 次仍找不着 → 返回 None 交由调用方告警一次+
        原地重试（scroll_search 的 STAY）。微滚计数记在 rec['_nudges']，命中/切到新屏即归零。
        子类须已实现 _find_join_on_row。返回：命中=(x,y,score)；微滚过="nudged"；否则 None。"""
        rec.setdefault("_join_warned", False)
        join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop)
        if join is not None:
            rec["_nudges"] = 0
            return join
        if rec.setdefault("_nudges", 0) < loop.get("nudge_max", 4):
            dirn = self._join_clip_dir(ctx, list_region, entry_xy, entry_tpl)
            if dirn is not None:
                rec["_nudges"] += 1
                self._nudge_list(ctx, list_region, dirn, loop)
                return "nudged"
            # 条目在区域内完整显示仍找不到按钮：不计入连拍（归零），由调用方告警+原地重试
            rec["_nudges"] = 0
        return None

    def _find_join_in_column(self, ctx, list_region, entry_screen_xy, threshold, loop,
                             join_tpl, entry_tpl=None, max_follow_cap=None):
        """按「整列找参加按钮、取离卡片行最近那枚」定位——比纵向窄条裁剪稳得多。命中返回
        (screen_x, screen_y, score)，否则 None。

        旧法只搜「条目中心上下 ±条带高」的窄条，但游戏里卡片「参加」按钮常与图标/文字条带
        有 ~20px 级纵向错位（按钮贴卡片中下部），错位稍大按钮就整枚漏在条带外（实测即此）。
        这里改为：在条目所在【整列】里用 match_multi 枚举全部 join 模板命中（阈值取
        max(threshold, 0.7) 滤掉背景噪声——实测真按钮 0.90+，噪点 ~0.38），按「垂直距离
        离条目中心最近的按钮」取目标，且距离须 ≤ max_follow（≈0.55×该列行距中位数、下限
        60px）——超出容差 = 该行按钮不在可视区（卡片贴列表边缘被裁/按钮状态不同），返回
        None 交由上层微滚或告警。整列裁剪只扫条目所在列，天然满足「卡片列内找、不跨列点
        右邻」。

        坑（实测踩过）：列里某些卡片的按钮当帧没匹配上（分数<阈值/状态不同），整列命中变得
        稀疏，行距中位数被拉大 → max_follow 被撑到 ~95px，于是「最近那枚」会变成**隔壁行**的
        按钮（曾 d=65 点到上一行运镖卡的「参加」进错活动）。传 max_follow_cap（如 55）把容差
        钉死在卡片一行之内：本行按钮没匹配上就返回 None，宁可靠上层微滚重试，也不顺藤点隔壁。"""
        if join_tpl is None:
            ctx.log("找「参加」失败：join 模板未标定。", level="warn")
            return None
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        rx, ry = rect[0], rect[1]
        sw, sh = scene.shape[1], scene.shape[0]
        ex_local = int(entry_screen_xy[0] - rx)
        ey_local = int(entry_screen_xy[1] - ry)
        cols = max(1, int(loop.get("activity_columns", 2)))
        col_w = sw / cols
        col_idx = min(cols - 1, max(0, int(ex_local // col_w)))
        x0 = max(0, int(round(col_idx * col_w)))
        x1 = min(sw, int(round((col_idx + 1) * col_w)))
        if x1 - x0 < 1:
            return None
        col = scene[:, x0:x1]
        lo_thr = max(float(threshold), 0.7)
        hits = vision.match_multi(col, join_tpl, lo_thr, max_hits=64, sort_origin_top_left=True)
        if not hits:
            return None
        ys = sorted(h[1] for h in hits)
        gaps = sorted(ys[i + 1] - ys[i] for i in range(len(ys) - 1))
        gap = gaps[len(gaps) // 2] if gaps else 0
        max_follow = max(60.0, float(gap) * 0.55)
        if max_follow_cap:
            max_follow = min(max_follow, float(max_follow_cap))
        best, best_d = None, None
        for (cx, cy, s) in hits:
            d = abs(cy - ey_local)
            if best_d is None or d < best_d:
                best, best_d = (cx, cy, s), d
        if best is not None and best_d > max_follow:
            now = time.time()
            last = getattr(self, "_join_follow_warn_ts", 0.0)
            if now - last > 20:  # 单次只告警一次，避免每帧刷屏（有卡片时每轮都可能重试）
                self._join_follow_warn_ts = now
                ctx.log(
                    f"卡片在 ({ex_local},{ey_local})，该列「参加」按钮命中在 y={ys}，最近的距 "
                    f"{best_d:.0f}px 越出本行带(±{max_follow:.0f}px)——本行按钮当帧没匹配上，"
                    "拒绝点击隔壁行的按钮，微滚/原地重试。", level="warn")
            return None
        if best is None:
            return None
        cx, cy, s = best
        return (rx + x0 + cx, ry + cy, s)

    def _join_clip_dir(self, ctx, list_region, entry_screen_xy, entry_tpl):
        """判定条目是否可能贴着列表区域上/下缘被裁剪，给出微滚方向：
        "top"=向上滚(露出上一行) / "bottom"=向下滚(露出下一行) / None。
        阈值放宽到两行高（条目模板常很小、缺口比一行宽就漏判了）：条目中心距上/下缘 < 两行高
        即认为可能被裁；方向按条目在区域上下哪个半区决定（贴下缘多半向下滚、贴上缘多半向上滚）。"""
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return None
        scene = win_mod.grab(rect)
        if scene is None:
            return None
        sh = scene.shape[0]
        if sh < 1:
            return None
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        margin = max(40, int(row_h * 2))
        ey_local = int(entry_screen_xy[1] - rect[1])
        near_top = ey_local < margin
        near_bot = sh - ey_local < margin
        if not near_top and not near_bot:
            return None
        return "top" if ey_local <= sh // 2 else "bottom"

    def _nudge_list(self, ctx, list_region, dirn, loop):
        """把列表朝「让越界那半滚进来」的方向微滚一格。dirn: 'top'=向上滚（露出上一行），
        'bottom'=向下滚（露出下一行）。格数取 nudge_step（默认同 scroll_step 幅值）。"""
        rect = (ctx.window.region_to_screen_rect(list_region)
                if list_region else ctx.window.rect())
        if rect is None:
            return
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        step = int(loop.get("nudge_step", abs(int(loop.get("scroll_step", -3)))))
        step = 1 if step < 1 else step
        delta = -step if dirn == "bottom" else step
        ctx.log(f"卡片贴着列表边缘({dirn})，微滚 {step} 格把被裁那半露出再找「参加」…", level="info")
        ctx.mouse.scroll(delta, cx, cy)

    def _make_rotation(self, ctx, records, step_fn, multi, switch_delay, tick, time_limit=0):
        """把「逐号 activate 切前台 + 非阻塞状态机推进」这套多开轮转包成 rotation.RotationConfig。

        运镖/宝图/秘境共用此辅助：
          · 窗口消失 → skip 不置 done（窗口可能恢复、下轮重试，节流告警）；
          · activate 失败 → 跳过该号本轮、节流告警；
          · time_limit>0 时作为总超时，并打各任务「到点自停」文案（非「降级」）。
        各任务只需传一个 step_fn(rec)（闭包绑定自己的 loop/regions/threshold）。record 须含
        ctx/state/done/dead_logged 字段（推进器用默认 get_ctx/get_state/is_done 读取）。
        让出判据见 core/rotation.py：step 后 state 没变=在等待就让出，监控态盯屏天然不空转。"""
        def on_window_gone(rec):
            if not rec["dead_logged"]:
                rec["ctx"].log("目标窗口不见了，跳过该号（其余号继续）。", level="warn")
                rec["dead_logged"] = True
            return "skip"

        def on_activate_fail(rec):
            if not rec.get("fg_warned"):
                rec["ctx"].log("未能切到前台（系统拒绝焦点抢占），本轮跳过、下轮重试。", level="warn")
                rec["fg_warned"] = True

        def step_once(rec):
            rec["fg_warned"] = False        # 能进来=activate 成功+窗口在 → 重置节流标志
            rec["dead_logged"] = False
            step_fn(rec)

        def between_steps(rec):
            # 每号切前台后、推进前：检测背包满则自动整理（开关在 tasks.organize_bag.auto_organize）。
            # 所有走 _make_rotation 的任务（运镖/宝图/秘境/副本）由此统一获得「背包满自动整理」。
            try:
                rec["ctx"].maybe_auto_organize()
            except Exception as e:
                rec["ctx"].log(f"自动整理背包检测异常（已忽略，继续任务）：{e}", level="warn")

        return rotation.RotationConfig(
            records=records, step_once=step_once,
            should_stop=ctx.should_stop, log=ctx.log,
            on_window_gone=on_window_gone, on_activate_fail=on_activate_fail,
            between_steps=between_steps,
            multi=multi, switch_delay=switch_delay, tick=tick,
            overall_timeout=(time_limit * 60 if time_limit > 0 else 0),
            timeout_msg=(f"已达时间上限 {time_limit} 分钟，停止。" if time_limit > 0 else None),
            jitter_ratio=ctx.cfg.get("humanize", {}).get("interval_jitter", 0.4))

    def _run_rotation_sequential(self, ctx, records, step_fn, multi, switch_delay, tick, time_limit=0):
        """多号【顺序执行】版轮转：一个号完整跑完（done）再切下一个号，不做跨号并行轮转。

        三界奇缘 / 趣味鉴赏这类「从头到尾一口气做一个号、再轮下个号」的任务走这套
        （用户拍板：多号轮询时一个号完成之后再继续下一个号）。单开（records 只有一个）
        行为与并行版完全一致。参数含义同 _make_rotation；time_limit 仍按整个多号过程的
        总上限计算，顺次跑时把剩余预算转给后面的号（到点自停 + 各任务文案）。"""
        start = time.time()
        for i, rec in enumerate(records):
            if ctx.should_stop():
                break
            if time_limit > 0:
                remain = time_limit * 60 - (time.time() - start)
                if remain <= 0:
                    ctx.log("已达总时间上限，停止。", level="warn")
                    break
                remain_min = max(0.001, remain / 60.0)
            else:
                remain_min = 0
            if i > 0:
                cur = getattr(rec["ctx"], "label", None) or f"号{i + 1}"
                ctx.log(f"── 上一个号已完成，轮到 {cur} ──", level="hit")
            rotation.run_rotation(self._make_rotation(
                ctx, [rec], step_fn, multi, switch_delay, tick, remain_min))
            if ctx.should_stop():
                break

    @staticmethod
    def _frame_diff(a, b):
        """两帧平均像素绝对差。形状不一致返回大值（视为仍在变化）。"""
        if a is None or b is None or a.shape != b.shape:
            return 999.0
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())

    def _click_region(self, ctx, region, speed=None):
        if not region:
            return False
        center = ctx.window.region_center_screen(region)
        if center is None:
            return False
        ctx.mouse.click(center[0], center[1], speed=speed)
        return True

    def _save_capture(self, scene, name):
        fname = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_") + str(name) + ".png"
        vision.save_image(str(CAPTURES_DIR / fname), scene)
        return fname

    def _wait_still(self, ctx, rect, min_sec=0.3, max_sec=2.0, stable_diff=1.5, poll=0.06):
        """自适应等画面静止：先等 min_sec，再每隔 poll 截一帧比上一帧，
        两帧平均像素差 < stable_diff 即认为静止、立即返回该帧；
        超过 max_sec 仍在变化则返回最后一帧。被停止返回 None。"""
        self._interruptible_sleep(ctx, min_sec)
        if ctx.should_stop():
            return None
        prev = win_mod.grab(rect)
        deadline = time.time() + max(0.0, max_sec - min_sec)
        while time.time() < deadline:
            if ctx.should_stop():
                return None
            time.sleep(poll)
            cur = win_mod.grab(rect)
            if cur is None:
                return prev
            if self._frame_diff(prev, cur) < stable_diff:
                return cur
            prev = cur
        return prev
