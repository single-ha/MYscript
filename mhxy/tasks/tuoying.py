# -*- coding: utf-8 -*-
"""
拓印（全局共享的临摹校验能力；工具页可单独跑一遍完整临摹）。

玩法：刷副本点「进入」后，队长窗口偶发弹「拓印」临摹界面——需按住鼠标沿随机图案描一遍再点「上传」。

本任务封装成可在「工具」页「拓印」里单独跑一遍完整临摹（user 2026-10-08 拍板，去掉原来的
「只描不传」演练性质——现在描完会**自动点「上传」**并按状态判定是否描下一遍）：对所选窗口第一个号先切前台，
在其「拓印临摹绘制区」里识别图案并沿骨架描一遍 → 点「上传」→ 判定「新图案已生成」还是「界面关闭」（见下）→
可能要多描多传几遍。

判定（user 2026-10-17 拍板）：拓印弹窗点「上传」后**通常不关闭**、界面标题一直在，故不能靠「等标题消失」。
改标一个「未完成状态按钮」（临摹未完成时按钮的样子，描完会变「完成」态）——点「上传」后新图案生成时它重现，
据此判定「该描下一遍」；服务器判定通过后弹窗才自动关闭，则判完成。未标该按钮时退回旧的「等标题消失」判据。

与刷副本共用一套资产与手感：
  · 标定资产存 tasks.tuoying（绘制区 + 标题/上传/未完成状态按钮模板，在「拓印」页标一次，所有副本共用）
  · 描摹手感参数读 tasks.dungeon.loop（tuoying_lateral/tuoying_sample_step/tuoying_passes 等），
    图案提取参数（tuoying_polarity/ink_contrast/... 见 core/scribble.DEFAULTS）读 tasks.tuoying.loop，
    保证这里的手感=副本内自动临摹的手感。
  · 识别失败时把 frame/mask/skel/strokes/trace 诊断图落 captures_debug/拓印描摹/（tuoying_save_debug 可关）。
完整自动流程（探测 → 描 → 上传 → 确认界面不再重现的循环）的参照实现见 dungeon_base._auto_trace。
资产只读 tasks.tuoying（旧 tasks.shared / tasks.dungeon 残留值不沿用）。
"""

import time

from ..core import scribble
from ..core import vision
from .base import Task, register

_NS = "tuoying"
_DUNGEON_NS = "dungeon"

_debug_dir = scribble.debug_dir
_debug_tag = scribble.debug_tag


