# -*- coding: utf-8 -*-
"""
三界奇缘任务（答题型：开活动→参加→识别到完成字样即停）。

用户口述流程（每个号一轮）：

  开「活动」(快捷键 Alt+C) → 在活动列表里找到那张活动卡片 → 点该行【右侧的「参加」按钮】
  → 【直接进入答题界面】（没有「选活动选项/开始答题」这类中间步骤）
  → 答题循环：每道题【任意点一个选项】即可推进（答错无所谓，不影响拿奖励），
     → 点完选项【自动进下一题】（没有「下一题/确定」按钮）
  → 直到 识别到「完成」标志（今日已答完/次数用完等字样，qq_done） 或
     长时间找不到选项按钮（界面已关闭），判定「答题已结束」→ 收尾结束本轮。

★ 多开执行（用户拍板 2026-09-22：一个号完成之后再继续下一个号）：多个号【逐个顺序跑】——
  先让一个号从头到尾做完（识别到完成字样/done）再切下一个号，不做跨号并行轮转；
  操作某号前先 activate() 切前台。单开=列表只有一个号、同一套执行。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户可按游戏「系统设置-快捷键」核对）。

停止：①所有号都识别到「完成」字样 ②时间上限分钟(安全网) ③手动停止/鼠标甩右上角 failsafe。
任务始终实跑（无演练模式）：开活动→参加→答题循环，先预飞自检模板齐全再开跑。
"""

import time

from ..core import vision
from ..core import window as win_mod
from ..core import scan
from .base import Task, register

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 活动列表里滚轮找卡片 → 点「参加」（点完直接进答题）
S_ANSWER = "ANSWER"                 #   答题循环：任意点选项，自动进下一题，识别到完成字样即停 → 收尾
S_DONE = "DONE"                     # 收尾：点「关闭」(可选) → 本轮结束

# 模板键（用 qq_ 前缀=奇缘，避免与运镖/宝图/秘境同名模板在磁盘互相覆盖——存盘按 templates/tm_<key>.png）。
_FLAG_KEYS = ["qq_entry", "qq_join", "qq_option", "qq_done", "qq_close"]
# 必备：缺失则 preflight 阻断（其余为可选，缺失仅提示/降级）。答题选项另做「模板或固定点位」检查。
_REQUIRED_FLAGS = ["qq_entry", "qq_join"]


