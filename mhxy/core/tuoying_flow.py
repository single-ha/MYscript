# -*- coding: utf-8 -*-
"""
拓印临摹弹窗的自动处理（共享编排）：探测「拓印」界面 → 沿图案描 → 点「上传/完成」→
判定界面关闭还是再弹一遍（必要时描两遍）→ 直到界面消失。

背景：刷副本点「进入」后队长窗口【偶发】弹出「拓印」临摹界面（无跳过/放弃按钮，必须描完），
宝图任务点「听听无妨」后也可能弹同一界面。两处都要同一套处理，故抽成本共享 mixin，
避免在 tasks/dungeon_base.py 与 tasks/treasure_map.py 各写一份。

宿主（继承本 mixin 的 Task）需提供：
  · self._scene_rect(ctx, regions)          —— 主识别区屏幕矩形
  · self._interruptible_sleep(ctx, sec)     —— 可中断 sleep（带弹窗守卫）
  · self._jitter(sec, ctx)                  —— 拟人化时长抖动
  · 可选 self.title / self.name             —— 仅用于日志/诊断留证

拓印资产（tuoying_title / tuoying_upload / tuoying_todo 模板 + tuoying_area 绘制区）统一读
tasks.tuoying（「工具」页「拓印」里标定，全任务共用）；描摹节奏参数读共享 tasks.dungeon.loop，
图案提取参数读 tasks.tuoying.loop —— 与刷副本/工具页单独跑同源。一律只读这份新值，不叠加历史共享值。

完成判据（★2026-10-18）：按钮「未完成↔完成」两态外观只差**局部亮度**，TM_CCOEFF 分不开（两态都 ~0.98 命中），
故用 SQDIFF 比像素（vision.sqdiff_score）判当前态。
"""

import time

from . import scribble
from . import vision


