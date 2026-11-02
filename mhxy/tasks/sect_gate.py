# -*- coding: utf-8 -*-
"""
门派闯关任务（周常）。
"""

import time

from ..core import vision
from ..ui import ui_state
from .weekly_base import WeeklyBaseTask
from .base import register  # noqa: F401


@register
class SectGateTask(WeeklyBaseTask):
    name = "sect_gate"
    title = "门派闯关"
    description = "周常：门派闯关（多人组队，队长跑循环，漫无目标时自动中止）"
    NEEDS_CLAIM_TASK = True

    _FLAG_KEYS = ["card", "join", "confirm", "claim_task", "enter", "battle_flag", "clock"]

    # 门派闯关寻路到 NPC 后对话框里那个按钮文案就是「门派闯关」（不是通用的「参加活动」），
    # 故把共用 spec 里 confirm 键的【显示名】改成「门派闯关按钮」——键仍为 confirm（流程读图与
    # 已标定模板都按该键存取，不受影响）；海底世界/迷魂塔沿用基类显示名「NPC『参加活动』按钮」。
    CALIBRATION = {
        **WeeklyBaseTask.CALIBRATION,
        "templates": [
            (it[0], "门派闯关按钮", *it[2:]) if it[0] == "confirm" else it
            for it in WeeklyBaseTask.CALIBRATION["templates"]
        ],
    }

    def _do_accept(self, ctx, loop, regions, threshold):
        """领任务：开活动 → 找卡片 → 点参加 → 轮询依次处理 门派闯关(confirm)/领取任务/
        进入战斗/小闹钟，并以战斗中标识为完成（同帧多按钮时按此先后，前置按钮优先点击）。"""
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），中止。", level="error")
            self._abort_capture(ctx, regions)
            return False
        ctx.log("已打开活动，翻找活动卡片…")
        if not self._wait_activity_ui(ctx, threshold, loop.get("activity_ui_wait_sec", 3.0)):
            self._interruptible_sleep(ctx, self._jitter(0.6, ctx))

        list_region = regions.get("activity_list")
        rec = {"_join_warned": False, "_nudges": 0, "_low_warned": False}
        r = self._find_card_once(ctx, rec, list_region, loop, regions, threshold)
        if ctx.should_stop():
            return False
        if r == "stopped":
            return False
        if r == "completed":
            ctx.log("该活动卡片显示「完成」，判定本号本任务已完成。", level="hit")
            return False
        if r != "join_clicked":
            reason = "活动卡片未找到（列表整段翻完）" if r == "abort_no_card" else "认出卡片但没找到「参加」按钮"
            ctx.log(f"领任务中止：{reason}", level="error")
            self._abort_capture(ctx, regions)
            return False

        ctx.log("已点「参加」，轮询检测参加活动/领取任务/进入战斗/战斗中标识…")
        confirm_tpl = self.flags.get("confirm")
        claim_tpl = self.flags.get("claim_task")
        enter_tpl = self.flags.get("enter")
        battle_tpl = self.flags.get("battle_flag")
        clock_tpl = self.flags.get("clock")

        deadline = time.time() + float(loop.get("enter_wait_sec", 60))
        poll = 1.0
        while not ctx.should_stop():
            if time.time() > deadline:
                ctx.log("等任务迹象超时，中止。", level="error")
                self._abort_capture(ctx, regions)
                return False
            rect = self._scene_rect(ctx, regions)
            cur = ctx.window.grab_screen(rect) if rect else None
            if cur is not None:
                # ① 战斗中标识只读判定（不点）：已进战斗就算本阶段成功，优先返回，
                #    避免下一帧残留的「进入战斗」被重复点。
                if battle_tpl is not None and ui_state.is_present(cur, self.flags, "battle_flag", threshold):
                    ctx.log("检测到战斗中标识，进入任务场景。", level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                    return True
                # 可点按钮严格按真实先后顺序检测：NPC「门派闯关」(confirm) → 领取任务(claim_task)
                # → 进入战斗(enter)。同一帧里前置按钮还在（上一点没生效/画面未变）时必须先点前置，
                # 绝不能因为「进入战斗」也在就先点它（user 2026-11-02：两按钮同帧时点错成进入战斗）。
                # ② NPC 对话框的「门派闯关」按钮（confirm）。
                if confirm_tpl is not None:
                    hit = vision.match(cur, confirm_tpl, threshold,
                                       template_name="confirm", task_name=self.name, window_rect=rect)
                    if hit is not None:
                        self.click_after_hit(ctx, rect[0] + hit[0], rect[1] + hit[1])
                        ctx.log(f"检测到「门派闯关」按钮，已点击（{hit[2]:.3f}）。", level="hit")
                        self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                        continue
                # ③ 「领取任务」按钮（confirm 之后才会出现）。
                if claim_tpl is not None:
                    hit = vision.match(cur, claim_tpl, threshold,
                                       template_name="claim_task", task_name=self.name, window_rect=rect)
                    if hit is not None:
                        self.click_after_hit(ctx, rect[0] + hit[0], rect[1] + hit[1])
                        ctx.log(f"检测到「领取任务」，已点击（{hit[2]:.3f}）。", level="hit")
                        self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                        continue
                # ④ 领完任务后场景里的「进入战斗」按钮（前置都没有时才点）。
                if enter_tpl is not None and self._match_scene(cur, rect, "enter", threshold) is not None:
                    hit = self._match_scene(cur, rect, "enter", threshold)
                    self.click_after_hit(ctx, hit[0], hit[1])
                    ctx.log(f"检测到「进入战斗」按钮，已点击（{hit[2]:.3f}）。", level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                    continue
                # ⑤ 小闹钟（寻路）兜底：点了进任务，没有上述按钮时靠它寻路。
                if clock_tpl is not None and self._match_scene(cur, rect, "clock", threshold) is not None:
                    hit = self._match_scene(cur, rect, "clock", threshold)
                    self.click_after_hit(ctx, hit[0], hit[1])
                    ctx.log(f"检测到「小闹钟」，已点击（{hit[2]:.3f}）。", level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                    return True
            self._interruptible_sleep(ctx, self._jitter(poll, ctx))
        return True


    def _any_signal(self, ctx, cur, rect, threshold):
        if cur is None:
            return False
        battle_tpl = self.flags.get("battle_flag")
        if battle_tpl is not None and ui_state.is_present(cur, self.flags, "battle_flag", threshold):
            return True
        enter_tpl = self.flags.get("enter")
        if enter_tpl is not None and self._match_scene(cur, rect, "enter", threshold) is not None:
            return True
        clock_tpl = self.flags.get("clock")
        if clock_tpl is not None and self._match_scene(cur, rect, "clock", threshold) is not None:
            return True
        claim_tpl = self.flags.get("claim_task")
        if claim_tpl is not None:
            hit = vision.match(cur, claim_tpl, threshold,
                               template_name="claim_task", task_name=self.name, window_rect=rect)
            if hit is not None:
                return True
        confirm_tpl = self.flags.get("confirm")
        if confirm_tpl is not None:
            hit = vision.match(cur, confirm_tpl, threshold,
                               template_name="confirm", task_name=self.name, window_rect=rect)
            if hit is not None:
                return True
        return False

