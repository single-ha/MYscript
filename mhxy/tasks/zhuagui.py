# -*- coding: utf-8 -*-
"""
抓鬼任务（多人·组队后可选择是否自动组队，队长跑 N 轮抓鬼循环）。

用户口述的真实流程（队长视角，每轮跑一遍）：
  开活动 → 点「抓鬼」卡片右侧「参加」→ 角色自动寻路到 NPC
  → 等「领取抓鬼任务」出现并点击（点完【可能弹提醒弹窗】→点「取消」关掉再继续）
  → 点任务条目标签（自动寻路到鬼的位置）→ 领完自动进战斗 → 自动战斗打完
  → 战斗结束弹「是否继续」→ 点「继续」（自动寻路到任务 NPC）
  → 再等「领取抓鬼任务」→ 点它 → 点任务条目 → 战斗 …… 循环
  → 直到 领满 loop.max_rounds 轮（轮回数=领任务次数，默认 2）→ 结束。

组队约束（与蹈海去同构，用户要求「可选择是否自动组队」）：
  skip_team=False（默认）→ 先复用 core.teaming.TeamFormation 组队，再由队长跑循环；
  skip_team=True（已组队）→ 跳过组队、直接由队长跑（此时只需队长那个号能定位）。

队伍里只有队长操作：队员被传送 + 自动战斗，全程不点。故组队成功后是【队长单角色线性循环】，
不需要多开轮转（轮转是给「各号各跑同一状态机」的任务用的）。

任务始终实跑（无演练模式）：组队+抓鬼流程真打；组队可用「已组队」跳过。
"""

import time

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES)
from .base import Task, register

# 抓鬼自身模板键（gg_ 前缀=抓鬼，存盘 templates/tm_gg_*.png，避免与别的任务同名互相覆盖）。
_FLAG_KEYS = ["gg_entry", "gg_join", "gg_claim", "gg_nav", "gg_next", "gg_cancel"]

# 完整循环是没有「进入战斗」按钮的：领完自动寻路到鬼就【自己进战斗】，打完弹「领下一轮」。


