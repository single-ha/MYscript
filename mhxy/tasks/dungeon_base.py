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

副本内统一轮询（user 2026-09-21 拍板，取代「等上一场→点闹钟→点进入战斗」的三段式分步等待）：
  每帧按优先级检测 结算界面 → 战斗标识 → 跳过剧情 → 进入战斗 → 小闹钟：
  结算界面即判副本结束、立即进收尾；战斗标识=战斗中不做任何动作、等下次轮询；
  跳过剧情/小闹钟/进入战斗 出现即点（点「进入战斗」计一场，达 max_rounds 强制收尾防死循环）。
  保留两层兜底：阶段空闲超时（开局 entry_skip_sec / 寻路 step_timeout_sec / 战斗 battle_timeout_sec，
  超时=副本可能卡住或已结束，进收尾）与静止画面兜底（still_end_sec：结算界面一闪而过/副本结束后的
  静止场景也判结束，战斗中画面在动不会误判）。
收尾动作：判结束后，再点一下屏幕 + 点小闹钟。

配置/标定全存共享 tasks.dungeon（见 DUNGEON_CALIBRATION），各副本无独立配置。
"""

import time

from ..core import scan
from ..core import scribble
from ..core import vision
from ..core import window as win_mod
from ..core.teaming import (TeamFormation, TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES)
from .base import Task, register

from ..ui import ui_state


DUNGEON_NS = "dungeon"                       # 共享配置命名空间（tasks.dungeon）

# 共享模板键（绝大部分存 tasks.dungeon.templates；tuoying_title/upload 存 tasks.tuoying.templates，
# 在「工具」页「拓印」里标定，见 TUOYING_TPL_KEYS；clock 存 tasks.shared.templates（「通用」页公共标定，
# task_config 叠加注入本任务 templates）；battle_flag 同理存 shared。由 _load_flags 统一读入）。
# 顺序按操作流程走，并作为标定向导里模板画廊的展示顺序（clock/battle_flag 已不在画廊，被 shared 取代）：
#   卡片(普通/侠士) → 参加 → 选择副本 → 侠士区标签页(侠士进副本前) → 进入 → 侠士确认(侠士) → 跳过/进入战斗 → 结算界面。
SHARED_TPL_KEYS = ["entry_common", "entry_xiashi", "join", "select",
                   "xiashi_tab", "enter_dungeon", "confirm", "skip", "clock", "enter",
                   "settlement", "tuoying_title", "tuoying_upload", "battle_flag"]

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
        ("entry_xiashi", "侠士副本卡片", "活动列表里侠士副本的那张卡片——侠士副本共用；可选项：侠士本优先用" 
                                       "它找卡，找不到/没标定时用「普通副本卡」兜底（两者参加后寻路到同一 NPC）",
         True),
        ("join", "参加按钮", "卡片右侧的「参加」按钮（所有副本共用）"),
        ("select", "选择副本按钮", "「选择副本」对话框里的按钮（所有副本共用）"),
        ("xiashi_tab", "侠士区标签页", "「选择副本」里的「侠士区」标签页，侠士副本先进副本前先点到它（仅侠士用）"),
        ("enter_dungeon", "「进入」按钮", "选择副本对话框里的「进入」按钮（普通/侠士两标签区共用）"),
        ("confirm", "侠士「确认」按钮", "侠士进副本后各号弹的「确认」按钮（仅侠士用）"),
        ("skip", "跳过剧情按钮", "副本内每轮先点的「跳过剧情」按钮（共用）"),
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

        # 跑完解散已迁至「日常」页集中控制（tasks.teaming.auto_disband），副本自身不再解散。

        regions = tc.get("regions", {})
        if not regions.get("activity_list"):
            problems.append("『活动列表区域』未标定 —— 请到「通用」页点「标定（公共区域）」框选（所有任务共用）")
        templates = tc.get("templates", {})
        req_keys = [(k, label) for (k, label, _d, *_x) in DUNGEON_CALIBRATION["templates"]
                    if not (_x and _x[0])                                    # 第4元组=可选资产（拓印等），不参与就绪与 preflight
                    and not ((k in ("confirm", "xiashi_tab")) and self.cat != "xiashi")
                    and k != "entry_xiashi"]               # 侠士卡可选：侠士本兜底用普通卡（见 probe）
        for k, label in req_keys:
            p = templates.get(k)
            if not p or vision.load_template(p) is None:
                problems.append(f"副本共用模板『{label}』({k}) 缺失 —— 请在本页「标定」里框选裁图")
        # 小闹钟(寻路)已迁「通用」页「标定（公共区域）」（tasks.shared，task_config 叠加进本任务 templates）：
        # 副本内寻路 + 每轮收尾都点它，必标。
        clock_path = templates.get("clock")
        if not clock_path or vision.load_template(clock_path) is None:
            problems.append("『小闹钟(寻路)』未标定 —— 请到「通用」页点「标定（公共区域）」框选（副本/抓鬼共用）")
        if self.cat == "xiashi":
            if not (templates.get("entry_common") or templates.get("entry_xiashi")):
                problems.append("副本卡片模板缺失 —— 请至少标定『普通副本卡片』或『侠士副本卡片』之一")

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
    def _run(self, ctx):
        tc = ctx.task_cfg(DUNGEON_NS)
        team_tc = ctx.task_cfg("teaming")
        loop = tc["loop"]
        regions = tc["regions"]
        threshold = loop["match_threshold"]
        dry_run = False
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
        # 跑完解散已迁至「日常」页集中控制（见 _disband_after_multi），副本跑完不再自动解散。

    # ==================================================================
    # 副本流程（普通=蹈海去线性；侠士进副本后多一段确认轮询）
    # ==================================================================
    def _run_dungeon(self, ctx, assignments, loop, regions, threshold):
        self._focus(ctx)
        npc_to = loop.get("npc_dialog_sec", 60)
        step_to = loop.get("step_timeout_sec", 30)

        if not self._open_and_join(ctx, loop, regions, threshold):
            return
        if not self._click_when(ctx, "select", "选择副本", regions, threshold, npc_to):
            ctx.log("等「选择副本」对话框超时（角色可能还没寻路到 NPC），中止。", level="error")
            return

        # —— 进副本：点「进入」（多命中点定位）→ 偶发「拓印」临摹弹窗处理（队长窗）→
        #      侠士轮询各号确认 / 普通验证已进本；进+确认/进本 外套重试 ——
        if not self._enter_with_tuoying(ctx, assignments, loop, regions, threshold, step_to):
            return

        # —— 副本内统一轮询（user 2026-09-21 拍板）：按优先级逐帧检测
        #      结算界面 → 战斗标识 → 跳过剧情 → 进入战斗 → 小闹钟 ——
        if not self._run_rounds(ctx, loop, regions, threshold):
            return

        # —— 收尾：再点一下屏幕推进结算 + 点小闹钟 ——
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        self._click_scene_center(ctx, regions)
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        if self._click_when(ctx, "clock", "小闹钟(收尾)", regions, threshold, step_to):
            ctx.log(f"★ {self.title} 完成，已点小闹钟收尾。★", level="hit")
        else:
            ctx.log("没点到「小闹钟」（副本可能已自动结束）。流程结束。", level="warn")

    # ------------------------------------------------------------------
    # 副本内统一轮询：结算界面 → 战斗标识 → 跳过剧情 → 小闹钟 → 进入战斗
    # ------------------------------------------------------------------
    def _run_rounds(self, ctx, loop, regions, threshold):
        """副本内主循环（取代原「等上一场→点小闹钟→等进入战斗」的三段式分步等待）：
        每 poll_sec（loop.poll_sec，默认 1s，可配）按优先级统一轮询识别：
          · 结算界面  → 判副本结束，返回 True（调用方进收尾）；识别阈值比统一阈值放宽 settle_grace
          · 战斗标识  → 战斗中不做任何动作，等下次轮询（不重开阶段计时——战斗太久由 battle 超时兜底；
                        自动开战副本没机会点「进入战斗」，见其出现即切战斗阶段，让 battle_timeout_sec 生效）
          · 跳过剧情  → 点它（过剧情/上一场打完续战）
          · 进入战斗  → 点它发起本场（发起场次计数 +1，等待窗口结束）；检测先于小闹钟——寻路到位后
                        两者同帧并存时优先点它，不怕小闹钟抢戏
          · 小闹钟    → 点它（寻路到当前目标）；点后进「等待窗口」+ 冷却，冷却期内小闹钟再现不再重复点，
                        避免寻路动画期间被反复重寻路（真没寻到位由超时兜底点它重寻路）
        保留三层兜底：
          · 阶段空闲超时：开局 entry_skip_sec / 寻路等待 step_timeout_sec / 战斗 battle_timeout_sec，
            超时=副本可能卡住或已结束，返回 True 进收尾；其中寻路等待超时且「小闹钟」仍在画面时，
            先点它重新寻路再等（最多 enter_clock_retries 次，同旧语义）
          · 发起战斗场次达 max_rounds 上限 → 强制收尾防死循环
          · 静止画面兜底：画面连续静止 still_end_sec 秒（结算界面一闪而过/副本结束后的静止场景）也判结束
        返回：True=正常/兜底收尾；False=should_stop 中止。"""
        entry_to = float(loop.get("entry_skip_sec", 60))
        step_to = float(loop.get("step_timeout_sec", 30))
        battle_to = float(loop.get("battle_timeout_sec", 600))
        max_rounds = max(1, int(loop.get("max_rounds", 24)))
        still_sec = max(0, int(loop.get("still_end_sec", 20)))
        still_diff = float(loop.get("still_diff", 6.0))
        grace = max(0.0, min(0.25, float(loop.get("settle_grace", 0.05))))
        settle_th = threshold - grace
        clock_retry_max = max(0, int(loop.get("enter_clock_retries", 3)))
        poll_sec = max(0.2, float(loop.get("poll_sec", 1.0)))   # 轮询间隔：空闲/等战斗的空转节奏，可配，默认 1s
        stpl = self.flags.get("settlement")

        fights = 0                    # 已点「进入战斗」发起的场次（达 max_rounds 强制收尾）
        stage_at = time.time()        # 当前阶段起点（开局=刚进副本）
        stage_to = entry_to           # 当前阶段空闲超时
        awaiting_enter = False        # 已点过小闹钟、处于寻路「等进入战斗」等待窗口
        clock_cool_until = 0.0        # 小闹钟冷却截止：冷却期内小闹钟再次出现不再重复点
        clock_retry = clock_retry_max  # 寻路等待超时后点小闹钟重新寻路的剩余次数
        bl_warned = False             # battle_flag 未标定只告警一次
        last_diag = 0.0
        # 静止画面兜底状态（结算界面一闪而过/副本已结束的静止场景）
        self._end_prev = None
        self._end_still = 0.0
        self._end_prev_t = 0.0

        while not ctx.should_stop():
            if fights >= max_rounds:
                ctx.log(f"已发起 {fights} 场战斗，达最大场数上限({max_rounds})，进入收尾。", level="warn")
                return True
            rect = self._scene_rect(ctx, regions)
            cur = win_mod.grab(rect) if rect else None
            now = time.time()

            # ① 静止画面兜底：结算界面一闪而过/副本已结束时回到的静止场景也判结束（战斗中画面在动不触发）
            if cur is not None and still_sec > 0:
                if self._end_prev is not None:
                    dd = vision.frame_diff(self._end_prev, cur)
                    dt = now - self._end_prev_t
                    self._end_still = (self._end_still + dt) if dd < still_diff else 0.0
                    if self._end_still >= still_sec:
                        ctx.log(f"画面已静止 {still_sec:.0f}s（结算界面一闪而过/副本已结束），判定结束。",
                                level="hit")
                        return True
                self._end_prev = cur
                self._end_prev_t = now

            # ② 结算界面（优先级最高；识别到即判副本结束、进收尾）
            if cur is not None and stpl is not None:
                sm = vision.match(cur, stpl, settle_th)
                if sm is not None:
                    ctx.log(f"识别到结算界面（{sm[2]:.3f}，阈值{settle_th:.2f}），副本结束。", level="hit")
                    return True

            # ③ 战斗标识：战斗中不做任何动作，等下次轮询。
            #    未点过「进入战斗」就开战（自动开战副本）时切到战斗阶段，让 battle_timeout_sec 兜底；
            #    点进入战斗进入的分支保持原战斗计时（不重开，战斗太久才触发超时收尾）。
            if cur is not None and self.flags.get("battle_flag") is not None:
                if ui_state.is_present(cur, self.flags, "battle_flag", threshold):
                    if stage_to != battle_to:
                        stage_at = now
                        stage_to = battle_to
                        awaiting_enter = False
                    self._interruptible_sleep(ctx, self._jitter(poll_sec, ctx))
                    continue

            # ④ 跳过剧情：点它（过剧情/上一场打完续战），随后等待窗口重开（可点小闹钟寻路）
            hit = self._match_scene(cur, rect, "skip", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「跳过剧情」（{hit[2]:.3f}）。", level="hit")
                stage_at = now
                stage_to = step_to
                awaiting_enter = False
                clock_cool_until = 0.0
                clock_retry = clock_retry_max
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                continue

            # ⑤ 进入战斗：点它发起本场，进入战斗等待（等打完由战斗标识/跳过/结算承接）。
            #    检测先于小闹钟：寻路到位后「进入战斗」与「小闹钟」同帧并存时优先点它，不受小闹钟抢戏
            hit = self._match_scene(cur, rect, "enter", threshold)
            if hit is not None:
                ctx.mouse.click(hit[0], hit[1])
                fights += 1
                ctx.log(f"第 {fights} 场：点「进入战斗」（{hit[2]:.3f}），等它打完…", level="hit")
                stage_at = now
                stage_to = battle_to
                awaiting_enter = False
                clock_cool_until = 0.0
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                continue

            # ⑥ 小闹钟：点它寻路（不在冷却期才点，防寻路动画期间画面里图标未消失被反复重寻路；
            #    真没寻到位时由下方超时兜底点它重寻路）
            hit = self._match_scene(cur, rect, "clock", threshold)
            if hit is not None and now >= clock_cool_until and not awaiting_enter:
                ctx.mouse.click(hit[0], hit[1])
                ctx.log(f"点「小闹钟」寻路（{hit[2]:.3f}）。", level="hit")
                stage_at = now
                stage_to = step_to
                awaiting_enter = True
                clock_cool_until = now + step_to
                clock_retry = clock_retry_max
                self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                continue

            # —— 阶段空闲超时兜底：长时间既无动作也无状态变化 → 副本可能卡住或已结束，进收尾 ——
            if now < stage_at + stage_to:
                if now - last_diag >= 15.0:
                    ctx.log(f"等副本推进…（已空闲 {now - stage_at:.0f}/{stage_to:.0f}s）")
                    last_diag = now
                self._interruptible_sleep(ctx, self._jitter(poll_sec, ctx))
                continue

            if awaiting_enter:
                # 寻路等待超时：小闹钟仍在画面 → 点它重新寻路再等（最多 enter_clock_retries 次）；
                # 小闹钟没了 → 副本可能已结束；重试耗尽 → 无法推进。
                if clock_retry > 0:
                    ch = self._match_scene(cur, rect, "clock", threshold)
                    if ch is not None:
                        ctx.log(f"等「进入战斗」超时({stage_to:.0f}s)，小闹钟仍在，点它重新寻路（还剩 {clock_retry} 次）。",
                                level="warn")
                        ctx.mouse.click(ch[0], ch[1])
                        clock_retry -= 1
                        stage_at = now
                        stage_to = step_to
                        clock_cool_until = now + step_to
                        self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
                        continue
                if clock_retry <= 0:
                    ctx.log(f"等「进入战斗」超时({stage_to:.0f}s)，已重寻路多次小闹钟仍在，判定副本可能已结束。",
                            level="warn")
                else:
                    ctx.log(f"等「进入战斗」超时({stage_to:.0f}s)且小闹钟已不在，判定副本可能已结束。", level="warn")
                    if self.flags.get("battle_flag") is None and not bl_warned:
                        bl_warned = True
                        ctx.log("可能因战斗已开始而未现「进入战斗」按钮（自动开战副本）；若反复超时，请到 "
                                "「通用」页「标定（公共区域）」补标『战斗标识』。", level="warn")
                return True
            if self.flags.get("battle_flag") is None and not bl_warned:
                bl_warned = True
                ctx.log("等副本推进超时且未标定共享 battle_flag（自动开战副本用它判战斗是否已开始）。",
                        level="warn")
            ctx.log(f"等副本推进超时({stage_to:.0f}s)（副本可能已结束或战斗卡住），进入收尾。", level="warn")
            return True
        return False

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
        # 找「参加」的微滚计数/告警标志（开活动→参加一次性发起、不复用轮转 record，局部状态即可）
        rec = {"_join_warned": False, "_nudges": 0, "_low_warned": False, "_fb_warned": False}
        # 找出卡片的模板优先级：侠士本=侠士卡优先、普通卡兜底；普通本只用普通卡（见 probe）
        card_keys = [self._card_key()]
        if self.cat == "xiashi":
            card_keys.append("entry_common")
        # 找副本卡片专用的更严阈值：entry_common/entry_xiashi 若框到多卡公共UI，镜像卡常拿 0.9x 分、
        # 真卡(标定原帧)≈0.99。用专属阈值分开，宁可继续滚动也不误认别的活动卡（曾因 0.85 误认运镖）。
        card_cut = float(loop.get("card_match_threshold", 0.0))
        card_cut = card_cut if card_cut > 0.0 else threshold

        def grab_rect():
            rect = (ctx.window.region_to_screen_rect(list_region)
                    if list_region else ctx.window.rect())
            return rect

        def probe(scene, rect):
            # 卡片模板优先级：侠士本=侠士卡优先、普通卡兜底（两者「参加」后寻路到同一 NPC，进本后再按
            # 侠士区标签页+第 N 个「进入」选对副本）；普通本只用普通卡。逐级取第一枚 ≥ card_cut 的候选。
            seek = 0.55  # 低门槛捞本屏所有候选，区分「没卡在屏」与「有卡但分数不足」
            best_pick = None   # (key, tpl, sorted_cands)
            meta = None        # (最高分, key)：仅当全没达到 card_cut 时用于告警
            for k in card_keys:
                tpl = self.flags.get(k)
                if tpl is None:
                    continue
                cands = vision.match_multi(scene, tpl, seek) if scene is not None else []
                if not cands:
                    continue
                cands.sort(key=lambda h: h[2], reverse=True)
                if cands[0][2] >= card_cut:
                    best_pick = (k, tpl, cands)
                    break
                if meta is None or cands[0][2] > meta[0]:
                    meta = (cands[0][2], k)
            if best_pick is None:
                if meta is not None:
                    # 本屏有候选但不够专属（镜像卡）——宁可滚动别处找，也不认成副本卡
                    if not rec["_low_warned"]:
                        rec["_low_warned"] = True
                        ctx.log(
                            f"卡片候选分数不足（{meta[0]:.3f} < loop.card_match_threshold {card_cut:.2f}），"
                            "判定为镜像卡不敢认，继续滚动另找；如反复出现，请重新圈选副本卡上更独特的部分、"
                            "或调大该阈值。", level="warn")
                    return scan.SCROLL, None
                # 主卡没候选（侠士卡不在/没标定、普通卡也不在）——滚动
                rec["_join_warned"] = False
                rec["_nudges"] = 0
                rec["_low_warned"] = False
                return scan.SCROLL, None
            key, tpl, cands = best_pick
            if key != card_keys[0] and not rec["_fb_warned"]:
                rec["_fb_warned"] = True
                ctx.log("侠士卡未识别到，用「普通副本卡」兜底（两者参加后寻路到同一 NPC，进本会自动选侠士区对应副本）。",
                        level="warn")
            rec["_low_warned"] = False
            hit = cands[0]
            entry_xy = (rect[0] + hit[0], rect[1] + hit[1])
            r = self._find_join_ready(ctx, rec, list_region, entry_xy, threshold, loop,
                                      entry_tpl=tpl)
            if r is not None and r != "nudged":
                ctx.mouse.click(r[0], r[1])
                ctx.log(
                    f"找到副本卡片（{hit[2]:.3f}）→ 点「参加」（{r[2]:.3f}）"
                    f"，靶点屏内坐标：卡片=({hit[0]},{hit[1]})，「参加」=({r[0] - rect[0]},{r[1] - rect[1]})。",
                    level="hit")
                return scan.ACCEPT, r
            if r != "nudged":
                if not rec["_join_warned"]:
                    rec["_join_warned"] = True
                    ctx.log("认出卡片但「参加」按钮一直没出现（已自动微滚补全；检查 join 模板/阈值），原地重试。", level="warn")
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
        """统一实现见 base.Task._find_join_in_column（整列枚举取距离卡片行最近那枚「进入」）。
        副本卡在此传 max_follow_cap：认出的卡片那行若没有匹配上的「进入/参加」，宁可微滚重找，
        也绝不把隔壁行按钮（例如运镖卡的「参加」，曾 d=65 被稀疏列容差放行而进错活动）当目标——
        本任务卡片的「参加」从不该指向其它行。"""
        return self._find_join_in_column(ctx, list_region, entry_screen_xy, threshold, loop,
                                         self.flags.get("join"),
                                         self.flags.get(self._card_key()),
                                         max_follow_cap=loop.get("join_same_row_px", 55))
