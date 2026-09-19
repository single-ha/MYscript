# -*- coding: utf-8 -*-
"""
拓印（全局共享的临摹校验能力；可单独「演练」）。

玩法：刷副本点「进入」后，队长窗口偶发弹「拓印」临摹界面——需按住鼠标沿随机图案描一遍再点「上传」。

本任务把它封装成可在「工具」页「拓印」里单独「演练」的动作：对所选窗口第一个号先切前台、
在其「拓印临摹绘制区」里识别图案并沿骨架描一遍（演练=只描不点上传）。

与刷副本共用一套资产与手感：
  · 标定资产存 tasks.tuoying（绘制区 + 标题/上传模板，在「拓印」页标一次，所有副本共用）
  · 描摹参数读 tasks.dungeon.loop（tuoying_lateral/tuoying_sample_step 等），保证演练手感=实战手感。

本任务只做「单窗描一遍」的演练入口；刷副本时的完整自动流程（探测 → 描 → 上传 →
确认界面不再重现的循环）在 dungeon_base 里。资产只读 tasks.tuoying（旧 tasks.shared / tasks.dungeon
残留值不沿用）。
"""

from ..core import scribble
from ..core import window as win_mod
from .base import Task, register

_NS = "tuoying"
_DUNGEON_NS = "dungeon"


@register
class TuoyingTask(Task):
    name = "tuoying"
    title = "拓印"
    ENSURE_MAIN_ON_START = False  # 演练需要拓印弹窗已在前台，启动时绝不 ESC 关它
    description = "刷副本「拓印」临摹弹窗的自动描摹能力；这里可单独演练描一遍"

    CALIBRATION = {
        "regions": [
            ("tuoying_area", "拓印描摹绘制区(可选)", "(可选/可整窗)「拓印」临摹界面里图案所在那块区域："
             "自动描摹就是按住鼠标沿图案描一遍；所有副本共用，标一次即可。留空=遇拓印弹窗只能转手动临摹。", True),
        ],
        "templates": [
            ("tuoying_title", "「拓印」界面标题", "(可选)刷副本点「进入」后，队长窗口偶尔会弹「拓印」临摹界面："
             "框标题或独特边框即可识别。所有副本共用。不标=遇弹窗转手动临摹。", True),
            ("tuoying_upload", "「上传」按钮", "(可选)拓印临摹完要点的「上传」按钮。所有副本共用。"
             "不标=自动描完后需手动点上传。", True),
        ],
        "watchlist": False,
    }

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        if not (tc.get("regions") or {}).get("tuoying_area"):
            problems.append("还没标定「拓印描摹绘制区」——请先点「标定（拓印）」框选它")
        if not ctx.select_windows():
            problems.append("没选到任何目标窗口 —— 请先「选择窗口」")
        return (len(problems) == 0), problems

    def _run(self, ctx):
        tc = ctx.task_cfg(_NS)
        loop = (ctx.task_cfg(_DUNGEON_NS).get("loop", {}) or {})
        area = (tc.get("regions") or {}).get("tuoying_area")
        wins = ctx.select_windows()
        if not wins:
            ctx.log("没选到任何目标窗口，已停止。", level="error")
            return
        if not self._is_admin():
            ctx.log("⚠ 当前非管理员：游戏前台时鼠标注入可能被拦截，建议以管理员重开。", level="warn")

        # 拓印只有队长弹，演练取第一个选中窗口即可。
        w = wins[0]
        if not w.activate():
            ctx.log("切前台失败（系统拒绝焦点抢占），无法演练。请手动把窗口调到前台后重试。", level="warn")
            return
        self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
        ctx.window = w            # 让绘制区换算基于该窗口
        rect = w.region_to_screen_rect(area) if area else w.rect()
        if rect is None:
            ctx.log("绘制区换算失败（窗口未定位），已停止。", level="error")
            return
        frame = win_mod.grab(rect)
        if frame is None:
            ctx.log("绘制区截图失败，请把「拓印」临摹界面调到前台再试。", level="warn")
            return

        # 演练=沿图案描一遍（不点上传），先确认识别得出图案。
        ok_pattern = scribble.has_pattern(frame, label="拓印绘制区")
        ctx.log("拓印·演练：按标定绘制区自动沿图案描一遍（临摹界面若没弹开，请先手动打开它）…", level="warn")
        if not ok_pattern:
            ctx.log("绘制区里没认出图案笔画，未描摹（绝不盲描）。请把「拓印」临摹界面调到前台再试。",
                    level="warn")
            return
        ok = scribble.trace_pattern(ctx.mouse, rect, frame,
                                    lateral=loop.get("tuoying_lateral", 3.0),
                                    sample_step=loop.get("tuoying_sample_step", 5.0),
                                    speed=1.0,
                                    label="拓印绘制区")
        ctx.log("描摹完成，请检查界面是否认过（演练不会替你点「上传」）。" if ok
                else "没能识别出图案笔画，未描摹——请确认临摹界面已弹出、且绘制区框得对。",
                level="hit" if ok else "warn")