class TuoyingFlow:
    """「拓印/临摹」自动处理的可复用 mixin（见模块 docstring）。"""

    # ---- 资产/参数读取（只读 tasks.tuoying / tasks.dungeon，不依赖宿主自身 flags）----
    def _tuo_assets(self, ctx):
        """加载并缓存拓印资产：{title, upload, todo, area, loop}（tasks.tuoying，工具页标定）。"""
        cache = getattr(self, "_tuo_cache", None)
        if cache is not None:
            return cache
        tasks = (ctx.cfg or {}).get("tasks", {}) or {}
        tuo = tasks.get("tuoying", {}) or {}
        tpl = tuo.get("templates", {}) or {}
        reg = tuo.get("regions", {}) or {}
        cache = {
            "title": vision.load_template(tpl.get("tuoying_title")) if tpl.get("tuoying_title") else None,
            "upload": vision.load_template(tpl.get("tuoying_upload")) if tpl.get("tuoying_upload") else None,
            "todo": vision.load_template(tpl.get("tuoying_todo")) if tpl.get("tuoying_todo") else None,
            "area": reg.get("tuoying_area") or None,
            "loop": tuo.get("loop", {}) or {},
        }
        self._tuo_cache = cache
        return cache

    def _tuo_loop(self, ctx):
        """拓印描摹节奏参数（共享 tasks.dungeon.loop，与刷副本同源）。"""
        try:
            return (ctx.task_cfg("dungeon").get("loop", {}) or {})
        except Exception:
            return {}

    def _tuo_match(self, cur, scene_rect, tpl, threshold):
        """在 cur 里匹配某个拓印模板，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold, template_name="tuoying",
                         task_name=getattr(self, "name", ""), window_rect=scene_rect)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    # ---- 探测 + 自动描 + 手动兜底。返回 False=被急停打断 ----
    def _handle_tuoying(self, ctx, regions, threshold):
        # 整个拓印阶段挂起弹窗守卫（_trace_active，见 base._defuse_popup）：拓印窗看似弹窗但没有
        # 可点掉的「×」，误让守卫点它的关闭会让「上传后再弹」流程断掉——拓印收尾靠识别标题模板。
        self._trace_active = True
        try:
            return self._handle_tuoying_impl(ctx, regions, threshold)
        finally:
            self._trace_active = False

    def _handle_tuoying_impl(self, ctx, regions, threshold):
        title = self._tuo_assets(ctx)["title"]
        if title is None:
            return True                      # 没标标题模板 → 不知道弹没弹，当没弹继续（真弹了由验证超时兜底）
        loop = self._tuo_loop(ctx)
        scene_rect = self._scene_rect(ctx, regions)
        deadline = time.time() + loop.get("tuoying_detect_sec", 8.0)
        while not ctx.should_stop():
            cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
            hit = self._tuo_match(cur, scene_rect, title, threshold)
            if hit is not None:
                ctx.log(f"识别到「拓印」临摹界面（{hit[2]:.3f}），自动临摹…", level="hit")
                return self._auto_trace(ctx, regions, threshold)
            if time.time() > deadline:
                return True
            self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
        return False

    # ---- 自动临摹：沿图案描 + 点上传 + 等界面消失；一次上传后可能再弹（要拓印两遍）→ 循环到不再重现 ----
    def _tuoying_max_rounds(self, ctx):
        """临摹「多少遍」的设置归拓印工具自己（tasks.tuoying.loop.tuoying_max_rounds，工具页可改）——
        进本弹窗不再自己设。读不到时用旧 tasks.dungeon.loop 值兜底（迁移后一般不存在）。"""
        v = (self._tuo_assets(ctx)["loop"].get("tuoying_max_rounds")) or self._tuo_loop(ctx).get("tuoying_max_rounds", 2)
        return max(1, int(v))

    def _auto_trace(self, ctx, regions, threshold):
        assets = self._tuo_assets(ctx)
        loop = self._tuo_loop(ctx)
        area = assets["area"]
        rect = ctx.window.region_to_screen_rect(area) if area else None
        if rect is None or assets["upload"] is None:
            ctx.log("⚠ 自动临摹需先标定「拓印临摹绘制区」+「上传按钮」——请手动临摹并点「上传」；脚本会等拓印界面消失后自动继续。",
                    level="warn")
            ctx.log("想全自动就在「工具→拓印」页里补标这两项。", level="warn")
            return self._wait_tuoying_gone(ctx, regions, threshold)
        max_rounds = self._tuoying_max_rounds(ctx)
        confirm_sec = loop.get("tuoying_gone_confirm_sec", 1.2)
        # 图案提取参数在拓印工具自己的命名空间（tasks.tuoying.loop.tuoying_*），与工具页单独跑同源。
        tuo_loop = assets["loop"]
        params = scribble.params_from_loop(tuo_loop)
        polarity = scribble.polarity_from_loop(tuo_loop)
        save_debug = scribble.debug_enabled(tuo_loop)
        lateral = loop.get("tuoying_lateral", 3.0)
        sample_step = loop.get("tuoying_sample_step", 5.0)
        trace_speed = loop.get("tuoying_speed", 1.0)
        if assets["todo"] is None:
            ctx.log("⚠ 未标定「未完成状态按钮」→ 描摹中途无法判定图案是否达标，只能描完再点「上传」。"
                    "想「描够一笔就停」请去「工具→拓印」补标该项。", level="warn")
        for rnd in range(1, max_rounds + 1):
            if ctx.should_stop():
                return False
            ctx.log(f"自动临摹（第 {rnd}/{max_rounds} 遍）：识别图案笔画并沿骨架描 {loop.get('tuoying_passes', 2)} 轮…",
                    level="warn")
            clicked = False
            for _pass in range(max(1, int(loop.get("tuoying_passes", 2)))):
                if ctx.should_stop():
                    return False
                # 先截绘制区当前画面：识别图案笔画像素，只沿图案描（描到图案外会拉低完成度）
                frame = ctx.window.grab_screen(rect) if rect else None
                if frame is None:
                    ctx.log("⚠ 绘制区截图失败，无法自动临摹——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                            level="warn")
                    return self._wait_tuoying_gone(ctx, regions, threshold)
                done = {"v": False}

                def _check_upload(_i, _n, _done=done):
                    # 每描完一笔判图案是否达标（按钮由「未完成」态切成「完成」态）→ 点它并停止描摹。
                    # 判据用 SQDIFF 比两态外观（TM_CCOEFF 分不开两态，见 _tuoying_button_ready）。
                    if self._tuoying_button_ready(ctx, regions, threshold,
                                                  cap=loop.get("tuoying_state_max_sqdiff", 0.06)) and \
                            self._click_tuoying_upload(ctx, regions, threshold, timeout=0.8):
                        ctx.log("描完一笔后图案已达标（按钮已变为「完成」态）——已点「上传/完成」，停止描摹。",
                                level="hit")
                        _done["v"] = True
                        return True
                    return False

                ok = scribble.trace_pattern(ctx.mouse, rect, frame,
                                        lateral=lateral,
                                        sample_step=sample_step,
                                        speed=trace_speed, polarity=polarity, params=params,
                                        on_stroke=_check_upload, should_stop=ctx.should_stop)
                if ok is None or ctx.should_stop():
                    return False               # 急停：描摹被中断
                if done["v"]:
                    clicked = True
                    break
                if not ok:
                    ctx.log("⚠ 没能从画面识别出图案笔画——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                            level="warn")
                    if save_debug:
                        files = scribble.dump_failure(scribble.debug_dir(),
                                                      scribble.debug_tag("tuoying_nopat"),
                                                      frame, params=params, polarity=polarity,
                                                      lateral=lateral, sample_step=sample_step)
                        if files:
                            ctx.log(f"已存识别失败调试图（frame/mask/skel/strokes/trace）：{files[0]}", level="info")
                    return self._wait_tuoying_gone(ctx, regions, threshold)
                if _pass == 0:
                    self._interruptible_sleep(ctx, self._jitter(0.15, ctx))
            if ctx.should_stop():
                return False
            if not clicked and not self._click_tuoying_upload(ctx, regions, threshold):
                ctx.log("⚠ 描完没点到「上传」，请手动临摹并点「上传」；脚本会等界面消失后自动继续。", level="warn")
                return self._wait_tuoying_gone(ctx, regions, threshold)
            # 上传后判定：新图案已生成（按钮回到「未完成」态）→ 接着描下一遍；
            # 服务器判定通过、界面关闭 → 完成。带「未完成状态按钮」时用它驱动（比等标题消失快也更准）。
            res = self._tuoying_await_next(ctx, regions, threshold,
                                           confirm_sec=confirm_sec,
                                           upload_timeout=loop.get("tuoying_upload_sec", 6.0))
            if res == "closed":
                ctx.log("拓印临摹通过，界面已关闭。", level="hit")
                return True
            if res == "ready":
                ctx.log(f"上传后检测到「未完成状态按钮」——新图案已生成，进入第 {rnd + 1} 遍…", level="hit")
                self._interruptible_sleep(ctx, self._jitter(loop.get("tuoying_ready_settle_sec", 0.5), ctx))
            else:
                ctx.log(f"上传后未判断出新图案或界面关闭（可能没识别到），进入第 {rnd + 1} 遍…", level="warn")
        ctx.log("⚠ 自动描完多遍后拓印界面仍会重现（校验可能没通过）——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                level="warn")
        return self._wait_tuoying_gone(ctx, regions, threshold)

    def _tuoying_await_next(self, ctx, regions, threshold, confirm_sec, upload_timeout):
        """上传后判定：新图案已生成（按钮回到「未完成」态）→ "ready"（接着描下一遍）；
        服务器判定通过、界面关闭 → "closed"（完成）；超时都判断不出 → "timeout"。

        没标「未完成状态按钮」时退回旧判据（等标题消失，见 _tuoying_confirmed_gone）——但拓印弹窗上传后
        通常**不关**、标题一直在，故旧判据基本只会超时（=再描一遍）。

        ⚠「新图案就绪」不能只查「未完成模板命中」——TM_CCOEFF 下**两态模板都会命中**（实测都 ~0.98），上传后
        旧图案的「完成」态会被误判成 ready。带 upload 模板时用 SQDIFF 比两态：仅当更接近「未完成」态才算就绪。"""
        assets = self._tuo_assets(ctx)
        loop = self._tuo_loop(ctx)
        todo_tpl = assets["todo"]
        if todo_tpl is None:
            return "closed" if self._tuoying_confirmed_gone(ctx, regions, threshold,
                                                             confirm_sec=confirm_sec,
                                                             upload_timeout=upload_timeout) else "timeout"
        up_tpl = assets["upload"]
        title_tpl = assets["title"]
        cap = loop.get("tuoying_state_max_sqdiff", 0.06)
        ready_sec = loop.get("tuoying_ready_sec", 10.0)
        scene_rect = self._scene_rect(ctx, regions)
        deadline = time.time() + ready_sec
        no_gone_since = None
        while not ctx.should_stop():
            cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
            if cur is not None and self._tuoying_is_todo_state(cur, todo_tpl, up_tpl, cap):
                return "ready"
            if title_tpl is not None:
                if self._tuo_match(cur, scene_rect, title_tpl, threshold) is None:
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

    def _tuoying_confirmed_gone(self, ctx, regions, threshold, confirm_sec, upload_timeout):
        """上传后判定拓印界面「真消失」：先等界面不再出现（upload_timeout 内），
        再持续观察 confirm_sec 内仍不重现才算真过；任何一步在超时内又回到界面都返回 False。"""
        title_tpl = self._tuo_assets(ctx)["title"]
        no_gone_since = None
        deadline = time.time() + upload_timeout + confirm_sec
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            if time.time() > deadline:
                return False
            cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
            seen = self._tuo_match(cur, scene_rect, title_tpl, threshold) is not None
            if not seen:
                if no_gone_since is None:
                    no_gone_since = time.time()
                elif time.time() - no_gone_since >= confirm_sec:
                    return True          # 消失且确认窗口内不再出现
            else:
                no_gone_since = None
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))

    @staticmethod
    def _tuoying_is_todo_state(cur, todo_tpl, up_tpl, cap):
        """画面里「上传/完成」按钮当前是否为**未完成**态。对 todo / upload 两模板取 SQDIFF 最小分：
        更接近「未完成」模板、且分数不太高才算未完成态；否则 False（完成态/按钮不在）。没标 upload → 退回
        只看 todo 是否命中（TM_CCOEFF 的旧行为，供兼容）。"""
        if cur is None or todo_tpl is None:
            return False
        s_todo = vision.sqdiff_score(cur, todo_tpl)
        if up_tpl is None:
            return s_todo <= cap
        s_up = vision.sqdiff_score(cur, up_tpl)
        return min(s_todo, s_up) <= cap and s_todo < s_up

    def _tuoying_button_ready(self, ctx, regions, threshold, cap=0.06):
        """判图案是否已达标（按钮由「未完成」态切成「完成」态）→ True/False；没法判 → None。

        ⚠ 两态外观**只有局部亮度差**（未完成处偏灰、完成处偏白），整体亮度近乎一样——TM_CCOEFF 减均值、
        对均匀亮度不敏感，**分不开两态**（实测两个模板在两种状态下都能 ~0.98 命中，★2026-10-18）。故用
        SQDIFF 比像素（vision.sqdiff_score）：对「未完成」todo 与「完成」upload 各取最小分，「完成」更低=已达标；
        两分都偏高（按钮根本不在，如别的界面）→ 判未达标，绝不误判。没标 todo/upload → None（退化描完再点上传）。"""
        assets = self._tuo_assets(ctx)
        todo = assets["todo"]
        up = assets["upload"]
        if todo is None or up is None:
            return None
        scene_rect = self._scene_rect(ctx, regions)
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        if cur is None:
            return None
        s_todo = vision.sqdiff_score(cur, todo)
        s_up = vision.sqdiff_score(cur, up)
        if min(s_todo, s_up) > cap:
            return False
        return s_up < s_todo

    def _click_tuoying_upload(self, ctx, regions, threshold, timeout=None):
        tpl = self._tuo_assets(ctx)["upload"]
        if tpl is None:
            return False
        if timeout is None:
            timeout = self._tuo_loop(ctx).get("tuoying_upload_sec", 6.0)
        deadline = time.time() + timeout
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
            hit = self._tuo_match(cur, scene_rect, tpl, threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log("点「上传」。", level="hit")
                return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        return False

    # ---- 等拓印界面消失。timeout=None=不限时（手动兜底）。返回 True=已消失 ----
    def _wait_tuoying_gone(self, ctx, regions, threshold, timeout=None):
        tpl = self._tuo_assets(ctx)["title"]
        if tpl is None:
            return True
        deadline = None if timeout is None else time.time() + timeout
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
            if self._tuo_match(cur, scene_rect, tpl, threshold) is None:
                return True
            if deadline is not None and time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.35, ctx))
        return False
