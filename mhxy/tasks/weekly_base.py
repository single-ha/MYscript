# -*- coding: utf-8 -*-
"""
周常任务基类（门派闯关 / 海底世界 / 迷魂塔）。

三个周常的流程骨架相同（user 2026-09-22 拍板），分两个阶段：
  领任务  打开活动列表 → 找到活动卡片 → 点「参加」→ 角色自动寻路到活动 NPC → 点「参加活动」
          三个任务的差异只在最后这个 NPC 对话框按钮（图样文案不同）→ 各自在命名空间里标
          定自己的 confirm 模板即可，代码零差异。
  做任务  参考进副本后的逻辑：轮询「战斗中标识(battle_flag) / 进入战斗 / 任务栏小闹钟(clock)」：
          · 战斗中标识在 → 战斗中，等
          · 进入战斗在     → 点它发起本场
          · 小闹钟在       → 点它寻路到当前目标（带冷却，防反复重寻）
          · 三样都没 → 累计，连续 clean_need_sec(默认12s) 都检测不到 = 本轮完成

多人与循环（user 拍板）：
  · 三个周常都是多人任务：开跑前先自动组队（复用 tasks.teaming，勾了「已组队」跳过），
    组好后【只驱动队长窗口】，队员由游戏带着进。
  · 做任务判完成 → 自动再开新一轮（回主界面 → 领任务 → 做任务），直到
    中止条件 或 时间上限 time_limit_min（默认60，页面可填）或 用户停止。
  · 中止条件（立即停整）：画面既没有任务迹象，又活动列表里找不到活动卡片、
    或找到卡片但没找到「参加」按钮（含：整段列表翻完无卡片 / 认出卡片连确认多次没「参加」/
    NPC「参加活动」超时 / 点完确认后进不去任务场景）——此时活动不可再开 = 周常自然收尾。

★ 本任务复用 base.Task 的 _find_join_ready/_find_join_in_column（只在该卡片所在列内找
「参加」、不点右邻卡片）与 _save_capture/_jitter/_interruptible_sleep 等工具。

子类只写：name / title / description。其余完全通用（标定 spec 见本类 CALIBRATION）。
"""

import time

from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES)
from ..ui import ui_state
from .base import Task, register   # noqa: F401  (register 供子类用，语义上子类 @register)

# 每个号/每轮的模板键（card/join/confirm/enter 在任务自身命名空间标定；
# battle_flag / clock 在「通用」页公共标定，task_config 叠加进本任务 templates）。
_FLAG_KEYS = ["card", "join", "confirm", "enter", "battle_flag", "clock"]


