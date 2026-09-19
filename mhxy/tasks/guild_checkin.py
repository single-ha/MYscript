# -*- coding: utf-8 -*-
"""
帮派签到（共享能力封装，可单独一键跑，也可进「日常一条龙·个人组」每窗口独立链）。

流程（每个所选窗口各跑一遍即完成当日签到）：
  发「打开帮派」快捷键（open_guild，默认 Alt+B）→ 等「福利」页签出现并点它 → 等「签到」按钮出现并点它
  → 用 Esc(close_panel) 关掉帮派界面。

实现：非阻塞轮转状态机（每号一份 record，_step_once 只推一小步，随时让出）。
run() 逐号把状态机一直推到 done（一气呵成）；make_chain_driver() 把同一套状态机交给
「日常一条龙·多开每窗口独立链」逐次推进。任务始终实跑（无演练模式），点到即生效。
配置/标定放共享命名空间 tasks.guild_checkin（scene 留空=整窗检测；模板=福利页签 + 签到按钮）。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register

_NS = "guild_checkin"

# 两个必要模板：(key, 界面显示名)。preflight 与演练都要用到，保持单一来源。
_ALL_TPL = [
    ("guild_welfare_tab", "「福利」页签"),
    ("guild_checkin_btn", "「签到」按钮"),
]

# 状态机状态（非阻塞：每访问一次只推进一步；超时跨步累计，见 _do_wait）
S_DRY = "DRY"            # 演练：只识别各标志打日志
S_OPEN = "OPEN"          # 发「打开帮派」快捷键
S_WELFARE = "WELFARE"    # 等点「福利」页签
S_CHECKIN = "CHECKIN"    # 等点「签到」按钮
S_CLOSE = "CLOSE"        # Esc 关界面 → 本轮完成


@register
class GuildCheckinTask(Task):
    name = _NS
    title = "帮派签到"
    description = "对每个所选窗口：打开帮派界面→点「福利」页签→点「签到」按钮，逐号完成签到（可跑日常一条龙）"
    CHAINS_PER_WINDOW = True   # 可做「日常一条龙·每窗口独立链」

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；「福利」页签和「签到」按钮都在这里找", True),
        ],
        "templates": [
            ("guild_welfare_tab", "「福利」页签", "帮派界面顶部的「福利」页签，框按钮本身、要独特"),
            ("guild_checkin_btn", "「签到」按钮", "福利页里的「签到」按钮，框按钮本身、要独特"),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        templates = tc.get("templates", {})
        for key, label in _ALL_TPL:
            if not templates.get(key) or vision.load_template(templates.get(key)) is None:
                problems.append(f"模板『{label}』缺失或加载失败 —— 请点「标定（帮派签到）」框选")
        if not ctx.hotkeys.get("open_guild"):
            problems.append("缺快捷键 open_guild（如 alt+b）—— 请在设置里填")
        if not ctx.select_windows():
            problems.append("没选到任何目标窗口 —— 请先「选择窗口」")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(_NS)
        dry_run = False
        loop = tc.get("loop", {}) or {}
        regions = tc.get("regions", {}) or {}
        threshold = loop.get("match_threshold", 0.85)
        timeout = loop.get("step_timeout_sec", 30)
        self.flags = self._load_flags(tc)
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没选到任何目标窗口，已停止。", level="error")
            return
        multi = ctx.cfg.get("targets", {}).get("multi", False) and len(wins) > 1
        if dry_run:
            ctx.log("帮派签到·演练：只识别「福利」页签/「签到」按钮并打日志，不点任何东西。"
                    "演练前请把号切到帮派界面、停在福利页。", level="warn")
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
                rec = self._new_record(child, dry_run, threshold, timeout, regions)
                while not rec["done"] and not child.should_stop():
                    self._step_once(child, rec)
                    if rec["done"]:
                        break
                    self._interruptible_sleep(child, self._jitter(0.4, child))
            except Exception as e:
                child.log(f"该号签到异常，跳过：{e}", level="error")
        ctx.log("★ 帮派签到结束。", level="hit")

    # ------------------------------------------------------------------
    # 每号状态机
    # ------------------------------------------------------------------
    @staticmethod
    def _new_record(wctx, dry_run, threshold, timeout, regions):
        return {"ctx": wctx, "state": (S_DRY if dry_run else S_OPEN), "t_state": time.time(),
                "threshold": threshold, "timeout": timeout, "regions": regions, "done": False}

    @staticmethod
    def _goto(rec, state):
        rec["state"] = state
        rec["t_state"] = time.time()

    def _step_once(self, ctx, rec):
        """推进行一个很小的动作后返回（非阻塞，好轮转到下一个号/交回一条龙驱动）。"""
        st = rec["state"]
        if st == S_DRY:
            self._do_dry(ctx, rec)
        elif st == S_OPEN:
            self._do_open(ctx, rec)
        elif st == S_WELFARE:
            self._do_wait(ctx, rec, "guild_welfare_tab", "「福利」页签", S_CHECKIN)
        elif st == S_CHECKIN:
            self._do_wait(ctx, rec, "guild_checkin_btn", "「签到」按钮", S_CLOSE)
        elif st == S_CLOSE:
            self._do_close(ctx, rec)

    def _do_dry(self, ctx, rec):
        cur = self._grab_scene(ctx, rec["regions"])
        for key, label in _ALL_TPL:
            best = self._best_score(cur, key)
            if best is None:
                ctx.log(f"「{label}」未见（当前画面里可能没有它）。")
            else:
                ctx.log(f"「{label}」相似度 {best:.2f}（阈值 {rec['threshold']}）。")
        rec["done"] = True

    def _do_open(self, ctx, rec):
        if not ctx.send_hotkey("open_guild"):
            ctx.log("发送「打开帮派」快捷键失败（未配置 open_guild），跳过该号。", level="error")
            rec["done"] = True
            return
        ctx.log("已发送「打开帮派」，等「福利」页签…")
        self._interruptible_sleep(ctx, self._jitter(1.0, ctx))
        self._goto(rec, S_WELFARE)

    def _do_wait(self, ctx, rec, key, label, next_state):
        """盯一个模板：出现并点一次→进下一步；超时（跨步累计）或停止→该号结束。每步只截一帧检一次。"""
        scene_rect = self._scene_rect(ctx, rec["regions"])
        cur = win_mod.grab(scene_rect)
        hit = self._match_scene(cur, scene_rect, key, rec["threshold"])
        if hit is not None:
            ctx.mouse.click(hit[0], hit[1])
            ctx.log(f"点「{label}」（{hit[2]:.3f}）。", level="hit")
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            self._goto(rec, next_state)
            return
        if time.time() - rec["t_state"] > rec["timeout"]:
            ctx.log(f"等「{label}」出现超时（界面没打开？已签到/不在该界面？），该号结束。", level="warn")
            rec["done"] = True

    def _do_close(self, ctx, rec):
        # 签完用 Esc 关掉帮派界面，避免留在界面上影响后续操作。
        if ctx.send_hotkey("close_panel"):
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        else:
            ctx.log("未配置 close_panel 快捷键，签完后不主动关界面。", level="warn")
        ctx.log("该号签到完成。", level="hit")
        rec["done"] = True

    # ------------------------------------------------------------------
    # 日常一条龙·每窗口独立链：暴露「单窗口一份 record + 单步推进函数」
    # ------------------------------------------------------------------
    def make_chain_driver(self, wctx):
        """给定单窗口上下文，返回 (record, step_fn)。step_fn() 推进该窗口状态机一步。
        与 run() 共用 _new_record/_step_once；不切前台（由一条龙总轮转统一切）。"""
        tc = wctx.task_cfg(_NS)
        loop = tc.get("loop", {}) or {}
        regions = tc.get("regions", {}) or {}
        threshold = loop.get("match_threshold", 0.85)
        timeout = loop.get("step_timeout_sec", 30)
        self.flags = self._load_flags(tc)
        rec = self._new_record(wctx, False, threshold, timeout, regions)
        return rec, (lambda: self._step_once(wctx, rec))

    # ---- 识别工具（与运镖/宝图同源实现）----
    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in [key for key, _lbl in _ALL_TPL]}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _match_scene(self, cur, scene_rect, key, threshold):
        tpl = self.flags.get(key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _best_score(self, cur, key):
        tpl = self.flags.get(key)
        if cur is None or tpl is None:
            return None
        try:
            m = vision.match(cur, tpl, 0.0)
        except Exception:
            return None
        return m[2] if m is not None else None