@register
class ZhuaguiTask(Task):
    name = "zhuagui"
    title = "抓鬼"
    description = "组队(可跳过)后由队长跑抓鬼循环：参加→领取→点任务条目寻路→自动战斗→点领下一轮，跑满设定轮数即停"
    CHAINS_PER_WINDOW = False

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区(推荐)；对话框/各按钮都在这里找", True),
            # activity_list 已在「通用」页「标定（公共区域）」统一标定（全任务共用），见 tasks.shared
        ],
        "templates": [
            ("gg_entry", "活动卡片入口", "活动列表里「抓鬼」那张卡片，框图标+文字、要独特"),
            ("gg_join", "参加按钮", "抓鬼卡片右侧的「参加」按钮，框按钮本身、要独特"),
            ("gg_claim", "领取抓鬼任务按钮", "自动寻路到任务 NPC 后对话框里那个「领取抓鬼任务」按钮；每轮都要点它领当前轮任务（第 1 轮与点「继续」寻路回 NPC 后都点）"),
            ("gg_nav", "任务条目标签(点它寻路)", "领取后屏幕边缘/任务栏当前抓鬼任务那个条目，点它触发自动寻路到鬼的位置"),
            ("gg_next", "继续按钮（是否继续弹窗）", "这场战斗打完弹出「是否继续」弹窗，点里面的「继续」自动寻路到任务 NPC 领下一轮（最后一轮不点）"),
            ("gg_cancel", "提醒弹窗取消按钮", "点「领取抓鬼任务」后可能弹出的提醒弹窗内的「取消」按钮；"
                                                "没标=弹窗时不处理（可能挡着下一步点不到任务条目）", True),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        team_tc = ctx.task_cfg("teaming")   # 组队设置统一读共享 tasks.teaming
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        skip_team = team_tc.get("skip_team", False)   # 「已组队」=跳过组队，直接由队长抓鬼

        if skip_team:
            if not wins:
                problems.append("没找到/没选中目标窗口 —— 请先在侧栏「选择窗口」选好队长所在的号")
        else:
            if not targets.get("multi"):
                problems.append("抓鬼需先组队：请在「选择窗口」切到多开并选好队长+队员（≥2 个号）")
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

        # 跑完解散已迁至「日常一条龙」页集中控制（tasks.teaming.auto_disband），抓鬼自身不再解散。

        regions = tc.get("regions", {})
        if not regions.get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请到「通用」页点「标定（公共区域）」框选（所有任务共用）")
        templates = tc.get("templates", {})
        for tk in ["gg_entry", "gg_join", "gg_claim", "gg_nav", "gg_next"]:
            p = templates.get(tk)
            if not p or vision.load_template(p) is None:
                problems.append(f"抓鬼模板『{tk}』缺失或加载失败 —— 请在本页「标定」里框选裁图")

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

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        team_tc = ctx.task_cfg("teaming")   # 组队设置统一读共享 tasks.teaming
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        dry_run = False
        skip_team = team_tc.get("skip_team", False)
        cap = team_tc.get("captain_index", 0)
        self.flags = self._load_flags(tc)
        self.max_rounds = max(1, int(loop.get("max_rounds", 2)))

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
            ctx.log("演练模式：不组队/不发快捷键/不点，只对各号识别组队+抓鬼模板做自检。", level="warn")
            self._dry_run_selfcheck(ctx, assignments, regions, threshold)
            return

        # —— 第一步：组队（勾了「已组队」则跳过）——
        cap_no = self._window_no(wins[cap], cap)
        if skip_team:
            ctx.log(f"★ 抓鬼：已组队，跳过组队，直接由队长（号{cap_no}）跑 {self.max_rounds} 轮 ★", level="warn")
        else:
            ctx.log(f"★ 抓鬼：先组队（队长=号{cap_no}，队员 {len(wins) - 1} 人），"
                    f"再由队长跑 {self.max_rounds} 轮 ★", level="warn")
            team_cfg = ctx.task_cfg("teaming")
            team = TeamFormation(ctx, assignments, team_cfg, dry_run=False)
            ok, reason = team.run_until_formed()
            if ctx.should_stop():
                return
            if not ok:
                ctx.log(f"组队未完成（{reason}），抓鬼中止。", level="error")
                return
            ctx.log("组队完成，队长开始抓鬼循环…", level="hit")

        # —— 第二步：队长跑抓鬼循环（N 轮）——
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        self._run_rounds(cap_child, loop, regions, threshold)
        # 跑完解散已迁至「日常一条龙」页集中控制（见 daily._disband_after_multi），抓鬼跑完不再自动解散。

    # ------------------------------------------------------------------
    # 队长抓鬼循环（线性、阻塞式；每轮= 领任务 → 战斗 → 领下一轮/收尾）
    # ------------------------------------------------------------------
    def _run_rounds(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        npc_to = loop.get("npc_dialog_sec", 60)     # 等「领取抓鬼任务」对话框（寻路到任务 NPC 耗时）
        step_to = loop.get("step_timeout_sec", 30)  # 通用按钮超时
        battle_to = loop.get("battle_timeout_sec", 600)  # 一场战斗超时

        # 前缀：开活动 → 参加 → 角色寻路到任务 NPC（第 1 轮入口）
        if not self._open_and_join(ctx, loop, regions, threshold):
            return

        need_continue = False   # 第 1 轮直接领任务；打完第 1 轮后，每轮开头先点「继续」寻路回任务 NPC
        for round_no in range(1, self.max_rounds + 1):
            if ctx.should_stop():
                return

            if need_continue:
                # 上一轮战斗打完 → 弹「是否继续」→ 点「继续」自动寻路到任务 NPC
                if not self._click_when(ctx, "gg_next", "继续（是否继续）", regions, threshold, step_to):
                    ctx.log(f"等「继续」超时（第 {round_no} 轮开头弹窗），中止。", level="error")
                    return

            # 寻路到任务 NPC 后：等「领取抓鬼任务」出现并点它；
            # 点完【可能弹提醒弹窗】（概率出现）→ 先点「取消」关掉再继续，否则挡着点不到任务条目。
            if not self._click_when_dismissing(ctx, "gg_claim", "领取抓鬼任务",
                                               "gg_cancel", "提醒弹窗取消",
                                               regions, threshold, npc_to):
                ctx.log(f"等「领取抓鬼任务」超时（第 {round_no} 轮），中止。", level="error")
                return
            # 再点任务条目标签寻路到鬼的位置（弹窗若仍在/再弹，先点「取消」）；
            # 条目刚冒出时首击会被游戏当聚焦吞掉，需连点两次（nav_double_gap_sec=两次的间隔）
            if not self._click_when_dismissing(ctx, "gg_nav", "任务条目(寻路)",
                                               "gg_cancel", "提醒弹窗取消",
                                               regions, threshold, step_to,
                                               double_gap_sec=loop.get("nav_double_gap_sec", 0.3)):
                ctx.log(f"等「任务条目」超时（第 {round_no} 轮），中止。", level="error")
                return

            # 抓鬼没有「进入战斗」按钮：寻路到鬼自动进战斗 → 直接等打完
            if not self._wait_round_end(ctx, loop, regions, threshold, battle_to, round_no):
                ctx.log(f"第 {round_no} 轮战斗异常（超时未等到结束），中止。", level="error")
                return
            ctx.log(f"★ 第 {round_no}/{self.max_rounds} 轮抓鬼完成。★", level="hit")
            need_continue = True

        ctx.log(f"已跑满 {self.max_rounds} 轮抓鬼，流程结束。", level="hit")

    # ---- 等一场战斗打完：出现「是否继续」弹窗即认为打完（没有别的结束标志）----
    def _wait_round_end(self, ctx, loop, regions, threshold, timeout, round_no):
        deadline = time.time() + timeout
        last_diag = 0.0
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None

            nxt = self._match_scene(cur, scene_rect, "gg_next", threshold)
            if nxt is not None:
                return True   # 出现「是否继续」弹窗即本轮战斗已打完（调用方接着点「继续」寻路回 NPC）

            now = time.time()
            if now > deadline:
                return False
            if now - last_diag >= 15.0:
                ctx.log(f"等第 {round_no} 轮战斗打完…（已 {now - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        return False

    # ---- 开活动 → 找抓鬼卡片 → 点「参加」----
    def _open_and_join(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 未配置），中止。", level="error")
            return False
        ctx.log("已打开活动，翻找抓鬼卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        list_region = regions.get("activity_list")
        # 找「参加」的微滚计数/告警标志（抓鬼本流程一次性发起、不复用轮转 record，局部状态即可）
        rec = {"_join_warned": False, "_nudges": 0}

        def grab_rect():
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            return rect

        def probe(scene, rect):
            hit = vision.match(scene, self.flags.get("gg_entry"), threshold) if scene is not None else None
            if hit is None:
                rec["_join_warned"] = False
                rec["_nudges"] = 0
                return scan.SCROLL, None
            entry_xy = (rect[0] + hit[0], rect[1] + hit[1])
            r = self._find_join_ready(ctx, rec, list_region, entry_xy, threshold, loop,
                                      entry_tpl=self.flags.get("gg_entry"))
            if r is not None and r != "nudged":
                ctx.mouse.click(r[0], r[1])
                ctx.log(f"找到抓鬼卡片（{hit[2]:.3f}）→ 点「参加」（{r[2]:.3f}），等寻路到 NPC。", level="hit")
                return scan.ACCEPT, r
            if r != "nudged":
                if not rec["_join_warned"]:
                    rec["_join_warned"] = True
                    ctx.log("认出卡片但「参加」按钮一直没出现（已自动微滚补全；检查 gg_join 模板/阈值），原地重试。", level="warn")
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
        ctx.log("翻找抓鬼卡片多次未果，中止。", level="error")
        return False

    # ------------------------------------------------------------------
    # 通用：轮询等某模板出现就点它（命中返回 True；超时返回 False）。全程勤查 should_stop。
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

    # ---- 等某按钮出现并点它；期间若「提醒弹窗」出现则先点「取消」关掉再继续 ----
    def _click_when_dismissing(self, ctx, target_key, target_label, cancel_key, cancel_label,
                               regions, threshold, timeout, double_gap_sec=0.0):
        """在同一个轮询里等 target_key 出现就点它；期间若 cancel_key（提醒弹窗的「取消」按钮）出现，
        先点「取消」关掉弹窗再继续等目标。弹窗是【概率出现】（用户报告：点「领取抓鬼任务」后可能弹提醒），
        不弹=零额外等待。命中返回 True；超时返回 False。全程勤查 should_stop。
        double_gap_sec>0 时：命中后隔这么久再【补击一次】（条目标签刚冒出来，首击常被游戏当成
        「聚焦/选中」吞掉，经验证延迟等它再单点也无效，必须同点连点两次，见 loop.nav_double_gap_sec）。"""
        deadline = time.time() + timeout
        last_diag = 0.0
        warned_no_cancel = False
        while not ctx.should_stop():
            scene_rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(scene_rect) if scene_rect else None

            cancel_tpl = self.flags.get(cancel_key)
            if cancel_tpl is None and not warned_no_cancel:
                ctx.log(f"未标定「{cancel_label.replace('弹窗', '')}」(模板 {cancel_key})："
                        f"若弹出提醒弹窗会挡着点不到「{target_label}」——请在标定里补「{cancel_label}」模板",
                        level="warn")
                warned_no_cancel = True
            if cancel_tpl is not None and scene_rect is not None and cur is not None:
                m = vision.match(cur, cancel_tpl, threshold)
                if m is not None:
                    ctx.mouse.click(scene_rect[0] + m[0], scene_rect[1] + m[1])
                    ctx.log(f"{cancel_label}弹窗弹出 → 点「取消」关闭（{m[2]:.3f}）。", level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                    continue

            hit = self._match_scene(cur, scene_rect, target_key, threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「{target_label}」（{hit[2]:.3f}）。", level="hit")
                if double_gap_sec > 0:
                    self._interruptible_sleep(ctx, self._jitter(double_gap_sec, ctx))
                    ctx.mouse.click(hit[0], hit[1])
                    ctx.log(f"补点「{target_label}」（防首击被吞，间隔 {double_gap_sec:.1f}s）。", level="hit")
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                return True

            now = time.time()
            if now > deadline:
                return False
            if now - last_diag >= 15.0:
                ctx.log(f"等「{target_label}」…（已 {now - (deadline - timeout):.0f}/{timeout:.0f}s）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
        return False

    # ------------------------------------------------------------------
    # 演练：周期性对每个号识别其相关模板
    # ------------------------------------------------------------------
    def _dry_run_selfcheck(self, ctx, assignments, regions, threshold):
        cap_keys = [(k, k) for k in _FLAG_KEYS]
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
                    wctx.log(f"[{role_txt}] 当前屏幕未识别到抓鬼标志（请切到对应界面再看）。")
                if multi and len(assignments) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(1.5, ctx))

    # ------------------------------------------------------------------
    # 识别/点击工具（本任务自带一份）
    # ------------------------------------------------------------------
    def _focus(self, ctx):
        try:
            ctx.window.activate()
        except Exception:
            pass

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
        tpl = self.flags.get(flag_key)
        if cur is None or tpl is None or scene_rect is None:
            return None
        m = vision.match(cur, tpl, threshold)
        if m is None:
            return None
        return (scene_rect[0] + m[0], scene_rect[1] + m[1], m[2])

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """统一实现见 base.Task._find_join_in_column（整列枚举取距离卡片行最近那枚「参加」）。
        抓鬼的参加按钮模板为 gg_join；卡片为 gg_entry。"""
        return self._find_join_in_column(ctx, list_region, entry_screen_xy, threshold, loop,
                                         self.flags.get("gg_join"),
                                         self.flags.get("gg_entry"))