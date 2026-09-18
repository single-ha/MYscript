# -*- coding: utf-8 -*-
"""
通用副本基类（刷副本页收录副本的通用实现）。

需求背景：刷副本页要跑 5 个副本，分两个标签区：
  · 普通区（70级普通 / 60级普通1 / 60级普通2）
  · 侠士区（70级侠士 / 60级侠士）：进副本后要轮询所有选中窗口点「确认」。
  进入副本前有差异，进入后流程完全一样——所以【标定共用一套】，只按普通/侠士区分。

本轮确认的几个关键点（本基类实现）：
  ① 卡片按标签区分：活动列表里普通/侠士卡片各一张模板（entry_common/entry_xiashi），
     找卡片用当前副本 cat 那张。
  ② 「进入」按钮共用一份（enter_dungeon）：普通/侠士两个标签区的「进入」长得一样，共用一张模板；
     区分标签靠「侠士区」标签页（xiashi_tab）——侠士副本进副本前先点该标签页切到侠士区，再找「进入」。
  ③ 多命中点「进入」定位：同一标签区几个「进入」长得一样，无法靠图像区分，
     故用 vision.match_multi 全屏检出【共用「进入」模板】的所有命中点，按(行,列)排好，
     取「当前这个副本在【同标签区】展示顺序里的第几个」命中点来点（以 GUI 该区展示顺序当基准，
     不能用勾选队列算——只勾一个它在队列里是 0，物理位置却未必是列表第一个）。
     「当前副本是第几个」由刷副本页在启动时写入 tasks.dungeon.enter_target 运行时字段。
  ④ 侠士「确认」轮询：队长点完「进入」后，轮询所有选中窗口，用共用「确认」模板把各号弹的确认点掉；
     全确认完再继续。某号确认超时 → 返回队长窗口重新点「进入」（本基类在 enter+confirm 外套一层重试）。
  ⑤ 「拓印」临摹弹窗（user 2026-09-08 反馈）：点「进入」后队长窗口【偶发】弹「拓印」临摹界面
     （只有队长弹、队员不弹），需按住鼠标沿随机图案描一遍再点「上传」才能继续进本、且无放弃/跳过按钮。
     处理：识别「拓印」标题 → 在标定的绘制区做拟人化区域填扫（core/scribble，图案随机但位置固定）→ 点上传；
     描完后轮询界面关闭；未标定或描完没被认可 → 日志提示手动临摹，轮询到界面消失自动继续。
     普通副本路径顺带补「已进本验证（settlement/跳过/小闹钟任一出现）+ 超时重试」，拓印掩盖的失败不再傻等。
     标定位置（全部在「工具」页「拓印」里标，存 tasks.tuoying，所有副本共用；只读这份新值，
     旧 tasks.shared / tasks.dungeon 里残留的旧值一律不沿用）：
     tuoying_title / tuoying_upload 模板存 tasks.tuoying.templates；tuoying_area 绘制区存 tasks.tuoying.regions
     （run() 里单独并入本任务 regions）。

子类只写：name / title / cat。其余完全通用。

副本内每轮流程（user 拍板）：点跳过剧情 → 点小闹钟寻路 → 点进入战斗 → 等战斗打完 → 循环直到副本结束
收尾判定（settlement 结算界面）：每轮等上一场打完时**主动轮询**是否出现结算界面——识别到即判副本结束、
立即进收尾，替代「纯等超时」；轮数上限 max_rounds 仍是防死循环兜底。
收尾动作：判结束后，再点一下屏幕 + 点小闹钟。

配置/标定全存共享 tasks.dungeon（见 DUNGEON_CALIBRATION），各副本无独立配置。
"""

import time

from ..core import scan
from ..core import scribble
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES,
                            DISBAND_REQUIRED_TEMPLATES)
from .base import Task, register

DUNGEON_NS = "dungeon"                       # 共享配置命名空间（tasks.dungeon）

# 共享模板键（绝大部分存 tasks.dungeon.templates；tuoying_title/upload 存 tasks.tuoying.templates，
# 在「工具」页「拓印」里标定，见 TUOYING_TPL_KEYS；由 _load_flags 单独读入，只读 tasks.tuoying）。
# 顺序按操作流程走，并作为标定向导里模板画廊的展示顺序：
#   卡片(普通/侠士) → 参加 → 选择副本 → 侠士区标签页(侠士进副本前) → 进入 → 侠士确认(侠士) → 跳过/闹钟/进入战斗 → 结算界面。
SHARED_TPL_KEYS = ["entry_common", "entry_xiashi", "join", "select",
                   "xiashi_tab", "enter_dungeon", "confirm", "skip", "clock", "enter",
                   "settlement", "tuoying_title", "tuoying_upload"]