class WeeklyBaseTask(Task):
    name = "weekly_base"
    title = "周常"
    description = "周常基类"
    # 多人任务：先自动组队，再由队长窗口跑两阶段循环（不做逐号轮转）

    CALIBRATION = {
        "regions": [
            ("scene", "主识别区", "留空=整个窗口当识别区（推荐）；战斗/按钮/小闹钟都在这里找", True),
            # activity_list 已在「通用」页「标定（公共区域）」统一标定（全任务共用），见 tasks.shared
        ],
        "templates": [
            ("card", "活动卡片", "活动列表里本任务那张卡片，框「图标+文字」、要独特（别的活动长得像）"),
            ("join", "参加按钮", "该卡片右侧的「参加」按钮，框按钮本身、要独特"),
            ("confirm", "NPC「参加活动」按钮", "自动寻路到活动 NPC 后对话框里要点的「参加活动」按钮"
                                              "（每个活动的图样文案不同）"),
            ("enter", "进入战斗按钮", "做任务场景里点它发起战斗的「进入战斗」按钮"),
            # battle_flag / clock 已移到「通用」页「标定（公共区域）」，见 tasks.shared
        ],
        "watchlist": False,
    }

    def __init__(self):
        self.flags = None

    # ==================================================================
    # preflight：自身模板 + 共享（活动区/battle_flag/clock）+ 组队资产
    # ==================================================================
    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(self.name)
        targets = ctx.cfg.get("targets", {})
        wins = ctx.select_windows()
        team_tc = ctx.task_cfg("teaming")
        skip_team = team_tc.get("skip_team", False)

        if skip_team:
            if not wins:
                problems.append("没找到/没选中目标窗口 —— 请先「选择窗口」选好队长所在的号")
        else:
            if not targets.get("multi"):
                problems.append(f"{self.title} 是多人任务：请在「选择窗口」切到多开并选好队长+队员（≥2 个号）")
            if len(wins) < 2:
                problems.append(f"{self.title} 需组队（队长+队员 ≥2 人），当前选中 {len(wins)} 个号")

        cap = team_tc.get("captain_index", 0)
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
            for hk in ("open_team", "open_friend"):
                if not ctx.hotkeys.get(hk):
                    problems.append(f"缺快捷键 {hk} —— 组队要用（请在设置里填）")

        regions = tc.get("regions", {})
        if not regions.get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请到「通用」页点「标定（公共区域）」框选（所有任务共用）")
        templates = tc.get("templates", {})
        for tk in ("card", "join", "confirm", "enter"):
            p = templates.get(tk)
            if not p or vision.load_template(p) is None:
                problems.append(f"模板『{tk}』缺失或加载失败 —— 请在标定向导里框选裁图")
        for sk in ("battle_flag", "clock"):
            p = templates.get(sk)
            if not p or vision.load_template(p) is None:
                label = "战斗界面标志" if sk == "battle_flag" else "小闹钟(寻路)"
                problems.append(f"『{label}』未标定 —— 请到「通用」页点「标定（公共区域）」框选（全任务共用）")

        if not ctx.hotkeys.get("open_activity"):
            problems.append("缺快捷键 open_activity（如 alt+c）—— 请在设置里填")

        sizes = {tuple(w.rect()[2:4]) for w in wins if w.rect()}
        if len(sizes) > 1:
            ctx.log("提示：所选号尺寸不一致，多开共用标定可能点偏，建议统一分辨率。", level="warn")

        return (len(problems) == 0), problems

    # ==================================================================
    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        team_tc = ctx.task_cfg("teaming")
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        skip_team = team_tc.get("skip_team", False)
        cap = team_tc.get("captain_index", 0)
        self.flags = self._load_flags(tc)

        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        if not skip_team and len(wins) < 2:
            ctx.log("选中窗口不足 2 个（多人任务需队长+队员）；若已自行组好队，请勾选「已组队」。",
                    level="error")
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

        # —— 组队（已组队则跳过），组好后只驱动队长窗口 ——
        cap_no = self._window_no(wins[cap], cap)
        if skip_team:
            ctx.log(f"★ {self.title}：已组队，跳过组队，直接由队长（号{cap_no}）跑周常循环 ★", level="warn")
        else:
            ctx.log(f"★ {self.title}：先组队（队长=号{cap_no}，队员 {len(wins) - 1} 人），"
                    "再由队长跑周常循环（队员由游戏带着进）★", level="warn")
            team = TeamFormation(ctx, assignments, team_tc, dry_run=False)
            ok, reason = team.run_until_formed()
            if ctx.should_stop():
                return
            if not ok:
                ctx.log(f"组队未完成（{reason}），{self.title} 中止。", level="error")
                return
            ctx.log("组队完成，队长开始周常流程…", level="hit")

        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        rounds = self._drive(cap_child, loop, regions, threshold)
        ctx.log(f"★ {self.title} 结束：队长完成 {rounds} 轮。★", level="hit")

    # ==================================================================
    # 队长驱动循环：领任务(可跳) → 做任务 → 判完成 → 再开一轮，直到 中止/时间上限/停止
    # ==================================================================
    def _drive(self, ctx, loop, regions, threshold):
        timeout_min = loop.get("time_limit_min", 60) or 0
        start_ts = time.time()
        clean_need = float(loop.get("clean_need_sec", 12))
        poll = max(0.2, float(loop.get("poll_sec", 1.0)))
        clock_cooldown = float(loop.get("step_to", 30))

        rounds = 0            # 已完成的轮数
        fights = 0            # 本轮已点「进入战斗」的场数（仅日志诊断）
        accepted = False      # 已进入任务场景（本回合）；False=要先领任务
        clean_since = None    # 做任务阶段三样轮询标识全无的累计起点
        clock_cool_until = 0.0
        enter_cool_until = 0.0
        last_diag = 0.0

        while not ctx.should_stop():
            now = time.time()
            if timeout_min > 0 and now - start_ts >= timeout_min * 60:
                ctx.log(f"已达时间上限 {timeout_min} 分钟，停止（已完成 {rounds} 轮）。", level="warn")
                break

            rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(rect) if rect else None
            in_task = self._any_signal(ctx, cur, rect, threshold)

            if not accepted:
                if in_task:
                    ctx.log("画面已有任务迹象（战斗/进入战斗/小闹钟），跳过领任务直接做任务。",
                            level="hit")
                    accepted = True
                    clean_since = None
                    continue
                # —— 领任务（阻塞单元）；失败 = 中止，整个任务收工 ——
                if not self._do_accept(ctx, loop, regions, threshold):
                    return rounds
                rounds += 1
                accepted = True
                clean_since = None
                continue

            # —— 做任务：单帧推进 ——
            if cur is not None:
                if ui_state.is_present(cur, self.flags, "battle_flag", threshold):
                    # 战斗中：只等，不打断
                    clean_since = None
                    clock_cool_until = 0.0
                    enter_cool_until = 0.0
                else:
                    hit = self._match_scene(cur, rect, "enter", threshold)
                    if hit is not None and now >= enter_cool_until:
                        ctx.mouse.click(hit[0], hit[1])
                        fights += 1
                        ctx.log(f"第 {rounds} 轮第 {fights} 场：点「进入战斗」（{hit[2]:.3f}）。",
                                level="hit")
                        clean_since = None
                        enter_cool_until = now + 2.0
                        clock_cool_until = 0.0
                        self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                        continue
                    hit = self._match_scene(cur, rect, "clock", threshold)
                    if hit is not None and now >= clock_cool_until:
                        ctx.mouse.click(hit[0], hit[1])
                        ctx.log(f"点「小闹钟」寻路（{hit[2]:.3f}）。", level="hit")
                        clean_since = None
                        clock_cool_until = now + clock_cooldown
                        enter_cool_until = 0.0
                        continue
                    # 三样轮询标识全无：累计，满 clean_need_sec = 本轮完成 → 开新一轮
                    if clean_since is None:
                        clean_since = now
                    if now - clean_since >= clean_need:
                        ctx.log(f"战斗标识/进入战斗/小闹钟已消失 {clean_need:.0f}s，"
                                f"判定第 {rounds} 轮完成，开新一轮。", level="hit")
                        self._back_to_main(ctx)
                        accepted = False
                        clean_since = None
                        continue

            # 空闲节流：报进度（区分「等战斗打」与「等三样全无判完成」）
            if now - last_diag >= 15.0:
                if accepted and clean_since is not None:
                    ctx.log(f"等本期结束…（任务迹象已消失 {now - clean_since:.0f}/{clean_need:.0f}s，"
                            f"满 {clean_need:.0f}s 判完成再开新一轮）")
                else:
                    ctx.log(f"做任务推进中…（已打 {fights} 场，完成 {rounds} 轮）")
                last_diag = now
            self._interruptible_sleep(ctx, self._jitter(poll, ctx))
        return rounds

    # ==================================================================
    # 领任务（阻塞单元）：开活动 → 找卡片 → 点参加 → 等NPC → 点参加活动 → 等任务迹象
    # 任何一步失败返回 False（中止已内部日志+截图，由调用方收工）。
    # ==================================================================
    def _do_accept(self, ctx, loop, regions, threshold):
        self._focus(ctx)
        if not ctx.send_hotkey("open_activity"):
            ctx.log("打不开活动界面（open_activity 快捷键未配置），中止。", level="error")
            self._abort_capture(ctx, regions)
            return False
        ctx.log("已打开活动，翻找活动卡片…")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))

        list_region = regions.get("activity_list")
        rec = {"_join_warned": False, "_nudges": 0, "_low_warned": False}
        r = self._find_card_once(ctx, rec, list_region, loop, regions, threshold)
        if ctx.should_stop():
            return False
        if r == "stopped":
            return False
        if r != "join_clicked":
            reason = "活动卡片未找到（列表整段翻完）" if r == "abort_no_card" else "认出卡片但没找到「参加」按钮"
            ctx.log(f"领任务中止：{reason}（活动不可参加/已参加）。（已截图存证）", level="error")
            self._abort_capture(ctx, regions)
            return False

        # 点完「参加」→ 角色自动寻路到 NPC → 等对话框出「参加活动」按钮（每个活动模板不同）
        npc_to = loop.get("npc_dialog_sec", 60)
        if not self._click_when(ctx, "confirm", "参加活动", regions, threshold, npc_to):
            ctx.log("等 NPC 对话框「参加活动」按钮超时，中止。", level="error")
            self._abort_capture(ctx, regions)
            return False
        ctx.log("已点「参加活动」，等进入任务场景…")
        enter_wait = loop.get("enter_wait_sec", 30)
        if not self._wait_task_enter(ctx, loop, regions, threshold, enter_wait):
            ctx.log(f"{enter_wait:.0f}s 内没等到任务迹象（战斗/进入战斗/小闹钟），中止。", level="error")
            self._abort_capture(ctx, regions)
            return False
        return True

    # ---- 找卡片 → 点「参加」：返回 "join_clicked" / "abort_no_card" / "abort_no_join" / "stopped" ----
    def _find_card_once(self, ctx, rec, list_region, loop, regions, threshold):
        tpl = self.flags.get("card")
        card_cut = float(loop.get("card_match_threshold", 0.0))
        card_cut = card_cut if card_cut > 0.0 else threshold

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())

        def probe(scene, rect):
            hit = vision.match(scene, tpl, card_cut) if scene is not None else None
            if hit is None:
                rec["_join_warned"] = False
                rec["_nudges"] = 0
                return scan.SCROLL, None
            entry_xy = (rect[0] + hit[0], rect[1] + hit[1])
            # 认出卡片后连确认几次「参加」（卡片贴列表边缘被裁/按钮错位时 _find_join_ready 自动微滚补全）
            for _ in range(max(1, int(loop.get("join_confirm_tries", 3)))):
                r = self._find_join_ready(ctx, rec, list_region, entry_xy, threshold, loop, entry_tpl=tpl)
                if r is not None and r != "nudged":
                    ctx.mouse.click(r[0], r[1])
                    ctx.log(f"找到活动卡片（{hit[2]:.3f}）→ 点「参加」（{r[2]:.3f}），开始寻路到 NPC。",
                            level="hit")
                    self._interruptible_sleep(ctx, self._jitter(0.5, ctx))
                    return scan.ACCEPT, {"join": True}
                if r == "nudged":
                    return scan.STAY, None
                self._interruptible_sleep(ctx, self._jitter(0.15, ctx))
            ctx.log("认出活动卡片但连确认多次都没找到其「参加」按钮 → 判不可参加，中止。", level="warn")
            return scan.ACCEPT, {"abort_no_join": True}

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
        if res.stopped:
            return "stopped"
        if res.found:
            return "join_clicked" if res.payload and res.payload.get("join") else "abort_no_join"
        ctx.log("活动列表翻找活动卡片多次未果。", level="warn")
        return "abort_no_card"

    # ---- 等「任务迹象」出现（进任务场景）：timeout 内任一迹象即算进了 ----
    def _wait_task_enter(self, ctx, loop, regions, threshold, timeout):
        deadline = time.time() + timeout
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
            cur = win_mod.grab(scene_rect) if scene_rect else None
            if self._any_signal(ctx, cur, scene_rect, threshold):
                ctx.log("任务迹象已出现，进入做任务阶段。", level="hit")
                return True
            if time.time() > deadline:
                return False
            self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        return False

    # ==================================================================
    # 做任务阶段的「任务迹象」判定：战斗标识 / 进入战斗 / 小闹钟 任一在
    # ==================================================================
    def _any_signal(self, ctx, cur, rect, threshold):
        if cur is None:
            return False
        if self.flags.get("battle_flag") is not None \
                and ui_state.is_present(cur, self.flags, "battle_flag", threshold):
            return True
        if self._match_scene(cur, rect, "enter", threshold) is not None:
            return True
        if self._match_scene(cur, rect, "clock", threshold) is not None:
            return True
        return False

    # ==================================================================
    # 工具
    # ==================================================================
    def _back_to_main(self, ctx):
        """开新一轮前先把窗口带回主界面（ESC 逐层关面板）；失败打日志不拦流程。"""
        self._focus(ctx)
        try:
            st = ui_state.back_to_main_screen(ctx.cfg, ctx.window)
        except Exception as e:
            ctx.log(f"回主界面异常（已忽略，仍尝试开活动）：{e}", level="warn")
            return
        if st is True:
            ctx.log("已回到主界面，再开新一轮。")
        elif st is None:
            ctx.log("无法判断主界面（大概率没标定「商城图标」），照旧开新一轮。", level="warn")
        else:
            ctx.log("回主界面兜底未成功（面板没关掉或商城图标没认到），仍尝试开活动。", level="warn")

    def _abort_capture(self, ctx, regions):
        """中止存证截图（状态下发前抓一张现场图，方便核对该号卡在哪）。"""
        try:
            self._save_capture(self._grab_scene(ctx, regions), "weekly_abort")
        except Exception:
            pass

    def _load_flags(self, tc):
        templates = tc.get("templates", {})
        return {k: vision.load_template(templates.get(k)) if templates.get(k) else None
                for k in _FLAG_KEYS}

    def _find_join_on_row(self, ctx, list_region, entry_screen_xy, threshold, loop):
        """统一实现见 base.Task._find_join_in_column（整列枚举取离卡片行最近那枚「参加」）。
        周常卡在此传 max_follow_cap：认出的卡片那行若没有匹配上的「参加」，宁可微滚重找，
        也绝不把隔壁行/邻卡按钮（曾 d=65 进错活动）当目标。"""
        return self._find_join_in_column(ctx, list_region, entry_screen_xy, threshold, loop,
                                         self.flags.get("join"),
                                         self.flags.get("card"),
                                         max_follow_cap=loop.get("join_same_row_px", 55))

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

    def _click_when(self, ctx, flag_key, label, regions, threshold, timeout):
        deadline = time.time() + timeout
        last_diag = 0.0
        scene_rect = self._scene_rect(ctx, regions)
        while not ctx.should_stop():
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

    def _focus(self, ctx):
        try:
            ctx.window.activate()
        except Exception:
            pass