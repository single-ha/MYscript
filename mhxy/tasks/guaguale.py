# -*- coding: utf-8 -*-
"""
刮刮乐（单人任务，可单独跑，也可进「日常·个人组」每窗口独立链）。

流程（每个所选窗口各刮一遍即完成今日刮刮乐）：
  主界面点「福利」按钮 → 点「打开刮刮乐」按钮 → 在标定的「刮奖区域」里按住鼠标 Z 字
  逐行来回扫、填满整块区域（loop.scratch_passes 遍） →
  有「节日奖励」按钮（今天有得领）就点它 → 等「领取节日奖励」按钮出现再点 → 等领奖弹窗
  里的「奖励」图标出现再点（点了才真领奖）→
  收尾：优先点共享「面板关闭按钮」(panel_close) 关层（领奖弹窗/刮奖界面/福利面板，最多
  loop.close_tries 层），找不到或没标就降级 ui_state.back_to_main_screen 逐层 Esc 回主界面。
没有「节日奖励」按钮（今天没有/已领过）→ 直接进收尾。

实现：非阻塞轮转状态机（每号一份 record，_step_once 只推一小步），run() 逐号把状态机一直
推到 done；make_chain_driver() 把同一套状态机交给「日常·多开每窗口独立链」。刮奖拆成
「一行一步」，行与行之间可被停止打断。
刮奖是几秒的长连续动作，频繁切前台反而低效，故 CHAIN_SEQUENTIAL=True（日常多开时逐号做完）。

标定：`scratch_area`「刮奖区域」是唯一必标区域（scene 留空=整窗当识别区）；`welfare`「福利」
按钮、`open_scratch`「打开刮刮乐」按钮两张模板必标；节日奖励三张模板
（festival_btn/claim_festival/reward_icon）**全部可选**——任一没标就整段跳过领奖、直接收尾，
不拖累就绪度。
"""

import random
import time

from ..core import vision
from ..ui import ui_state
from .base import Task, register

_NS = "guaguale"

# 状态机状态（非阻塞：每访问一次只推进一小步）
S_WELFARE = "WELFARE"            # 主界面找「福利」按钮并点
S_OPEN = "OPEN"                  # 等并点「打开刮刮乐」按钮
S_SCRATCH = "SCRATCH"            # 在刮奖区域 Z 字逐行扫（rec["line"]/["pass_no"] 推进）
S_FESTIVAL = "FESTIVAL"          # 等「节日奖励」按钮（可选；超时=直接收尾）
S_CLAIM_WAIT = "CLAIM_WAIT"      # 点完节日奖励，等「领取节日奖励」按钮出现
S_CLAIM = "CLAIM"                # 点「领取节日奖励」
S_REWARD = "REWARD"              # 等领奖弹窗里的「奖励」图标并点（点了才领奖）
S_CLOSE = "CLOSE"                # 收尾关面板回主界面

# 必标模板 / 节日奖励三张可选模板（成组：任一没标就整段跳过领奖分支）
_REQ_TPL = [("welfare", "福利按钮"), ("open_scratch", "打开刮刮乐按钮")]
_FESTIVAL_TPL = ("festival_btn", "claim_festival", "reward_icon")