@register
class SanjieTask(Task):
    name = "sanjie"
    title = "三界奇缘"
    description = "自动开活动→参加→进答题→任意点选项（自动进下一题）→识别到完成字样即停（多开逐号顺序跑：一个号完成再下一个号）"
    CHAINS_PER_WINDOW = True   # 可做「日常一条龙·每窗口独立链」
    CHAIN_SEQUENTIAL = True    # 一条龙多开时也逐号顺序跑：一个号答完全套再轮下一个号（用户拍板）

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；答题选项/完成字样等都在这里找", True),
            # activity_list 已在「通用」页「标定（公共区域）」统一标定（全任务共用），见 tasks.shared
        ],
        "templates": [
            ("qq_entry", "活动卡片入口", "活动列表里要点「参加」的那张卡片，框图标+文字、要独特"),
            ("qq_join", "参加按钮", "那张卡片右侧的「参加」按钮，框按钮本身、要独特"),
            ("qq_option", "答题选项按钮(任选其一)", "答题界面里任意一个选项按钮，脚本就点它作答本题。"
                                              "不标则必须在运行参数 loop.answer_pos 里配一个固定点位", True),
            ("qq_done", "答题完成标志", "答完后出现的「今日已答完/次数已用完/结算」这类字样，识别到即停；不标时靠空闲兜底", True),
            ("qq_close", "「关闭」按钮", "答题结束后的关闭/退出按钮；没有可不标", True),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        templates = tc.get("templates", {})

        # activity_list 属公共区域（全任务共用），在「通用」页标定；task_config 已把 tasks.shared 叠加进来，直接读即可
        for rk, label in [("activity_list", "活动列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请到「通用」页点「标定（公共区域）」框选（所有任务共用）")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        # 答题选项：模板或固定点位至少一个
        opt = templates.get("qq_option")
        if not (opt and vision.load_template(opt) is not None) and not tc.get("loop", {}).get("answer_pos"):
            problems.append("答题无着落：请标定『qq_option』选项按钮模板，或在配置 loop.answer_pos 填固定点位")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")

        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")

        for tk, label in [("qq_done", "完成标志"), ("qq_close", "关闭")]:
            path = templates.get(tk)
            if not path or (path and vision.load_template(path) is None):
                ctx.log(f"提示：可选模板『{tk}』({label})未标定，将降级处理（可靠性略降）。", level="warn")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = False
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.answer_idle = float(loop.get("answer_idle_sec", 15))

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = loop.get("tick_interval_sec", 0.5)

        time_limit = loop.get("time_limit_min", 0) or 0
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截。"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        # 用户要求：运行任务前先激活要操作的窗口（快捷键/点击只发给前台窗口，目标号不在前台会发空/点歪）
        for wctx in contexts:
            wctx.window.activate()

        if dry_run:
            ctx.log("演练模式：只对各号当前屏幕做『各标志识别自检』，不发快捷键/不点关键操作。"
                    + ("多号逐个扫描。" if multi else ""), level="warn")
            self._dry_run_selfcheck(ctx, contexts, multi, regions, threshold, switch_delay, deadline)
            return

        ctx.log(f"★ 实战模式：{('多开逐号 ' + str(len(contexts)) + ' 个号' if multi else '单号')}，"
                f"识别到完成字样(今日已答完/次数用完)即停，一个号完成后再继续下一个号 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。")

        records = [self._new_record(c) for c in contexts]
        self._run_rotation_sequential(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold),
            multi, switch_delay, tick, time_limit)

        if all(r["done"] for r in records):
            ctx.log("所有号都已识别到完成字样/结束。")
        total = sum(r["answered"] for r in records)
        ctx.log(f"已停止。共答 {total} 题，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步
        （沿用 run() 同款 _step_once），record["done"]=本任务在该窗口完成。
        与 run() 共用 _new_record/_step_once，不自跑轮转、不切前台（由一条龙总轮转统一切）。
        本任务 CHAIN_SEQUENTIAL=True：一条龙多开时主循环只让一个号持有它、一口气做到 done
        才放行下一个号（不跨号轮转）；step_fn 内部无等待让出依赖，可被阻塞式连推。"""
        tc = wctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.answer_idle = float(loop.get("answer_idle_sec", 15))
        rec = self._new_record(wctx)
        return rec, (lambda: self._step_once(wctx, rec, loop, regions, threshold))

    # ------------------------------------------------------------------
    # 多开轮转：上下文与每号状态记录
    # ------------------------------------------------------------------
    def _resolve_contexts(self, ctx, multi):
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            return [ctx.make_child(w, f"号{self._window_no(w, i)}") for i, w in enumerate(wins)]
        ctx.window = wins[0]
        return [ctx]

    @staticmethod
    def _new_record(wctx):
        """每个号一份独立状态。轮转时按 state 各推进一步，互不干扰。"""
        now = time.time()
        return {"ctx": wctx, "state": S_OPEN_ACTIVITY, "t_state": now, "t_action": now,
                "answered": 0, "recover": 0, "runs": 0, "done": False}

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    @staticmethod
    def _state_elapsed(rec):
        return time.time() - rec["t_state"]

    # ------------------------------------------------------------------
    # 单步推进：按这个号的 state 做【一小步】非阻塞动作，然后立刻返回（好轮转到下一个号）
    # ------------------------------------------------------------------
    def _step_once(self, ctx, rec, loop, regions, threshold):
        st = rec["state"]
        if st == S_OPEN_ACTIVITY:
            self._do_open_activity(ctx, rec, loop, regions, threshold)
        elif st == S_FIND_CARD:
            self._do_find_card(ctx, rec, loop, regions, threshold)
        elif st == S_ANSWER:
            self._do_answer(ctx, rec, loop, regions, threshold)
        elif st == S_DONE:
            self._do_done(ctx, rec, loop, regions, threshold)

    # ---- 开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已打开活动，翻找三界奇缘卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 找卡片 → 点「参加」（点完直接进答题界面）----
    #   用户要求：滚轮查找在【同一个号】上一气呵成跑完（找到/翻完才轮转下个号），不在滚动中途返回。
    #   故用内部 while 把整段查找做完再返回；每滚一屏后自等 scroll_settle_sec 让画面落定。仍勤查 should_stop。
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())

        def probe(scene, rect):
            hit = vision.match(scene, self.flags.get("qq_entry"), threshold) if scene is not None else None
            if hit is None:
                rec["_join_warned"] = False
                rec["_nudges"] = 0
                return scan.SCROLL, None
            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            r = self._find_join_ready(ctx, rec, list_region, entry_xy, threshold, loop,
                                      entry_tpl=self.flags.get("qq_entry"))
            if r is not None and r != "nudged":
                ctx.mouse.click(r[0], r[1])
                ctx.log(f"找到卡片（{score:.3f}）→ 点「参加」（{r[2]:.3f}），直接进答题界面。", level="hit")
                return scan.ACCEPT, r
            if r != "nudged":
                # 卡片完整显示仍没找到「参加」→ 告警一次+原地重试（走 scroll_search 的 STAY）。
                if not rec["_join_warned"]:
                    rec["_join_warned"] = True
                    ctx.log("认出卡片但「参加」按钮一直没出现（已自动微滚补全；检查 qq_join 模板/阈值），原地重试。", level="warn")
            # 认出条目但没找到参加：不滚动（会滚走目标），原地重试
            return scan.STAY, None

        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, self._jitter(s, ctx)),
            scroll_step=loop.get("scroll_step", -3),
            max_tries=max(1, loop.get("scroll_max_tries", 8)),
            settle_sec=loop.get("scroll_settle_sec", 0.35),
            reset_to_top=loop.get("scroll_reset_top", True),
            end_diff=loop.get("scroll_end_diff", 2.0),
            reset_max=loop.get("scroll_reset_max", 20),
            log=ctx.log, label="活动列表")
        if res.found:
            rec["t_action"] = time.time()
            self._goto(rec, S_ANSWER)
            return
        if res.stopped:
            return
        ctx.log("翻找三界奇缘卡片多次未果。", level="warn")
        self._recover_window(ctx, rec, loop, regions)

    # ---- 答题循环：识别到完成字样 / 长时间无选项 → 收尾 ----
    def _do_answer(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)

        # 完成标志（今日已答完/次数用完等字样）→ 收尾
        done_hit = self._match_scene(cur, scene_rect, "qq_done", threshold)
        if done_hit is not None:
            ctx.log(f"识别到答题完成标志（{done_hit[2]:.3f}）→ 答题结束。", level="hit")
            self._goto(rec, S_DONE)
            return

        # 点任意选项（模板优先，其次固定点位）作答本道题
        hit = self._match_scene(cur, scene_rect, "qq_option", threshold)
        if hit is None and loop.get("answer_pos"):
            fx, fy = loop["answer_pos"]
            hit = (scene_rect[0] + int(scene_rect[2] * fx),
                   scene_rect[1] + int(scene_rect[3] * fy), 0.0)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            rec["answered"] += 1
            ctx.log(f"点任意选项作答（累计 {rec['answered']} 题）。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            rec["t_action"] = time.time()
            return

        # 长时间找不到选项按钮：答题界面可能已关闭 → 收尾
        if time.time() - rec["t_action"] > self.answer_idle:
            ctx.log("长时间找不到选项按钮（答题界面可能已关闭），按结束处理。", level="warn")
            self._goto(rec, S_DONE)

    # ---- 收尾：点「关闭」(可选)，本轮结束，答完即停 ----
    def _do_done(self, ctx, rec, loop, regions, threshold):
        scene_rect = self._scene_rect(ctx, regions)
        cur = win_mod.grab(scene_rect)
        close = self._match_scene(cur, scene_rect, "qq_close", threshold)
        if close is not None:
            ctx.mouse.click(close[0], close[1])
            ctx.log(f"点「关闭」退出答题界面（{close[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        else:
            ctx.log("答题结束（无关闭按钮/每日次数已用完）。")
        rec["answered"] = 0
        rec["done"] = True

    # ---- 卡死兜底（仅答题前状态：关面板重新开活动）----
    def _recover_window(self, ctx, rec, loop, regions):
        rec["recover"] += 1
        if rec["recover"] >= loop.get("max_stuck_recover", 3):
            ctx.log("多次卡死仍无进展，放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log(f"卡住（第 {rec['recover']}/{loop.get('max_stuck_recover', 3)} 次），关面板重新开活动…", level="warn")
        self._save_capture(self._grab_scene(ctx, regions), f"stuck_{rec['state']}")
        self._focus(ctx)
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        self._goto(rec, S_OPEN_ACTIVITY)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _focus(self, ctx):
        """把游戏窗口切到前台——键盘快捷键(SendInput)只发给有焦点的窗口，发键前必须先激活。"""
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """统一实现见 base.Task._find_join_in_column（整列枚举取距离卡片行最近那枚「参加」）。
        三界奇缘的参加按钮模板为 qq_join；卡片为 qq_entry。"""
        return self._find_join_in_column(ctx, list_region, entry_screen_xy, threshold, loop,
                                         self.flags.get("qq_join"),
                                         self.flags.get("qq_entry"))

    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FLAG_KEYS}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        """在整张 scene 里匹配 flag_key，命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay, deadline):
        """演练：先把每个号切到活动界面（发 open_activity 快捷键），再周期性识别各标志，报告命中。
        否则一开始屏上还是主界面、找不到任务入口卡片=误判（用户拍板：演练也要先开活动列表再识别）。"""
        keys = [("qq_entry", "活动卡片"), ("qq_join", "参加"),
                ("qq_option", "选项按钮"), ("qq_done", "完成标志"), ("qq_close", "关闭")]
        # 先给每个号打开活动列表，再开始识别「任务入口」（否则屏上还是主界面，找不到卡片=误判）
        for wctx in contexts:
            if ctx.should_stop():
                return
            if wctx.window.rect() is None:
                continue
            wctx.window.activate()
            if ctx.send_hotkey("open_activity"):
                wctx.log("已打开活动列表（演练：开活动→识别入口）。")
                self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
            else:
                wctx.log("打不开活动列表（open_activity 快捷键未配置），只能识别当前屏。", level="error")
        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log("演练时间上限到，停止。")
                break
            for wctx in contexts:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                if multi:
                    wctx.window.activate()
                scene = self._grab_scene(wctx, regions)
                found = []
                for key, label in keys:
                    tpl = self.flags.get(key)
                    if tpl is None:
                        continue
                    hit = vision.match(scene, tpl, threshold) if scene is not None else None
                    if hit is not None:
                        found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    wctx.log("当前屏幕未识别到任何已标定标志（请打开对应界面再看）。")
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))