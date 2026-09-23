# -*- coding: utf-8 -*-
"""
趣味鉴赏任务（点爱心玩法：开活动→参加→匹配并点击心形图案，点满 N 次或超时即停）。

用户口述流程（每个号一轮）：

  开「活动」(快捷键 Alt+C) → 在活动列表里找到「趣味鉴赏」那张卡片 → 点该行【右侧的「参加」按钮】
  → 进入鉴赏界面：画面里有待取/待鉴赏的「图文列表区域」，目标是认出【心形图案】并点击它；
     当前屏没匹配到心形图案 → 就在【图文列表区域】里滚动（滚轮）再匹配，循环找；
  → 结束条件：点击心形图案累计达到设定的次数（默认 5 次） 或 该号在鉴赏环节超时（默认 120s）
     → 收尾：逐层 Esc 关面板【返回主界面】（用户拍板 2026-09-22：点完 5 个心形后返回主界面）。

★ 多开执行（用户拍板 2026-09-22：一个号完成之后再继续下一个号）：多个号【逐个顺序跑】——
  先让一个号从头到尾做完（点满次数/done）再切下一个号，不做跨号并行轮转；
  操作某号前先 activate() 切前台。单开=列表只有一个号、同一套执行。

导航靠 ctx.send_hotkey(动作名)（键位在 config.hotkeys，用户可按游戏「系统设置-快捷键」核对）。
滑动用鼠标滚轮（滚动只发生在标定的「图文列表区域」内，不滚出该区）。

停止：①点击次数达到目标 ②鉴赏环节超时 ③时间上限分钟(安全网) ④手动停止/鼠标甩右上角 failsafe。
任务始终实跑（无演练模式）：开活动→参加→点心形图案，先预飞自检模板齐全再开跑。
"""

import time

from ..core import vision
from ..core import window as win_mod
from ..core import scan
from ..ui import ui_state
from .base import Task, register

# 每个号的状态机状态（非阻塞：每访问一次只推进一步）
S_OPEN_ACTIVITY = "OPEN_ACTIVITY"   # 发活动快捷键
S_FIND_CARD = "FIND_CARD"           # 活动列表里滚轮找「趣味鉴赏」卡片 → 点「参加」
S_HEART = "HEART"                   # 鉴赏界面：匹配心形图案→点它；没匹配到在图文列表区域滚动再匹配
S_DONE = "DONE"                     # 收尾：返回主界面 → 本轮结束

# 模板键（用 appr_ 前缀=鉴赏，避免与运镖/宝图/三界奇缘同名模板在磁盘互相覆盖——存盘按 templates/tm_<key>.png）。
_FLAG_KEYS = ["appr_entry", "appr_join", "appr_heart"]
# 必备：缺失则 preflight 阻断（心形图案是玩法核心，必标）。
_REQUIRED_FLAGS = ["appr_entry", "appr_join", "appr_heart"]