# 标定向导/就绪判据用的共享标定 spec（一次标定所有副本）
DUNGEON_CALIBRATION = {
    "regions": [
        ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；对话框/各按钮都在这里找", True),
        # activity_list 已在「通用」页「标定（公共区域）」统一标定（全任务共用），见 tasks.shared，
        # task_config() 已把 shared 叠加进本任务 regions，运行时直接 regions.get 即可。
        # 拓印绘制区 tuoying_area 在「工具」页「拓印」标定（存 tasks.tuoying，run() 单独并入）。
    ],
    "templates": [
        ("entry_common", "普通副本卡片", "活动列表里普通副本的那张卡片——普通副本共用"),
        ("entry_xiashi", "侠士副本卡片", "活动列表里侠士副本的那张卡片——侠士副本共用"),
        ("join", "参加按钮", "卡片右侧的「参加」按钮（所有副本共用）"),
        ("select", "选择副本按钮", "「选择副本」对话框里的按钮（所有副本共用）"),
        ("xiashi_tab", "侠士区标签页", "「选择副本」里的「侠士区」标签页，侠士副本先进副本前先点到它（仅侠士用）"),
        ("enter_dungeon", "「进入」按钮", "选择副本对话框里的「进入」按钮（普通/侠士两标签区共用）"),
        ("confirm", "侠士「确认」按钮", "侠士进副本后各号弹的「确认」按钮（仅侠士用）"),
        ("skip", "跳过剧情按钮", "副本内每轮先点的「跳过剧情」按钮（共用）"),
        ("clock", "小闹钟寻路", "任务栏「小闹钟」，点它寻路到当前目标（共用）"),
        ("enter", "进入战斗按钮", "寻路到位后点它发起本场的「进入战斗」按钮（共用）"),
        ("settlement", "结算界面", "副本结束时的结算画面（识别到即判结束收尾，共用）"),
    ],
    "watchlist": False,
}


def share_tpl(cfg, key):
    """读共享副本模板（tasks.dungeon.templates[key]）并加载成图像；未标定返回 None。"""
    tc = cfg.get("tasks", {}).get(DUNGEON_NS, {})
    p = (tc.get("templates") or {}).get(key)
    return vision.load_template(p) if p else None