@register
class TuoyingTask(Task):
    name = "tuoying"
    title = "拓印"
    ENSURE_MAIN_ON_START = False  # 拓印界面要已在前台（工具页独立跑的场合），启动时绝不 ESC 关它
    description = "刷副本「拓印」临摹弹窗的自动描摹能力；这里可单独跑一遍完整临摹（描+上传）"

    CALIBRATION = {
        "regions": [
            ("tuoying_area", "拓印描摹绘制区(必标)", "「拓印」临摹界面里图案所在那块区域：自动描摹就是按住"
             "鼠标沿图案描一遍；所有副本共用，标一次即可。", False),
        ],
        "templates": [
            ("tuoying_title", "「拓印」界面标题", "(可选但建议)刷副本点「进入」后，队长窗口偶尔会弹「拓印」临摹界面："
             "框标题或独特边框即可识别。所有副本共用。标了=上传后能自动确认界面已关闭/还会重现再描；"
             "不标=上传后按固定等待兜底。", True),
            ("tuoying_upload", "「上传」按钮", "(必标)拓印临摹完要点的「上传」按钮。所有副本共用。"
             "工具页独立跑&副本内全自动都靠它提交，标好不会卡在手动。", False),
            ("tuoying_todo", "「未完成」状态按钮", "(可选但强烈建议)临摹未描完时按钮的样子——描完它会变成「完成」态。"
             "别填「上传」按钮：框那个随描摹进度变样的状态按钮的「未完成」外观。"
             "标了=点「上传」后靠它判定「新图案已生成」→ 立刻描下一遍，比等标题消失快得多、也更准"
             "（拓印弹窗上传后通常不关、标题一直在）。不标=退回旧判据（等标题消失，通常只能靠超时）。", True),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        if not (tc.get("regions") or {}).get("tuoying_area"):
            problems.append("还没标定「拓印描摹绘制区」——请先点「标定（拓印）」框选它")
        if not (tc.get("templates") or {}).get("tuoying_upload"):
            problems.append("还没标定「上传」按钮——真执行要靠它自动提交临摹，请先点「标定（拓印）」框选")
        if not ctx.select_windows():
            problems.append("没选到任何目标窗口 —— 请先「选择窗口」")
        return (len(problems) == 0), problems

    def _run(self, ctx):
        tasks = (ctx.cfg or {}).get("tasks", {}) or {}
        tuo = (tasks.get(_NS, {}) or {})
        tpl_cfg = (tuo.get("templates", {}) or {})
        area = (tuo.get("regions", {}) or {}).get("tuoying_area")
        # 标题/上传模板只读 tasks.tuoying 的新值（不沿 old shared，与 dungeon_base._load_flags 同原则）。
        up_tpl = vision.load_template(tpl_cfg.get("tuoying_upload")) if tpl_cfg.get("tuoying_upload") else None
        title_tpl = vision.load_template(tpl_cfg.get("tuoying_title")) if tpl_cfg.get("tuoying_title") else None
        todo_tpl = vision.load_template(tpl_cfg.get("tuoying_todo")) if tpl_cfg.get("tuoying_todo") else None
        loop = (ctx.task_cfg(_DUNGEON_NS).get("loop", {}) or {})   # 描摹手感参数与副本共用一套（手感一致）
        tuo_loop = (tuo.get("loop", {}) or {})                     # 图案提取参数在拓印工具自己命名空间
        params = scribble.params_from_loop(tuo_loop)
        polarity = scribble.polarity_from_loop(tuo_loop)
        save_debug = scribble.debug_enabled(tuo_loop)
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没选到任何目标窗口，已停止。", level="error")
            return
        if not self._is_admin():
            ctx.log("⚠ 当前非管理员：游戏前台时鼠标注入可能被拦截，建议以管理员重开。", level="warn")

        # 拓印只由队长弹，这里取第一个选中窗口操作。
        w = wins[0]
        if not w.activate():
            ctx.log("切前台失败（系统拒绝焦点抢占），已停止。请手动把窗口调到前台后重试。", level="warn")
            return
        # 整个拓印阶段挂起弹窗守卫（_trace_active，见 base._defuse_popup）：拓印窗看似弹窗但没有
        # 可点掉的「×」，误让守卫点它会把「上传后再弹」的续描流程打掉。
        self._trace_active = True
        try:
            return self._run_trace(ctx, w, area, loop, up_tpl, title_tpl, todo_tpl, params, polarity, save_debug)
        finally:
            self._trace_active = False

    def _run_trace(self, ctx, w, area, loop, up_tpl, title_tpl, todo_tpl, params, polarity, save_debug):
        self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
        ctx.window = w
        win_rect = w.rect()
        if win_rect is None:
            ctx.log("窗口定位失败，已停止。", level="error")
            return
        rect = w.region_to_screen_rect(area) if area else win_rect
        if rect is None:
            ctx.log("绘制区换算失败（窗口未定位），已停止。", level="error")
            return

        # 先确认识别得出图案，绝不盲描。
        frame = ctx.window.grab_screen(rect)
        if frame is None:
            ctx.log("绘制区截图失败，请把「拓印」临摹界面调到前台再试。", level="warn")
            return
        if not scribble.has_pattern(frame, label="拓印绘制区", polarity=polarity, params=params):
            ctx.log("绘制区里没认出图案笔画，绝不盲描。请把「拓印」临摹界面调到前台再试。", level="warn")
            if save_debug:
                files = scribble.dump_failure(_debug_dir(), _debug_tag("tuoying_nopat"), frame,
                                              params=params, polarity=polarity)
                if files:
                    ctx.log(f"已存识别失败调试图（frame/mask/skel/strokes/trace）：{files[0]}", level="info")
            return
        self._trace_loop(ctx, loop, rect, win_rect, up_tpl, title_tpl, todo_tpl, params, polarity, save_debug)

    def _tuoying_max_rounds(self, ctx, loop):
        """临摹「多少遍」的设置归拓印工具自己（tasks.tuoying.loop.tuoying_max_rounds）。
        读不到时用旧 tasks.dungeon.loop 值兜底（迁移后一般不存在）。"""
        tuo = ((ctx.cfg or {}).get("tasks", {}) or {}).get("tuoying", {}) or {}
        v = ((tuo.get("loop", {}) or {}).get("tuoying_max_rounds")) or loop.get("tuoying_max_rounds", 2)
        return max(1, int(v))

    def _trace_loop(self, ctx, loop, rect, win_rect, up_tpl, title_tpl, todo_tpl, params, polarity, save_debug):
        """完整临摹循环：沿图案描 → 点「上传」→ 判定「新图案就绪」还是「界面关闭」，可能要多描多传几遍。
        判据与 dungeon_base._auto_trace 一致；这里是「手动把临摹界面调到前台再跑」的单窗版。"""
        ctx.log("拓印：按标定绘制区自动沿图案描一遍并点「上传」，跑完整次临摹…", level="warn")
        max_rounds = self._tuoying_max_rounds(ctx, loop)
        confirm_sec = loop.get("tuoying_gone_confirm_sec", 1.2)
        upload_timeout = loop.get("tuoying_upload_sec", 6.0)
        lateral = loop.get("tuoying_lateral", 3.0)
        sample_step = loop.get("tuoying_sample_step", 5.0)
        for rnd in range(1, max_rounds + 1):
            if ctx.should_stop():
                return
            ctx.log(f"拓印临摹（第 {rnd}/{max_rounds} 遍）：沿图案骨架描 {loop.get('tuoying_passes', 2)} 轮…",
                    level="warn")
            for _pass in range(max(1, int(loop.get("tuoying_passes", 2)))):
                if ctx.should_stop():
                    return
                # 每遍先截绘制区当前画面：识别图案笔画像素，只沿图案描（描到图案外会拉低完成度）。
                frame = ctx.window.grab_screen(rect)
                if frame is None:
                    ctx.log("⚠ 绘制区截图失败，未描摹。请确认临摹界面已弹出。", level="warn")
                    return
                ok = scribble.trace_pattern(ctx.mouse, rect, frame,
                                            lateral=lateral,
                                            sample_step=sample_step,
                                            speed=1.0, label="拓印绘制区",
                                            polarity=polarity, params=params)
                if not ok:
                    ctx.log("⚠ 没能识别出图案笔画，未描摹——请确认临摹界面已弹出、绘制区框得对。", level="warn")
                    if save_debug:
                        files = scribble.dump_failure(_debug_dir(), _debug_tag("tuoying_nopat"),
                                                      frame, params=params, polarity=polarity,
                                                      lateral=lateral, sample_step=sample_step)
                        if files:
                            ctx.log(f"已存识别失败调试图（frame/mask/skel/strokes/trace）：{files[0]}", level="info")
                    return
                if _pass == 0:
                    self._interruptible_sleep(ctx, self._jitter(0.15, ctx))
            if ctx.should_stop():
                return
            if not self._click_upload(ctx, up_tpl, win_rect, loop, timeout=upload_timeout):
                ctx.log("⚠ 没点到「上传」（上传按钮模板没标定/没找到），请手动点「上传」。", level="warn")
                self._wait_gone(ctx, title_tpl, win_rect, loop)
                return
            # 上传后判定：见 dungeon_base._tuoying_await_next 的判据。
            res = self._await_next(ctx, title_tpl, todo_tpl, win_rect, loop,
                                   confirm_sec=confirm_sec, upload_timeout=upload_timeout)
            if res == "closed":
                ctx.log("拓印临摹通过，界面已关闭。", level="hit")
                return
            if res == "unknown":
                # 没标「界面标题」也没标「未完成状态按钮」→ 无法自动确认；等上传生效的固定窗口后完成。
                ctx.log("已点「上传」；未标定「界面标题/未完成状态按钮」，无法自动确认界面关闭，等待上传生效后完成。",
                        level="info")
                self._interruptible_sleep(ctx, self._jitter(upload_timeout, ctx))
                return
            if res == "ready":
                ctx.log(f"上传后检测到「未完成状态按钮」——新图案已生成，进入第 {rnd + 1} 遍…", level="hit")
                self._interruptible_sleep(ctx, self._jitter(loop.get("tuoying_ready_settle_sec", 0.5), ctx))
            else:
                ctx.log(f"上传后未判断出新图案或界面关闭（可能没识别到），进入第 {rnd + 1} 遍…", level="warn")
        ctx.log("⚠ 自动描完多遍后拓印界面仍会重现（校验可能没通过）——请手动临摹并点「上传」。", level="warn")
        self._wait_gone(ctx, title_tpl, win_rect, loop)

    def _await_next(self, ctx, title_tpl, todo_tpl, win_rect, loop, confirm_sec, upload_timeout):
        """上传后判定：新图案已生成（「未完成状态按钮」重现）→ "ready"（接着描下一遍）；
        服务器判定通过、界面关闭 → "closed"（完成）；超时都判断不出 → "timeout"；没有任何可判据 → "unknown"。

        没标「未完成状态按钮」时退回旧判据（等标题消失，见 _confirmed_gone）——但拓印弹窗上传后通常不关、
        标题一直在，故旧判据基本只会超时。判据同 dungeon_base._tuoying_await_next。"""
        if todo_tpl is None:
            gone = self._confirmed_gone(ctx, title_tpl, win_rect, loop,
                                        confirm_sec=confirm_sec, upload_timeout=upload_timeout)
            if gone is True:
                return "closed"
            if gone is None:
                return "unknown"
            return "timeout"
        threshold = loop.get("match_threshold", 0.85)
        ready_sec = loop.get("tuoying_ready_sec", 10.0)
        deadline = time.time() + ready_sec
        no_gone_since = None
        while not ctx.should_stop():
            cur = ctx.window.grab_screen(win_rect)
            if cur is not None:
                if vision.match(cur, todo_tpl, threshold) is not None:
                    return "ready"
                if title_tpl is not None:
                    if vision.match(cur, title_tpl, threshold) is None:
                        if no_gone_since is None:
                            no_gone_since = time.time()
                        elif time.time() - no_gone_since >= confirm_sec:
                            return "closed"
                    else:
                        no_gone_since = None
            if time.time() > deadline:
                return "timeout"
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        return "timeout"

    # ---- 小工具：找并点上传 / 盯界面消失（判据与 dungeon_base 一致，只把 scene 换成整窗）----
    def _click_upload(self, ctx, up_tpl, win_rect, loop, timeout=None):
        """在【整窗】里找「上传」按钮并点（按钮不在绘制区内，不能只搜绘制区）。"""
        if up_tpl is None:
            return False
        if timeout is None:
            timeout = loop.get("tuoying_upload_sec", 6.0)
        threshold = loop.get("match_threshold", 0.85)
        deadline = time.time() + timeout
        while not ctx.should_stop():
            scene = ctx.window.grab_screen(win_rect)
            if scene is not None:
                hit = vision.match(scene, up_tpl, threshold)
                if hit is not None:
                    ctx.mouse.click(win_rect[0] + hit[0], win_rect[1] + hit[1])
                    ctx.log(f"点「上传」（{hit[2]:.3f}）。", level="hit")
                    return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        return False

    def _confirmed_gone(self, ctx, title_tpl, win_rect, loop, confirm_sec, upload_timeout):
        """上传后判定拓印界面「真消失」：先等界面不再出现（upload_timeout 内），再持续观察
        confirm_sec 内仍不重现才算真过；任何一步在超时内又回到界面都返回 False。
        没标「界面标题」模板 → 返回 None（无法确认，调用方走固定等待兜底）。"""
        if title_tpl is None:
            return None
        threshold = loop.get("match_threshold", 0.85)
        last_seen = time.time()
        no_gone_since = None
        deadline = time.time() + upload_timeout + confirm_sec
        while not ctx.should_stop():
            if time.time() > deadline:
                return False
            cur = ctx.window.grab_screen(win_rect)
            seen = cur is not None and vision.match(cur, title_tpl, threshold) is not None
            if not seen:
                if no_gone_since is None:
                    no_gone_since = time.time()
                elif time.time() - no_gone_since >= confirm_sec:
                    return True          # 消失且确认窗口内不再出现
            else:
                no_gone_since = None
                last_seen = time.time()
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        return False

    def _wait_gone(self, ctx, title_tpl, win_rect, loop, timeout=None):
        """等拓印界面消失。timeout=None=不限时（手动兜底：停在那等你手动处理）。
        没标标题模板 → 无法判定，直接返回 True。"""
        if title_tpl is None:
            return True
        threshold = loop.get("match_threshold", 0.85)
        deadline = None if timeout is None else time.time() + timeout
        while not ctx.should_stop():
            cur = ctx.window.grab_screen(win_rect)
            if cur is not None and vision.match(cur, title_tpl, threshold) is None:
                return True
            if deadline is not None and time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.35, ctx))
        return False