@register
class AppreciationTask(Task):
    name = "appreciation"
    title = "趣味鉴赏"
    description = "自动开活动→参加→匹配并点击心形图案，点满设定次数或超时即停（多开逐号顺序跑：一个号完成再下一个号）"
    CHAINS_PER_WINDOW = True   # 可做「日常·每窗口独立链」
    CHAIN_SEQUENTIAL = True    # 日常多开时也逐号顺序跑：一个号点满再轮下一个号（用户拍板）

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)", True),
            ("appr_region", "图文列表区域", "进鉴赏界面后、心形图案所在的那片图文列表区域；"
             "心形在此匹配、滚动也在这一片里滚。框住列表本身", False),
            # activity_list 已在「通用」页「标定（公共区域）」统一标定（全任务共用），见 tasks.shared
        ],
        "templates": [
            ("appr_entry", "活动卡片入口", "活动列表里要点「参加」的那张「趣味鉴赏」卡片，框图标+文字、要独特"),
            ("appr_join", "参加按钮", "那张卡片右侧的「参加」按钮，框按钮本身、要独特"),
            ("appr_heart", "心形图案", "鉴赏界面里要点击的心形图案（图样/图标），框图案本身、要独特。"
                                        "当前屏没它时脚本会在图文列表区域里滚动再找"),
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

        for rk, label in [("appr_region", "图文列表区域")]:
            if not regions.get(rk):
                problems.append(f"『{label}』未标定 —— 请在标定向导里框选心形图案所在的图文列表区域")

        for tk in _REQUIRED_FLAGS:
            path = templates.get(tk)
            if not path or vision.load_template(path) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("打开『活动』缺快捷键：请在 config.hotkeys.open_activity 填上（如 alt+c）")

        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")

        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = False
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.target_clicks = max(1, int(loop.get("target_clicks", 5)))

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
                f"每号点满 {self.target_clicks} 次心形图案或鉴赏超时即停，一个号完成后再继续下一个号 ★", level="warn")
        if time_limit > 0:
            ctx.log(f"时间上限 {time_limit} 分钟（到点自停）。")

        records = [self._new_record(c) for c in contexts]
        self._run_rotation_sequential(
            ctx, records,
            lambda rec: self._step_once(rec["ctx"], rec, loop, regions, threshold),
            multi, switch_delay, tick, time_limit)

        if all(r["done"] for r in records):
            ctx.log("所有号都已结束。")
        total = sum(r["clicks"] for r in records)
        ctx.log(f"已停止。共点击 {total} 个心形图案，用时 {(time.time() - start_ts) / 60:.1f} 分钟。")

    # ------------------------------------------------------------------
    # 日常·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口本任务状态机一步
        （沿用 run() 同款 _step_once），record["done"]=本任务在该窗口完成。
        与 run() 共用 _new_record/_step_once，不自跑轮转、不切前台（由日常总轮转统一切）。
        本任务 CHAIN_SEQUENTIAL=True：日常多开时主循环只让一个号持有它、一口气做到 done
        才放行下一个号（不跨号轮转）；step_fn 内部无等待让出依赖，可被阻塞式连推。"""
        tc = wctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        self.flags = self._load_flags(tc)
        self.target_clicks = max(1, int(loop.get("target_clicks", 5)))
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
                "clicks": 0, "scrolls": 0, "not_found_since": None, "heart_deadline": 0.0,
                "recover": 0, "_card_wait": False, "done": False}

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
        elif st == S_HEART:
            self._do_heart(ctx, rec, loop, regions, threshold)
        elif st == S_DONE:
            self._do_done(ctx, rec, loop, regions, threshold)

    # ---- 开活动 ----
    def _do_open_activity(self, ctx, rec, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），放弃该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已打开活动，翻找「趣味鉴赏」卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        rec["scrolls"] = 0
        self._goto(rec, S_FIND_CARD)

    # ---- 找「趣味鉴赏」卡片 → 点「参加」（点完进入鉴赏界面）----
    #   用户要求：滚轮查找在【同一个号】上一气呵成跑完（找到/翻完才轮转下个号），不在滚动中途返回。
    #   故用内部 while 把整段查找做完再返回；每滚一屏后自等 scroll_settle_sec 让画面落定。仍勤查 should_stop。
    def _do_find_card(self, ctx, rec, loop, regions, threshold):
        list_region = regions.get("activity_list")

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())

        def probe(scene, rect):
            hit = vision.match(scene, self.flags.get("appr_entry"), threshold) if scene is not None else None
            if hit is None:
                rec["_join_warned"] = False
                rec["_nudges"] = 0
                return scan.SCROLL, None
            cx, cy, score = hit
            entry_xy = (rect[0] + cx, rect[1] + cy)
            r = self._find_join_ready(ctx, rec, list_region, entry_xy, threshold, loop,
                                      entry_tpl=self.flags.get("appr_entry"))
            if r is not None and r != "nudged":
                ctx.mouse.click(r[0], r[1])
                ctx.log(f"找到「趣味鉴赏」卡片（{score:.3f}）→ 点「参加」（{r[2]:.3f}），进鉴赏界面。", level="hit")
                rec["not_found_since"] = None
                rec["scrolls"] = 0
                rec["heart_deadline"] = time.time() + max(1.0, float(loop.get("heart_timeout_sec", 120)))
                self._goto(rec, S_HEART)
                return scan.ACCEPT, r
            if r != "nudged":
                # 卡片完整显示仍没找到「参加」→ 告警一次+原地重试（走 scroll_search 的 STAY）。
                # 卡片露半张被裁剪是主因，_find_join_ready 已先朝补齐方向微滚过 nudge_max 次。
                if not rec["_join_warned"]:
                    rec["_join_warned"] = True
                    ctx.log("认出「趣味鉴赏」卡片但「参加」按钮一直没出现（已自动微滚补全；检查 appr_join 模板/阈值），原地重试。", level="warn")
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
            return
        if res.stopped:
            return
        ctx.log("翻找「趣味鉴赏」卡片多次未果。", level="warn")
        self._recover_window(ctx, rec, loop, regions)

    # ---- 鉴赏循环：匹配心形图案→点它；没匹配到就在图文列表区域滚动再匹配 ----
    def _do_heart(self, ctx, rec, loop, regions, threshold):
        # 结束条件①：点击次数达到目标
        if rec["clicks"] >= self.target_clicks:
            ctx.log(f"已点击 {rec['clicks']} 个心形图案，完成鉴赏。", level="hit")
            self._goto(rec, S_DONE)
            return
        # 结束条件②：鉴赏环节超时
        if time.time() >= rec["heart_deadline"]:
            ctx.log(f"心形图案匹配超时（{loop.get('heart_timeout_sec', 120):.0f}s）"
                    f"，已点 {rec['clicks']}/{self.target_clicks}，收尾该号。", level="warn")
            self._goto(rec, S_DONE)
            return

        region = regions.get("appr_region")
        if not region:
            ctx.log("图文列表区域未标定，无法滚动匹配心形图案，放弃该号。", level="error")
            rec["done"] = True
            return
        rect = ctx.window.region_to_screen_rect(region)
        if rect is None:
            return                      # 窗口没了/取不到 rect → 轮转层 already 处理；这里直接让出
        cur = win_mod.grab(rect)
        hit = self._match_region(cur, rect, "appr_heart", threshold)
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            rec["clicks"] += 1
            rec["t_action"] = time.time()
            rec["not_found_since"] = None
            rec["scrolls"] = 0
            ctx.log(f"点中心形图案（{hit[2]:.3f}），累计 {rec['clicks']}/{self.target_clicks}。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(float(loop.get("post_click_sec", 1.0)), ctx))
            return

        # 当前屏没匹配到心形图案 → 在图文列表区域滚动再匹配
        now = time.time()
        if rec["not_found_since"] is None:
            rec["not_found_since"] = now
            return                      # 给画面一点落定时间，别刚没找到就滚
        if now - rec["not_found_since"] < loop.get("scroll_wait_sec", 0.8):
            return
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        step = int(loop.get("scroll_step", -3))
        ctx.mouse.scroll(step, cx, cy)
        rec["scrolls"] += 1
        rec["not_found_since"] = now
        # 连续滚了很多屏仍然没找到：可能过了头/翻到底 → 反向滚回顶，从头再找一遍（防漏上半截）
        if rec["scrolls"] % max(1, int(loop.get("scroll_max_tries", 10))) == 0:
            back = -step * 6            # 反向多滚几屏回顶部
            ctx.mouse.scroll(back, cx, cy)
            ctx.log(f"已在图文列表里滚了 {rec['scrolls']} 屏仍未找到心形图案，反向滚回重找…", level="warn")
            rec["not_found_since"] = time.time()

    # ---- 收尾：点满次数/超时后逐层关面板回到主界面 → 本轮结束 ----
    #   用户拍板 2026-09-22：点完 5 个心形图案之后返回主界面（遗留面板会挡乱下个任务/下个号）。
    #   复用 ui_state.back_to_main_screen：按 Esc 或 Esc 弹不干净就多按几层，直到画面认出商城图标。
    def _do_done(self, ctx, rec, loop, regions, threshold):
        st = ui_state.back_to_main_screen(ctx.cfg, ctx.window)
        done_msg = f"该号鉴赏结束，共点击 {rec['clicks']} 个心形图案。"
        if st is True:
            ctx.log(f"已返回主界面。{done_msg}", level="hit")
        elif st is None:
            ctx.log(f"无法判定主界面（未标定商城图标），不盲按；{done_msg}", level="warn")
        else:
            ctx.log(f"连按 Esc 仍未回主界面（可能有弹窗卡住）；{done_msg}", level="warn")
        rec["done"] = True

    # ---- 卡死兜底（仅鉴赏前状态：关面板重新开活动）----
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
        趣味鉴赏的参加按钮模板为 appr_join；卡片为 appr_entry。"""
        return self._find_join_in_column(ctx, list_region, entry_screen_xy, threshold, loop,
                                         self.flags.get("appr_join"),
                                         self.flags.get("appr_entry"))

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

    def _match_region(self, cur, rect, flag_key, threshold):
        """在【图文列表区域】rect 里匹配 flag_key（心形图案），命中返回屏幕绝对 (x,y,score)，否则 None。"""
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (rect[0] + m[0], rect[1] + m[1], m[2])

    def _dry_run_selfcheck(self, ctx, contexts, multi, regions, threshold, switch_delay, deadline):
        """演练：先把每个号切到活动界面（发 open_activity 快捷键），再周期性识别各标志，报告命中。
        否则一开始屏上还是主界面、找不到任务入口卡片=误判（用户拍板：演练也要先开活动列表再识别）。"""
        keys = [("appr_entry", "活动卡片"), ("appr_join", "参加"),
                ("appr_heart", "心形图案")]
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