class DungeonBaseTask(Task):
    name = "dungeon_base"
    title = "通用副本"
    cat = "common"                 # "xiashi"(侠士) 或 "common"(普通)——决定用哪个「进入」标签区
    is_dungeon = True
    PREF = None                    # 已废弃：标定/配置全走共享 tasks.dungeon，不再按副本前缀模板

    @classmethod
    def _card_key(cls):
        """本副本标签区的「卡片」模板键（活动列表里找卡片用）。"""
        return f"entry_{cls.cat}"

    @classmethod
    def _enter_key(cls):
        """进副本的「进入」按钮模板键：普通/侠士共用一张（enter_dungeon）。"""
        return "enter_dungeon"

    @classmethod
    def _need_xiashi_tab(cls):
        """侠士副本进副本前需先切到「侠士区」标签页。"""
        return cls.cat == "xiashi"

    def __init__(self):
        self.flags = None
        self._end_prev = None      # 收尾兜底：上一帧截图 / 连续静止计时 / 时刻（识别结算界面一闪而过后回场景的静止画面）
        self._end_still = 0.0
        self._end_prev_t = 0.0

    # ==================================================================
    # preflight：读取共享 tasks.dungeon 资产（组队模板+副本共用模板）检查
    # ==================================================================
    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(DUNGEON_NS)
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        team_tc = ctx.task_cfg("teaming")
        skip_team = team_tc.get("skip_team", False)

        if skip_team:
            if not wins:
                problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好队长所在的号")
        else:
            if not targets.get("multi"):
                problems.append(f"{self.title} 需先组队：请在「选择窗口」切到多开并选好队长+队员（≥2 个号）")
            if len(wins) < 2:
                problems.append(f"组队至少 2 人（队长+队员），当前选中 {len(wins)} 个号")

        cap = team_tc.get("captain_index", 0)
        # 队长序号只在有多个窗口时才有意义：单开（1 个号）不校验，run 里自动把唯一的号当队长
        if len(wins) > 1 and not (0 <= cap < len(wins)):
            problems.append(f"队长序号 号{cap + 1} 越界（共 {len(wins)} 个号），请在「组队设置」重选队长")

        if not skip_team:
            for rk in TEAM_REQUIRED_REGIONS:
                if not team_tc.get("regions", {}).get(rk):
                    problems.append(f"组队区域『{rk}』未标定 —— 请在「通用」页点「标定（组队）」框选")
            for tk in TEAM_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"组队模板『{tk}』缺失 —— 请在「通用」页点「标定（组队）」裁图")

        if team_tc.get("auto_disband", False):
            for tk in DISBAND_REQUIRED_TEMPLATES:
                p = team_tc.get("templates", {}).get(tk)
                if not p or vision.load_template(p) is None:
                    problems.append(f"勾了「跑完解散队伍」但退队模板『{tk}』缺失 —— 请在「通用」页标定「退出队伍」")

        regions = tc.get("regions", {})
        if not regions.get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请到「通用」页点「标定（公共区域）」框选（所有任务共用）")
        templates = tc.get("templates", {})
        req_keys = [(k, label) for (k, label, _d, *_x) in DUNGEON_CALIBRATION["templates"]
                    if not (_x and _x[0])                                    # 第4元组=可选资产（拓印等），不参与就绪与 preflight
                    and not ((k in ("confirm", "xiashi_tab")) and self.cat != "xiashi")]
        for k, label in req_keys:
            p = templates.get(k)
            if not p or vision.load_template(p) is None:
                problems.append(f"副本共用模板『{label}』({k}) 缺失 —— 请在本页「标定」里框选裁图")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("缺快捷键 open_activity（如 alt+c）—— 请在设置里填")
        if not skip_team:
            if not ctx.hotkeys.get("open_team"):
                problems.append("缺快捷键 open_team（如 alt+t）—— 组队要用")
            if not ctx.hotkeys.get("open_friend"):
                problems.append("缺快捷键 open_friend（如 alt+f）—— 组队要用")

        sizes = {tuple(w.rect()[2:4]) for w in wins if w.rect()}
        if len(sizes) > 1:
            ctx.log("提示：所选号尺寸不一致，多开共用标定可能点偏，建议统一分辨率。", level="warn")

        return (len(problems) == 0), problems

    # ==================================================================
    def run(self, ctx):
        tc = ctx.task_cfg(DUNGEON_NS)
        team_tc = ctx.task_cfg("teaming")
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        dry_run = tc.get("dry_run", True)
        skip_team = team_tc.get("skip_team", False)
        cap = team_tc.get("captain_index", 0)
        self.flags = self._load_flags(tc, ctx)

        # 拓印绘制区在「工具」页「拓印」标定（存 tasks.tuoying），单独并入本任务 regions；
        # 只读这份新值（None=未标→遇拓印转手动），其它命名空间残留旧值一律不用。
        regions["tuoying_area"] = self._tuoying_area(ctx)

        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        if not skip_team and len(wins) < 2:
            ctx.log("选中窗口不足 2 个（组队至少队长+1 队员）；若已自行组好队，请勾选「已组队」。", level="error")
            return
        if not (0 <= cap < len(wins)):
            cap = 0

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏在前台时鼠标/键盘注入可能被 UIPI 拦截，建议以管理员重开。",
                    level="warn")

        cap_child = None
        member_pairs = []
        for i, w in enumerate(wins):
            child = ctx.make_child(w, f"号{self._window_no(w, i)}")
            if i == cap:
                cap_child = child
            else:
                member_pairs.append((child, TeamFormation.ROLE_MEMBER))
        assignments = [(cap_child, TeamFormation.ROLE_CAPTAIN)] + member_pairs

        if dry_run:
            ctx.log("演练模式：不组队、不发快捷键/不点，只对各号识别组队+副本模板做自检。", level="warn")
            self._dry_run_selfcheck(ctx, assignments, regions, threshold)
            return

        # —— 组队（已组队则跳过）——
        cap_no = self._window_no(wins[cap], cap)
        if skip_team:
            ctx.log(f"★ {self.title}：已组队，跳过组队，直接由队长（号{cap_no}）跑副本 ★", level="warn")
        else:
            ctx.log(f"★ {self.title}：先组队（队长=号{cap_no}，队员 {len(wins) - 1} 人），再由队长跑副本 ★",
                    level="warn")
            team_cfg = ctx.task_cfg("teaming")
            team = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
            ok, reason = team.run_until_formed()
            if ctx.should_stop():
                return
            if not ok:
                ctx.log(f"组队未完成（{reason}），{self.title} 中止。", level="error")
                return
            ctx.log("组队完成，队长开始跑副本流程…", level="hit")

        # —— 队长跑副本流程（侠士进副本后还要轮询各号点确认）——
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        self._run_dungeon(cap_child, assignments, loop, regions, threshold)

        # —— 自动解散（若勾选）——
        if team_tc.get("auto_disband", False) and not ctx.should_stop():
            ctx.log("副本结束，自动解散队伍（所有号退队）…", level="warn")
            self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
            team_cfg = ctx.task_cfg("teaming")
            team = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
            ok, _ = team.run_disband()
            if ok:
                ctx.log("队伍已解散。", level="hit")

    # ==================================================================
    # 副本流程（普通=蹈海去线性；侠士进副本后多一段确认轮询）
    # ==================================================================
    def _run_dungeon(self, ctx, assignments, loop, regions, threshold):
        self._focus(ctx)
        npc_to = loop.get("npc_dialog_sec", 60)
        step_to = loop.get("step_timeout_sec", 30)
        entry_skip_to = loop.get("entry_skip_sec", 60)
        battle_to = loop.get("battle_timeout_sec", 600)

        if not self._open_and_join(ctx, loop, regions, threshold):
            return
        if not self._click_when(ctx, "select", "选择副本", regions, threshold, npc_to):
            ctx.log("等「选择副本」对话框超时（角色可能还没寻路到 NPC），中止。", level="error")
            return

        # —— 进副本：点「进入」（多命中点定位）→ 偶发「拓印」临摹弹窗处理（队长窗）→
        #      侠士轮询各号确认 / 普通验证已进本；进+确认/进本 外套重试 ——
        if not self._enter_with_tuoying(ctx, assignments, loop, regions, threshold, step_to):
            return

        # —— 副本内每轮：跳过剧情 → 小闹钟寻路 → 进入战斗 → 等战斗 → 循环直到副本结束 ——
        max_rounds = max(1, int(loop.get("max_rounds", 24)))
        skip_to = entry_skip_to
        round_no = 0
        while not ctx.should_stop():
            round_no += 1
            if round_no > max_rounds:
                ctx.log(f"已达最大轮数上限({max_rounds})，进入收尾。", level="warn")
                break
            # 1) 等上一场打完：先主动查是否已到结算界面（判定副本结束），否则等并点「跳过剧情」续战
            #    （首轮用 entry_skip 超时等进本传送/首场前剧情，之后用 battle 超时等上一场打完）
            status = self._wait_round_end(ctx, regions, threshold, skip_to, round_no, loop)
            if status == "settled":
                ctx.log("识别到结算界面，副本已结束，进入收尾。", level="hit")
                break
            if status is None:
                ctx.log("等本场打完超时（副本可能已结束或战斗卡住），进入收尾。", level="warn")
                break
            # 2) 点「小闹钟」寻路到当前目标
            if not self._click_when(ctx, "clock", "小闹钟寻路", regions, threshold, step_to):
                ctx.log("点「小闹钟」寻路超时，中止。", level="error")
                return
            # 3) 点「进入战斗」发起本场：寻路到位后偶尔会先弹 NPC 对话（点任意处可推进、不点过会儿自动过，对话结束自动进战斗），
            #    故「进入战斗」迟迟不出现时点一下场景推进对话，而不是干等超时误判副本结束。
            tap_every = max(0.0, float(loop.get("npc_dialog_tap_sec", 5.0)))
            if not self._click_enter_or_dialog(ctx, regions, threshold, step_to, tap_every):
                ctx.log(f"第 {round_no} 场「进入战斗」按钮没出现（副本可能已结束），进入收尾。", level="warn")
                break
            ctx.log(f"第 {round_no} 场已发起，等它打完…", level="hit")
            skip_to = battle_to   # 之后等「跳过剧情」= 等上一场打完

        # —— 收尾：再点一下屏幕推进结算 + 点小闹钟 ——
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        self._click_scene_center(ctx, regions)
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        if self._click_when(ctx, "clock", "小闹钟(收尾)", regions, threshold, step_to):
            ctx.log(f"★ {self.title} 完成，已点小闹钟收尾。★", level="hit")
        else:
            ctx.log("没点到「小闹钟」（副本可能已自动结束）。流程结束。", level="warn")

    # ---- 进副本：多命中点「进入」定位（当前副本=同标签区队列里的第几个）----
    def _click_enter_multi(self, ctx, loop, regions, threshold, timeout):
        # 侠士副本先进副本前先切到「侠士区」标签页，再找「进入」
        if self._need_xiashi_tab():
            if not self._switch_to_xiashi_tab(ctx, regions, threshold, timeout):
                ctx.log("切「侠士区」标签页失败（xiashi_tab 未标定/未识别）。", level="error")
                return False
        tpl = self.flags.get(self._enter_key())
        if tpl is None:
            ctx.log("「进入」模板未标定。", level="error")
            return False
        # 当前副本在同标签区里的序号（由刷副本页启动前写入 tasks.dungeon.enter_target）
        hub = ctx.task_cfg("dungeon")
        target = hub.get("enter_target") or {}
        pos = int(target.get("pos", 0)) if target.get("cat") == self.cat else 0
        deadline = time.time() + timeout
        while not ctx.should_stop():
            rect = self._scene_rect(ctx, regions)
            scene = win_mod.grab(rect) if rect else None
            hits = vision.match_multi(scene, tpl, threshold) if scene is not None else []
            if hits:
                if pos < len(hits):
                    hx, hy, score = hits[pos]
                    sx, sy = (rect[0] if rect else 0), (rect[1] if rect else 0)
                    ctx.mouse.click(sx + hx, sy + hy)
                    ctx.log(f"点「进入」→ 本标签区第 {pos + 1} 个（共检出 {len(hits)} 个，{score:.3f}）。",
                            level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
                    return True
                ctx.log(f"本标签区检出 {len(hits)} 个「进入」，但当前该跑第 {pos + 1} 个，不足，重试…",
                        level="warn")
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        return False

    # ---- 侠士：先点「侠士区」标签页切到侠士区，再点「进入」----
    def _switch_to_xiashi_tab(self, ctx, regions, threshold, timeout):
        tpl = self.flags.get("xiashi_tab")
        if tpl is None:
            ctx.log("「侠士区」标签页模板未标定。", level="error")
            return False
        deadline = time.time() + timeout
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, "xiashi_tab", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点到「侠士区」标签页（{hit[2]:.3f}），切到侠士区。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
                return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        return False

    # ---- 侠士：轮询所有选中窗口点「确认」----
    def _confirm_all_windows(self, ctx, assignments, regions, threshold, timeout):
        tpl = self.flags.get("confirm")
        if tpl is None:
            ctx.log("侠士「确认」模板未标定。", level="error")
            return False
        remaining = list(assignments)
        deadline = time.time() + timeout
        while remaining and not ctx.should_stop():
            for wctx, role in list(remaining):
                if wctx.window.rect() is None:
                    remaining.remove((wctx, role))
                    continue
                if len(remaining) > 1:
                    if not wctx.window.activate():
                        continue
                rect = self._scene_rect(wctx, regions)
                cur = win_mod.grab(rect) if rect else None
                hit = vision.match(cur, tpl, threshold) if cur is not None else None
                if hit is not None:
                    sx, sy = (rect[0] if rect else 0), (rect[1] if rect else 0)
                    wctx.mouse.click(sx + hit[0], sy + hit[1])
                    wctx.log(f"（{wctx.label}）已点确认。", level="hit")
                    remaining.remove((wctx, role))
            if remaining:
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        return not remaining

    def _tuoying_area(self, ctx):
        """有效拓印绘制区：读 tasks.tuoying（「工具」页「拓印」标定）；读不到返回 None。
        直接读原始 cfg 这份新值（不用 ctx.task_cfg("tuoying")，避免历史 shared 叠加串扰）。"""
        tasks = (ctx.cfg or {}).get("tasks", {}) or {}
        tuo = (tasks.get("tuoying", {}) or {}).get("regions", {}) or {}
        return tuo.get("tuoying_area") or None

    # ---- 进副本主入口：点「进入」 + 偶发「拓印」临摹弹窗处理 + 侠士确认/普通验证进本（外套重试）----
    def _enter_with_tuoying(self, ctx, assignments, loop, regions, threshold, step_to):
        retries = max(1, int(loop.get("enter_retry_max", 3)))
        retry_pause = loop.get("enter_retry_pause", 1.2)
        if self.cat == "xiashi":
            for attempt in range(1, retries + 1):
                if ctx.should_stop():
                    return False
                if not self._click_enter_multi(ctx, loop, regions, threshold, step_to):
                    ctx.log(f"{self.title}「进入」未点到（第 {attempt} 次），中止。", level="error")
                    return False
                self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
                if not self._handle_tuoying(ctx, loop, regions, threshold):
                    return False
                confirmed = self._confirm_all_windows(ctx, assignments, regions, threshold,
                                                      loop.get("confirm_sec", 40))
                if confirmed:
                    return True
                ctx.log(f"各号确认未收齐（第 {attempt} 次），返回队长重新点「进入」…", level="warn")
                self._focus(ctx)
                self._interruptible_sleep(ctx, self._jitter(retry_pause, ctx))
            ctx.log("侠士进副本确认多次未完成，中止。", level="error")
            return False
        for attempt in range(1, retries + 1):
            if ctx.should_stop():
                return False
            if not self._click_enter_multi(ctx, loop, regions, threshold, step_to):
                ctx.log(f"{self.title}「进入」未点到（第 {attempt} 次），中止。", level="error")
                return False
            self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
            if not self._handle_tuoying(ctx, loop, regions, threshold):
                return False
            if self._verify_entered(ctx, loop, regions, threshold, loop.get("enter_check_sec", 10.0)):
                return True
            ctx.log(f"点「进入」后未确认已进本（第 {attempt} 次），返回队长重新点「进入」…", level="warn")
            self._focus(ctx)
            self._interruptible_sleep(ctx, self._jitter(retry_pause, ctx))
        ctx.log("普通副本多次点「进入」仍未进本（可能被拓印/弹窗拦截且模板未标定），中止。", level="error")
        return False

    # ---- 拓印临摹弹窗：探测 + 自动描 + 手动兜底。返回 False=中止 ----
    def _handle_tuoying(self, ctx, loop, regions, threshold):
        tpl = self.flags.get("tuoying_title")
        if tpl is None:
            return True                      # 没标标题模板 → 不知道弹没弹，当没弹继续（真弹了由验证超时兜底）
        scene_rect = self._scene_rect(ctx, regions)
        deadline = time.time() + loop.get("tuoying_detect_sec", 8.0)
        while not ctx.should_stop():
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, "tuoying_title", threshold)
            if hit is not None:
                ctx.log(f"识别到「拓印」临摹界面（{hit[2]:.3f}），自动临摹…", level="hit")
                return self._auto_trace(ctx, loop, regions, threshold)
            if time.time() > deadline:
                return True
            self._interruptible_sleep(ctx, self._jitter(0.3, ctx))
        return False

    # ---- 自动临摹：沿图案描 + 点上传 + 等界面消失；一次上传后可能再弹（要拓印两遍）→ 循环到不再重现 ----
    def _auto_trace(self, ctx, loop, regions, threshold):
        area = regions.get("tuoying_area")
        up_key = self.flags.get("tuoying_upload")
        rect = ctx.window.region_to_screen_rect(area) if area else None
        if rect is None or up_key is None:
            ctx.log("⚠ 自动临摹需先标定「拓印临摹绘制区」+「上传按钮」——请手动临摹并点「上传」；脚本会等拓印界面消失后自动继续。",
                    level="warn")
            ctx.log("想全自动就在「通用」页→标定（公共区域）里补标这两项。", level="warn")
            return self._wait_tuoying_gone(ctx, loop, regions, threshold)
        max_rounds = max(1, int(loop.get("tuoying_max_rounds", 3)))
        confirm_sec = loop.get("tuoying_gone_confirm_sec", 1.2)
        for rnd in range(1, max_rounds + 1):
            if ctx.should_stop():
                return False
            ctx.log(f"自动临摹（第 {rnd}/{max_rounds} 遍）：识别图案笔画并沿骨架描 {loop.get('tuoying_passes', 2)} 轮…",
                    level="warn")
            for _pass in range(max(1, int(loop.get("tuoying_passes", 2)))):
                if ctx.should_stop():
                    return False
                # 先截绘制区当前画面：识别图案笔画像素，只沿图案描（描到图案外会拉低完成度）
                frame = win_mod.grab(rect) if rect else None
                if frame is None:
                    ctx.log("⚠ 绘制区截图失败，无法自动临摹——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                            level="warn")
                    return self._wait_tuoying_gone(ctx, loop, regions, threshold)
                ok = scribble.trace_pattern(ctx.mouse, rect, frame,
                                        lateral=loop.get("tuoying_lateral", 3.0),
                                        sample_step=loop.get("tuoying_sample_step", 5.0),
                                        speed=1.0)
                if not ok:
                    ctx.log("⚠ 没能从画面识别出图案笔画——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                            level="warn")
                    return self._wait_tuoying_gone(ctx, loop, regions, threshold)
                if _pass == 0:
                    time.sleep(self._jitter(0.15, ctx))
            if ctx.should_stop():
                return False
            if not self._click_tuoying_upload(ctx, loop, regions, threshold):
                ctx.log("⚠ 描完没点到「上传」，请手动临摹并点「上传」；脚本会等界面消失后自动继续。", level="warn")
                return self._wait_tuoying_gone(ctx, loop, regions, threshold)
            # 上传后：等界面消失，并在确认窗口内盯住「不再重现」才算真过。
            # 拓印实际要描两遍：上传一次后图案相同的临摹界面会再次弹出，须再来一轮。
            if self._tuoying_confirmed_gone(ctx, loop, regions, threshold,
                                            confirm_sec=confirm_sec,
                                            upload_timeout=loop.get("tuoying_upload_sec", 6.0)):
                ctx.log("拓印临摹通过，界面已关闭。", level="hit")
                return True
            ctx.log(f"拓印界面在上传后再次出现（可能需要拓印多遍），进入第 {rnd + 1} 遍…", level="warn")
        ctx.log("⚠ 自动描完多遍后拓印界面仍会重现（校验可能没通过）——请手动临摹并点「上传」；脚本会等界面消失后自动继续。",
                level="warn")
        return self._wait_tuoying_gone(ctx, loop, regions, threshold)

    def _tuoying_confirmed_gone(self, ctx, loop, regions, threshold, confirm_sec, upload_timeout):
        """上传后判定拓印界面「真消失」：先等界面不再出现（upload_timeout 内），
        再持续观察 confirm_sec 内仍不重现才算真过；任何一步在超时内又回到界面都返回 False。"""
        last_seen = time.time()
        no_gone_since = None
        deadline = time.time() + upload_timeout + confirm_sec
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            if time.time() > deadline:
                return False
            cur = win_mod.grab(scene_rect) if scene_rect else None
            seen = self._match_scene(cur, scene_rect, "tuoying_title", threshold) is not None
            if not seen:
                if no_gone_since is None:
                    no_gone_since = time.time()
                elif time.time() - no_gone_since >= confirm_sec:
                    return True          # 消失且确认窗口内不再出现
            else:
                no_gone_since = None
                last_seen = time.time()  # 又开始出现：重置确认窗
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))

    def _click_tuoying_upload(self, ctx, loop, regions, threshold, timeout=None):
        tpl = self.flags.get("tuoying_upload")
        if tpl is None:
            return False
        if timeout is None:
            timeout = loop.get("tuoying_upload_sec", 6.0)
        deadline = time.time() + timeout
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, "tuoying_upload", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log("点「上传」。", level="hit")
                return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
        return False

    # ---- 等拓印界面消失。timeout=None=不限时（手动兜底）。返回 True=已消失 ----
    def _wait_tuoying_gone(self, ctx, loop, regions, threshold, timeout=None):
        tpl = self.flags.get("tuoying_title")
        if tpl is None:
            return True
        deadline = None if timeout is None else time.time() + timeout
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            cur = win_mod.grab(scene_rect) if scene_rect else None
            if self._match_scene(cur, scene_rect, "tuoying_title", threshold) is None:
                return True
            if deadline is not None and time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.35, ctx))
        return False

    # ---- 验证已进本：结算/跳过剧情/小闹钟 任一出现即算进。超时=False ----
    def _verify_entered(self, ctx, loop, regions, threshold, timeout):
        labels = {"settlement": "结算", "skip": "跳过剧情", "clock": "小闹钟"}
        deadline = time.time() + timeout
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            cur = win_mod.grab(scene_rect) if scene_rect else None
            for key, label in labels.items():
                hit = self._match_scene(cur, scene_rect, key, threshold)
                if hit is not None:
                    ctx.log(f"已进本（识别到「{label}」，{hit[2]:.3f}）。", level="hit")
                    return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.35, ctx))
        return False

    # ---- 开活动 → 找本副本卡片 → 点「参加」----
    def _open_and_join(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 未配置），中止。", level="error")
            return False
        ctx.log("已打开活动，翻找副本卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        list_region = regions.get("activity_list")

        def grab_rect():
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            return rect

        def probe(scene, rect):
            hit = vision.match(scene, self.flags.get(self._card_key()), threshold) if scene is not None else None
            if hit is None:
                return scan.SCROLL, None
            entry_xy = (rect[0] + hit[0], rect[1] + hit[1])
            join = self._find_join_on_row(ctx, list_region, entry_xy, threshold, loop)
            if join is not None:
                ctx.mouse.click(join[0], join[1])
                ctx.log(f"找到副本卡片（{hit[2]:.3f}）→ 点「参加」（{join[2]:.3f}），等寻路到 NPC。", level="hit")
                return scan.ACCEPT, join
            ctx.log("认出卡片但没找到右侧「参加」（检查 join 模板/阈值）。", level="warn")
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
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
            return True
        if res.stopped:
            return False
        ctx.log("翻找副本卡片多次未果，中止。", level="error")
        return False

    # ------------------------------------------------------------------
    # 识别/点击工具（与蹈海去同构）
    # ------------------------------------------------------------------
    def _click_when(self, ctx, flag_key, label, regions, threshold, timeout):
        deadline = time.time() + timeout
        last_diag = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, flag_key, threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「{label}」（{hit[2]:.3f}）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                return True
            now = time.time()
            if now > deadline:
                return False
            if now - last_diag >= 15.0:
                ctx.log(f"等「{label}」…（已 {now - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        return False

    def _click_scene_center(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        if rect is None:
            return
        cx, cy = rect[0] + rect[2] // 2, rect[1] + rect[3] // 2
        ctx.mouse.click(cx, cy)
        ctx.log("再点一下屏幕（推进收尾）。")

    def _click_enter_or_dialog(self, ctx, regions, threshold, timeout, tap_every=5.0):
        """点「进入战斗」发起本场。寻路到位后偶尔会先弹 NPC 对话（点任意处可推进、
        不点过会儿也自动继续，对话结束自动进战斗）——故「进入战斗」等 tap_every 秒仍未出现时，
        点一下场景中心推进对话，再继续等。返回 True=点到「进入战斗」；False=超时。"""
        deadline = time.time() + timeout
        center = self._scene_center(ctx, regions)
        last_tap = 0.0
        last_diag = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            hit = self._match_scene(cur, scene_rect, "enter", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「进入战斗」（{hit[2]:.3f}）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                return True
            now = time.time()
            if now > deadline:
                return False
            if tap_every > 0 and center is not None and now - last_tap >= tap_every:
                ctx.mouse.click(center[0], center[1])
                ctx.log(f"「进入战斗」未出现（{now - (deadline - timeout):.0f}s），疑似 NPC 对话，点场景推进…")
                last_tap = now
            if now - last_diag >= 15.0:
                ctx.log(f"等「进入战斗」…（已 {now - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        return False

    def _scene_center(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        if rect is None:
            return None
        return (rect[0] + rect[2] // 2, rect[1] + rect[3] // 2)

    def _wait_round_end(self, ctx, regions, threshold, timeout, round_no, loop=None):
        """等上一场战斗打完：每帧先主动查「结算界面」（副本结束信号），识别到即返回 "settled"；
        否则等并点「跳过剧情」续战（= 上一场已打完可开下一场），返回 "skip"。
        超时仍未出现两者 → 返回 None（由调用方判副本结束收尾）。

        结算识别的阈值比统一阈值放宽 settle_grace（默认 0.05，config.dungeon.loop.settle_grace）：
        结算画面只在一场结束时短暂出现，框样可能与标定图有细微差异，放宽能显著提高召回。
        关键时序：结算画面在【点完跳过剧情后】才弹出，故点跳过剧情后驻留 post_skip_sec（默认 3）秒专盯结算。
        停滞画面兜底：still_end_sec（默认 20，0=关）秒画面连续静止即判副本结束——
        结算界面一闪而过抓不住时，它关闭后回到的静止场景就是结束信号。"""
        deadline = time.time() + timeout
        last_diag = 0.0
        grace = max(0.0, min(0.25, float((loop or {}).get("settle_grace", 0.05))))
        settle_th = threshold - grace
        still_sec = max(0, int((loop or {}).get("still_end_sec", 20)))
        still_diff = float((loop or {}).get("still_diff", 6.0))
        post_skip_sec = max(0.0, float((loop or {}).get("post_skip_sec", 3.0)))
        stpl = self.flags.get("settlement")
        self._end_prev = None
        self._end_still = 0.0
        self._end_prev_t = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None
            now_t = time.time()
            if cur is not None:
                # 兜底：画面连续静止 still_sec 秒 → 判副本已结束。
                # 结算界面一闪而过（会自动关闭）抓不住时，它关闭后回到的静止场景就是结束信号，
                # 不再干等超时。战斗中画面一直在动，不会误判。
                if self._end_prev is not None and still_sec > 0:
                    dd = vision.frame_diff(self._end_prev, cur)
                    dt = now_t - self._end_prev_t
                    if dd < still_diff:
                        self._end_still += dt
                    else:
                        self._end_still = 0.0
                    if self._end_still >= still_sec:
                        ctx.log(f"画面已静止 {still_sec:.0f}s（结算界面一闪而过/副本已结束），判定结束。",
                                level="hit")
                        return "settled"
                self._end_prev = cur
                self._end_prev_t = now_t
            if cur is not None and stpl is not None:
                sm = vision.match(cur, stpl, settle_th)
                if sm is not None:
                    ctx.log(f"识别到结算界面（{sm[2]:.3f}，阈值{settle_th:.2f}），副本结束。", level="hit")
                    return "settled"
            tj = self._match_scene(cur, scene_rect, "skip", threshold) if cur is not None else None
            if tj is not None:
                ctx.mouse.click(tj[0], tj[1])
                ctx.log(f"点「跳过剧情」({round_no})（{tj[2]:.3f}）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                # 结算画面是【点完跳过剧情后】才弹的（最后一场：点跳过剧情=触发结算）。
                # 点完立刻驻留 post_skip_sec 秒专盯结算，命中即副本结束；错过也到期返回，
                # 不再多等，免得非最后一场时把下一场剧情卡住。
                look_end = time.time() + post_skip_sec
                while not ctx.should_stop() and time.time() < look_end:
                    sr2 = self._scene_rect(ctx, regions)
                    cur2 = win_mod.grab(sr2) if sr2 else None
                    if cur2 is not None and stpl is not None:
                        sm2 = vision.match(cur2, stpl, settle_th)
                        mdiag = vision.best_score(cur2, stpl)
                        sc2 = (mdiag[0] if mdiag and mdiag[1] else 0.0)
                        if sm2 is not None:
                            ctx.log(f"点跳过剧情后识别到结算界面（{sm2[2]:.3f}），副本结束。", level="hit")
                            return "settled"
                        ctx.log(f"结算候检未命中：最佳分 {sc2:.2f} / 阈值 {settle_th:.2f}", level="debug")
                    self._interruptible_sleep(ctx, self._jitter(0.25, ctx))
                return "skip"
            if not ctx.should_stop() and now_t > deadline:
                return None
            if now_t - last_diag >= 15.0:
                ctx.log(f"等本场打完…（已 {now_t - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now_t
            self._interruptible_sleep(ctx, self._jitter(0.25, ctx))   # 0.25s 轮询：结算界面一闪而过也要抓得住
        return None

    def _dry_run_selfcheck(self, ctx, assignments, regions, threshold):
        cap_keys = [(k, label) for (k, label, _d, *_x) in DUNGEON_CALIBRATION["templates"]]
        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        while not ctx.should_stop():
            for wctx, role in assignments:
                if ctx.should_stop():
                    break
                if wctx.window.rect() is None:
                    continue
                if multi:
                    wctx.window.activate()
                scene = self._grab_scene(wctx, regions)
                found = []
                if role == TeamFormation.ROLE_CAPTAIN:
                    for key, label in cap_keys:
                        tpl = self.flags.get(key)
                        if tpl is None or scene is None:
                            continue
                        hit = vision.match(scene, tpl, threshold)
                        if hit is not None:
                            found.append(f"{label}({hit[2]:.2f})")
                if found:
                    wctx.log("识别到：" + "、".join(found), level="hit")
                else:
                    role_txt = "队长" if role == TeamFormation.ROLE_CAPTAIN else "队员"
                    wctx.log(f"[{role_txt}] 当前屏幕未识别到副本标志（请切到对应界面再看）。")
                if multi and len(assignments) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))

    def _focus(self, ctx):
        try:
            ctx.window.activate()
        except Exception:
            pass

    def _load_flags(self, tc, ctx):
        """读全部共享模板。绝大部分读 tasks.dungeon.templates；tuoying_title/upload 只读
        tasks.tuoying.templates（「工具」页「拓印」里标定，所有副本共用）——旧 tasks.shared / tasks.dungeon
        里残留的 tuoying 值一律不沿用，先从合并模板里摘干净再只填入新值。"""
        templates = dict(tc.get("templates", {}) or {})
        for k in ("tuoying_title", "tuoying_upload"):
            templates.pop(k, None)
        tasks = (ctx.cfg or {}).get("tasks", {}) or {}
        tuo_t = ((tasks.get("tuoying", {}) or {}).get("templates") or {})
        for k, v in tuo_t.items():
            if v:
                templates[k] = v
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None for k in SHARED_TPL_KEYS}

    def _scene_rect(self, ctx, regions):
        region = regions.get("scene")
        return ctx.window.region_to_screen_rect(region) if region else ctx.window.rect()

    def _grab_scene(self, ctx, regions):
        rect = self._scene_rect(ctx, regions)
        return win_mod.grab(rect) if rect else None

    def _match_scene(self, cur, scene_rect, flag_key, threshold):
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        join_tpl = self.flags.get("join")
        entry_tpl = self.flags.get(self._card_key())
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
        ex, ey = entry_screen_xy
        row_h = entry_tpl.shape[0] if entry_tpl is not None else 40
        band = max(40, int(row_h * 2))
        sh, sw = scene.shape[:2]
        ey_local = int(ey - ry)
        ex_local = int(ex - rx)
        cols = max(1, int(loop.get("activity_columns", 2)))
        col_w = sw / cols
        col_idx = min(cols - 1, max(0, int(ex_local // col_w)))
        col_right = int(round((col_idx + 1) * col_w))
        y0 = max(0, ey_local - band // 2)
        y1 = min(sh, ey_local + band // 2)
        x0 = max(0, ex_local)
        x1 = min(sw, col_right)
        if y1 - y0 < 1 or x1 - x0 < 1:
            return None
        crop = scene[y0:y1, x0:x1]
        m = vision.match(crop, join_tpl, threshold)
        if m is None:
            return None
        cx, cy, score = m
        return (rx + x0 + cx, ry + y0 + cy, score)