@register
class GuagualeTask(Task):
    name = _NS
    title = "刮刮乐"
    description = "对每个所选窗口：主界面点「福利」→「打开刮刮乐」→ 刮奖区域刮开 → 顺手领节日奖励（有就领）"
    CHAINS_PER_WINDOW = True   # 可做「日常·每窗口独立链」
    # 刮奖是几秒的长连续动作，切前台会打断节奏、也切不回来几次 → 多开时逐号做完再换号
    CHAIN_SEQUENTIAL = True

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；「福利」「打开刮刮乐」等按钮都在这里找", True),
            ("scratch_area", "刮奖区域", "刮刮乐界面里那块要刮开的银色/灰色区域——把整块刮奖面积框住，脚本就在这块里来回扫"),
        ],
        "templates": [
            ("welfare", "福利按钮", "主界面上的「福利」图标/按钮，框图标+文字，要和其他按钮区分开"),
            ("open_scratch", "打开刮刮乐按钮", "福利面板里那个「打开刮刮乐」按钮，框按钮本身、要独特"),
            ("festival_btn", "节日奖励按钮", "刮奖界面上的「节日奖励」入口按钮；没标=今天没有节日奖励可领时直接收尾，不影响跑", True),
            ("claim_festival", "领取节日奖励按钮", "点开节日奖励后出现的「领取节日奖励」按钮；跟上一项配套，没标就跳过领奖", True),
            ("reward_icon", "奖励图标", "点完「领取节日奖励」弹出的领奖弹窗里的「奖励」图标，点它才真正领奖；配套可选，没标就跳过", True),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        templates = tc.get("templates", {})
        regions = tc.get("regions", {})
        if not regions.get("scratch_area"):
            problems.append("没框「刮奖区域」—— 请点「标定（刮刮乐）」把整块要刮的区域框住")
        for key, label in _REQ_TPL:
            if not templates.get(key) or vision.load_template(templates.get(key)) is None:
                problems.append(f"模板『{label}』缺失或加载失败 —— 请点「标定（刮刮乐）」框选")
        missing_opt = [k for k in _FESTIVAL_TPL if not templates.get(k)]
        if missing_opt and len(missing_opt) < len(_FESTIVAL_TPL):
            # 标了一部分：说明用户想领但会卡在某一步 → 明确提示补标，但不拦跑
            problems.append("「节日奖励」三张模板（节日奖励按钮/领取节日奖励按钮/奖励图标）只标了一部分，"
                            "会领不到奖 —— 请把三张都标齐，或三张都不标（不领）")
        if not ctx.select_windows():
            problems.append("没选到任何目标窗口 —— 请先「选择窗口」")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(_NS)
        loop = tc.get("loop", {}) or {}
        regions = tc.get("regions", {}) or {}
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没选到任何目标窗口，已停止。", level="error")
            return
        multi = ctx.cfg.get("targets", {}).get("multi", False) and len(wins) > 1
        self.flags = self._load_flags(tc)
        for i, w in enumerate(wins):
            if ctx.should_stop():
                break
            label = f"号{self._window_no(w, i)}" if multi else None
            child = ctx.make_child(w, label)
            # 切前台并校验（铁律）：activate 失败=没真正到前台，绝不在后台号瞎点，跳过该号。
            if not w.activate():
                child.log("切前台失败（系统拒绝焦点抢占），跳过该号。", level="warn")
                continue
            self._interruptible_sleep(child, self._jitter(0.3, child))
            try:
                rec = self._new_record(child, loop, regions)
                while not rec["done"] and not child.should_stop():
                    self._step_once(child, rec)
                    if rec["done"]:
                        break
                    self._interruptible_sleep(child, self._jitter(0.4, child))
            except Exception as e:
                child.log(f"该号刮刮乐异常，跳过：{e}", level="error")
        ctx.log("★ 刮刮乐结束。", level="hit")

    # ------------------------------------------------------------------
    # 每号状态机
    # ------------------------------------------------------------------
    @staticmethod
    def _new_record(wctx, loop, regions):
        area = regions.get("scratch_area")
        gap = max(4, int(loop.get("scratch_line_gap", 10)))
        h = (area or (0, 0, 0, 0))[3]
        n_rows = max(1, int((h + gap - 1) // gap)) if h else 1
        return {"state": S_WELFARE, "t_state": time.time(),
                "threshold": float(loop.get("match_threshold", 0.85)),
                "timeout": float(loop.get("step_timeout_sec", 20)),
                "festival_wait": float(loop.get("festival_wait_sec", 4)),
                "claim_wait": float(loop.get("claim_wait_sec", 6)),
                "reward_wait": float(loop.get("reward_icon_wait_sec", 6)),
                "regions": regions,
                "area": area, "n_rows": n_rows,
                "step": max(3, int(loop.get("scratch_sample_step", 8))),
                "speed": max(0.5, float(loop.get("scratch_speed", 1.6))),
                "passes": max(1, int(loop.get("scratch_passes", 2))),
                "close_tries": max(1, int(loop.get("close_tries", 3))),
                "line": 0, "pass_no": 0,
                "festival_ok": all(wctx.task_cfg(_NS).get("templates", {}).get(k)
                                   for k in _FESTIVAL_TPL),
                "done": False}

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    def _step_once(self, ctx, rec):
        """推进一个很小的动作后返回（非阻塞，好轮转到下一个号/交回日常驱动）。"""
        st = rec["state"]
        if st == S_WELFARE:
            self._do_welfare(ctx, rec)
        elif st == S_OPEN:
            self._do_open(ctx, rec)
        elif st == S_SCRATCH:
            self._do_scratch_line(ctx, rec)
        elif st == S_FESTIVAL:
            self._do_festival(ctx, rec)
        elif st == S_CLAIM_WAIT:
            self._do_claim_wait(ctx, rec)
        elif st == S_CLAIM:
            self._do_claim(ctx, rec)
        elif st == S_REWARD:
            self._do_reward(ctx, rec)
        elif st == S_CLOSE:
            self._do_close(ctx, rec)

    def _do_welfare(self, ctx, rec):
        """主界面找「福利」按钮并点 → 等刮刮乐入口按钮。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        hit = self._match_scene(cur, scene_rect, "welfare", rec["threshold"])
        if hit is None:
            if time.time() - rec["t_state"] > rec["timeout"]:
                ctx.log(f"超 {rec['timeout']:.0f}s 没在画面里找到「福利」按钮——请确认已在主界面、"
                        "模板已标定。", level="error")
                rec["done"] = True
            return
        self.click_after_hit(ctx, hit[0], hit[1])
        ctx.log(f"点「福利」（{hit[2]:.3f}）。", level="hit")
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        self._goto(rec, S_OPEN)

    def _do_open(self, ctx, rec):
        """等并点「打开刮刮乐」按钮 → 进入刮奖界面（超时中止该号，绝不瞎点）。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        hit = self._match_scene(cur, scene_rect, "open_scratch", rec["threshold"])
        if hit is None:
            if time.time() - rec["t_state"] > rec["timeout"]:
                ctx.log(f"超 {rec['timeout']:.0f}s 没等到「打开刮刮乐」按钮——可能福利面板没打开或"
                        "没认到，跳过该号。", level="error")
                rec["done"] = True
            return
        self.click_after_hit(ctx, hit[0], hit[1])
        ctx.log(f"点「打开刮刮乐」（{hit[2]:.3f}），开始刮奖…", level="hit")
        self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
        rec["line"] = 0
        rec["pass_no"] = 0
        self._goto(rec, S_SCRATCH)

    def _do_scratch_line(self, ctx, rec):
        """刮奖：每次推进**一行**（按住→沿该行逐点 glide→松开），行间可被停止打断。

        Z 字来回（每行方向 = (行号 + 遍数) % 2），逐遍填满整块刮奖区域；
        行间用普通 sleep 而非 _interruptible_sleep——那会跑弹窗守卫，可能把刮奖界面上
        同款样式的「×」误当弹窗点掉、中途把界面关了（同拓印挂起守卫的理由，这里直接不调）。
        """
        area = rec["area"]
        if not area or rec["n_rows"] <= 0:
            ctx.log("「刮奖区域」没框/为空，无法刮奖，跳过该号。", level="error")
            rec["done"] = True
            return
        if rec["pass_no"] >= rec["passes"]:
            ctx.log(f"刮奖完成（扫了 {rec['pass_no']} 遍区域）。", level="hit")
            self._goto(rec, S_FESTIVAL)
            return
        if rec["line"] >= rec["n_rows"]:
            rec["pass_no"] += 1
            rec["line"] = 0
            return
        pts = self._row_points(area, rec["line"], rec["n_rows"], rec["step"],
                               reverse=((rec["line"] + rec["pass_no"]) % 2 == 1))
        mouse = ctx.mouse
        speed = rec["speed"] * random.uniform(0.9, 1.2)
        mouse.human_move(pts[0][0], pts[0][1], speed=speed * 1.2)
        mouse.hold()
        try:
            for px, py in pts[1:]:
                if ctx.should_stop():
                    break
                mouse.glide(px, py, speed=speed * random.uniform(0.9, 1.15))
        finally:
            mouse.release()
        rec["line"] += 1
        time.sleep(self._jitter(0.05, ctx))   # 行间短暂抬笔（拟人；不跑弹窗守卫）

    def _do_festival(self, ctx, rec):
        """等「节日奖励」按钮：三张可选模板没标齐 → 整段跳过；超时没出现 = 今天没得领 → 直接收尾。"""
        if not rec["festival_ok"]:
            self._goto(rec, S_CLOSE)
            return
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        hit = self._match_scene(cur, scene_rect, "festival_btn", rec["threshold"])
        if hit is not None:
            self.click_after_hit(ctx, hit[0], hit[1])
            ctx.log(f"点「节日奖励」（{hit[2]:.3f}），等领取按钮…", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
            self._goto(rec, S_CLAIM_WAIT)
            return
        if time.time() - rec["t_state"] > rec["festival_wait"]:
            ctx.log("今天没有「节日奖励」可领（或已领过），跳过领奖、直接收尾。")
            self._goto(rec, S_CLOSE)

    def _do_claim_wait(self, ctx, rec):
        """点完节日奖励后等「领取节日奖励」按钮出现（界面有延迟，不等就瞎点容易点空）。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        if self._match_scene(cur, scene_rect, "claim_festival", rec["threshold"]) is not None:
            self._goto(rec, S_CLAIM)
            return
        if time.time() - rec["t_state"] > rec["claim_wait"]:
            ctx.log(f"点了「节日奖励」但超 {rec['claim_wait']:.0f}s 没等到「领取节日奖励」按钮，"
                    "跳过领奖、直接收尾。", level="warn")
            self._goto(rec, S_CLOSE)

    def _do_claim(self, ctx, rec):
        """点「领取节日奖励」→ 等领奖弹窗里的「奖励」图标。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        hit = self._match_scene(cur, scene_rect, "claim_festival", rec["threshold"])
        if hit is None:
            if time.time() - rec["t_state"] > rec["claim_wait"]:
                ctx.log("「领取节日奖励」按钮又不见了，跳过领奖、直接收尾。", level="warn")
                self._goto(rec, S_CLOSE)
            return
        self.click_after_hit(ctx, hit[0], hit[1])
        ctx.log(f"点「领取节日奖励」（{hit[2]:.3f}），等领奖弹窗里的「奖励」图标…", level="hit")
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        self._goto(rec, S_REWARD)

    def _do_reward(self, ctx, rec):
        """等领奖弹窗里的「奖励」图标并点（点了才真领奖）；超时跳过，不瞎点。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = ctx.window.grab_screen(scene_rect) if scene_rect else None
        hit = self._match_scene(cur, scene_rect, "reward_icon", rec["threshold"])
        if hit is not None:
            self.click_after_hit(ctx, hit[0], hit[1])
            ctx.log(f"点「奖励」图标（{hit[2]:.3f}），节日奖励已领。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
            self._goto(rec, S_CLOSE)
            return
        if time.time() - rec["t_state"] > rec["reward_wait"]:
            ctx.log(f"超 {rec['reward_wait']:.0f}s 没等到领奖弹窗里的「奖励」图标，跳过、直接收尾。",
                    level="warn")
            self._goto(rec, S_CLOSE)

    def _do_close(self, ctx, rec):
        """收尾：优先点共享「面板关闭按钮」(panel_close) 逐层关（刮奖界面/福利面板等），
        找不到或没标定就降级 ui_state.back_to_main_screen 逐层 Esc 回主界面。"""
        closed = 0
        for _ in range(rec["close_tries"]):
            if ctx.should_stop():
                break
            hit = ui_state.find_panel_close(ctx.cfg, ctx.window)
            if hit is None:
                break
            self.click_after_hit(ctx, hit[0], hit[1])
            closed += 1
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        if closed:
            ctx.log(f"收尾关了 {closed} 层面板。", level="info")
        st = ui_state.is_main_screen(ctx.cfg, ctx.window)
        if st is True:
            ctx.log("该号刮刮乐完成，已回到主界面。", level="hit")
        elif st is None:
            ctx.log("无法判定主界面（大概率没标定「商城图标」），不盲按 Esc；按原流程收尾。",
                    level="warn")
        else:
            st2 = ui_state.back_to_main_screen(ctx.cfg, ctx.window)
            if st2 is True:
                ctx.log("该号刮刮乐完成，已回主界面。", level="hit")
            else:
                ctx.log("连按 Esc 仍未回主界面（可能有弹窗卡住），按原流程收尾。", level="warn")
        rec["done"] = True

    # ------------------------------------------------------------------
    # 日常·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口状态机一步。
        与 run() 共用 _new_record/_step_once；不切前台（由日常总轮转统一切）。"""
        tc = wctx.task_cfg(_NS)
        loop = tc.get("loop", {}) or {}
        regions = tc.get("regions", {}) or {}
        self.flags = self._load_flags(tc)
        rec = self._new_record(wctx, loop, regions)
        return rec, (lambda: self._step_once(wctx, rec))

    # ---- 识别/刮奖工具 ----
    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FESTIVAL_TPL + tuple(k for k, _ in _REQ_TPL)}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return ctx.window.grab_screen(rect) if rect else None

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold, template_name=flag_key, task_name=self.name,
                         window_rect=scene_rect)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    @staticmethod
    def _row_points(area, row, n_rows, step, reverse=False):
        """刮奖区域第 row 行的采样点（屏幕绝对坐标）：略微内缩 2px，避免扫出区域外。"""
        x, y, w, h = area
        cy = int(round(y + h * (row + 0.5) / n_rows))
        x0, x1 = x + 2, x + max(4, w - 2)
        xs = list(range(x0, x1 + 1, max(1, int(step))))
        if xs[-1] < x1:
            xs.append(x1)
        if reverse:
            xs.reverse()
        return [(px, cy) for px in xs]
