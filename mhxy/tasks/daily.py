# -*- coding: utf-8 -*-
"""
日常：把已有任务按用户勾选、分「单人任务/多人任务」两组串起来跑完。

分组（用户拍板，2026-09-07 重做，与「单人任务/多人任务」两个页面对齐；两区分组固定，不提供互换）：
  · 个人组（CHAINABLE_SINGLE = list(SINGLE_TASK_ORDER)，改那张表两者同步）
        = 宝图 / 秘境降妖 / 趣味鉴赏 / 三界奇缘 / 运镖 / 帮派签到 / 活跃度奖励 / 刮刮乐
        —— 每窗口独立任务链，互不制约：号1 自己 运镖→宝图→… 一路跑，号2 独立同样跑，
        靠一只鼠标在号间轮转交错推进，谁也不等谁。
        例外（用户拍板，2026-09-22；刮刮乐 2026-10-20 加入）：三界奇缘 / 趣味鉴赏 / 刮刮乐
        （CHAIN_SEQUENTIAL=True）不交错轮转，
        而是【一个号把它从头做完整（record done）才轮到下一个号做】——主循环指定唯一持有者、
        以 blocking 推进，排队的号跳过本号等待。
  · 多人组（MULTI_BARRIER）= 刷副本 / 抓鬼
        —— 本质跨窗口协作（必须先组队、且只有队长操作），做不成「每窗口独立」，
        故是【集体屏障】：各窗口在自己的链里走到这一步就【停靠等待】，直到所有未完成的号都汇合到
        这一步，才集体跑一次（组队→队长线性跑完），跑完一起放行。
        多人组固定排在单人组前面（用户拍板去掉两区排序），steps 全局有序=执行顺序。
  · 单人任务组在本趟流程中 → 多人步跑完转入单人步前【强制解散】队伍（单人步=每窗口独立链，
    残留队伍会干扰视角/点名；「日常」页的「跑完多人任务后解散队伍」开关随之强制打开并锁定）。
    整条链只有多人步、后面没有单人步时，才按该开关决定是否收尾解散。

「刷副本」步指向**副本中枢当前勾选的全部副本**（tasks.dungeon.selected，is_dungeon 列表，按勾选顺序），
逐个跑它们各自的「先组队再跑流程」，而非 DungeonTask 本身（那只组队即停）；选哪些副本/顺序/队长/标定都在「刷副本」页。

设计原则（用户拍板）：完全套用每个子任务【已经做好的流程】，本任务只做「串联 + 轮转编排」——
  · 子任务列表与顺序存 tasks.daily.steps（有序，每项 {task, enabled}），界面可勾选 + 上下调序；
  · 多开/单开、各自的标定与参数，全部沿用各子任务自身的配置（本任务不另设）；
  · 独立子任务经其 make_chain_driver(wctx) 暴露「每窗口一份 record + 单步推进函数」，与它自己的 run()
    共用同一套状态机；本任务把多个子任务在每个窗口上首尾相接成一条链；
  · 每步进链前先跑该子任务的 preflight：不通过就跳过该步（缺标定/缺窗口等）；处于「演练」的子任务也跳过
    （演练只自检、永不结束，会卡死整条链）——要纳入实跑请到该任务页关掉「演练」；
  · 全程勤查 should_stop（停止/热键/鼠标甩角都能整条龙叫停）；整体时间上限只是安全网。

为什么不含「抢关注」：它是【无限盯市场抢货】、不会自己跑完，串进来会一直卡住，后面的任务永远轮不到，
不属于日常这种「跑通即进入下一个」的流程（用户拍板排除）。
单开模式：只有一个窗口，自然就是顺序跑完一条链（同一套引擎，窗口数=1）。
"""

import subprocess
import time

from .base import (Task, register, get_task, dungeon_tasks, dungeon_display_names,
                   enter_target_for)
from .dungeon_base import DUNGEON_NS
from ..core.config import SINGLE_TASK_ORDER
from ..core import window as win_mod
from ..core.teaming import TeamFormation
from ..ui import ui_state

# 可进日常的任务（这些都有明确「完成条件」、会自动结束）。抢关注 sniper 不在此列。
# 个人组：每窗口独立链（各自都能经 make_chain_driver 逐窗口跑）。
# 默认顺序 = 单人任务页签顺序（与 config.SINGLE_TASK_ORDER 同源，改页签/默认序就改那一处）。
CHAINABLE_SINGLE = list(SINGLE_TASK_ORDER)
# 多人组：跨窗口协作，作为「集体屏障」——各号走到该步停靠，汇合齐才集体跑一次。
# "dungeon" 是「刷副本」中枢步：跑时解析成 tasks.dungeon.selected 选中的那个副本（见 _resolve）。
# 5个副本子任务（60侠士/70侠士/60普通1/60普通2/70普通）也都算多人任务，各自只刷该本。
DUNGEON_SUB_TASKS = ["dt_60_xiashi", "dt_70_xiashi", "dt_60_common1", "dt_60_common2", "dt_70_common"]
MULTI_BARRIER = ["dungeon", "zhuagui"] + DUNGEON_SUB_TASKS
# 流程控制类「集体动作」伪步骤：日常流程控制页把它们当作可拖拽的执行步骤放在链里，
# 各号走到即汇合、用主 ctx 集体执行一次。它们不属 CHAINABLE（不参与缺行补齐/区组），
# 位置完全由 steps 存储顺序决定。team_form=一键组队，team_disband=一键解散。
TEAM_FORM_STEP = "team_form"
TEAM_DISBAND_STEP = "team_disband"
TEAM_STEPS = [TEAM_FORM_STEP, TEAM_DISBAND_STEP]
TEAM_STEP_TITLES = {TEAM_FORM_STEP: "组队", TEAM_DISBAND_STEP: "解散队伍"}

# 「整理背包」也是 steps 里的一个可拖拽、可重复的伪步骤（user 2026-11-01 拍板）：
# 引擎走到该步才在【每个号】上就地整理一次（不汇合、不做集体屏障）；整条流程里可放多个。
# 与自动整理旧开关 tasks.organize_bag.auto_organize 的关系：steps 里出现本步=「按位置执行」，
# 不再在任务交接点自动触发；旧 config（steps 里没有本步）仍由交接点自动整理，行为零回归。
ORGANIZE_STEP = "organize"
ORGANIZE_STEP_TITLE = "自动整理背包"
# 整条链里允许重复出现的伪步骤（组队/解散/整理）：去重、缺行补齐都不作用于它们。
REPEATABLE_STEPS = TEAM_STEPS + [ORGANIZE_STEP]
# 任何一个这类伪步骤出现，就视为「新结构链」：严格按存储绝对顺序执行、不按区重排。
STRUCTURE_STEPS = TEAM_STEPS + [ORGANIZE_STEP]

# 「日常流程控制」弹窗保存链时打的显式标记（tasks.daily.flow_ordered=True，user 2026-11-01）：
# 用户的诉求是「引擎严格按 UI 上设置的流程顺序执行」，故只要这条链是弹窗存过的，**哪怕里面
# 一个伪步骤都没有（纯普通任务）**，引擎也按 steps 存储绝对顺序跑，绝不再按多人/单人分区重排。
# 没经弹窗保存过的旧 config 没有此标记，维持「多人组在前」的按区拼段（行为零回归）。
FLOW_ORDERED_KEY = "flow_ordered"

COLLECTIVE_BARRIER = MULTI_BARRIER + TEAM_STEPS
CHAINABLE = CHAINABLE_SINGLE + MULTI_BARRIER
GROUP_OF = {n: "single" for n in CHAINABLE_SINGLE}
GROUP_OF.update({n: "multi" for n in MULTI_BARRIER})
# 分区顺序【唯一来源】：多人组在前、其余按 GROUP_OF 的出现顺序。
# 日常页 `_GROUPS`、起始页日常卡 `_GROUPS`、引擎 `_enabled_steps` 的 steps 分段都引用它，
# 保证「界面显示的执行顺序 == 引擎跑的顺序」——别在别处再算一套。
GROUPS = ["multi"] + [g for g in dict.fromkeys(GROUP_OF.values()) if g != "multi"]
GROUP_ORDER_DEFAULT = list(GROUPS)          # 两区分组固定（多人在前、单人在后），保留作配置默认值
GROUP_TITLES = {"single": "单人任务", "multi": "多人任务"}
# 以后要加新分区：这里补 GROUP_OF 的映射 + GROUP_TITLES 标题即可；日常页/起始页的 _GROUPS、
# 引擎的 steps 分段、config 的 group_order 都会自动跟进，无需改别处。


def get_selected_dungeon_titles(cfg):
    """取当前勾选的副本标题列表，**按展示顺序**（侠士本在前、类内等级低→高，再普通本）。
    供起始页/日常页的执行链显示用（把「刷副本」展开为具体副本名）。"""
    order = dungeon_display_names()
    sel = (cfg.get("tasks", {}) or {}).get("dungeon", {}).get("selected")
    if isinstance(sel, str):
        sel = [sel]
    if not isinstance(sel, list):
        return []
    chosen = {s for s in sel if isinstance(s, str)}
    out = []
    for n in order:
        if n in chosen:
            cls = get_task(n)
            out.append(cls.title if cls else n)
    return out


@register
class DailyTask(Task):
    name = "daily"
    title = "日常"
    description = "勾选已有任务按顺序跑完；单人任务每窗口独立跑、多人任务（刷副本/抓鬼）集体汇合后跑"

    # 日志来源短标签（GUI 全局面板）：运行中由引擎更新为「当前正在跑的子任务名」，
    # GUI 侧（DailyPage.pump）据此把来源标签显示成「宝图/运镖/刷副本…」而非笼统的「日常」。
    # None=回退「日常」（流程开场/收尾/解散等非任务日志）。
    current_log_source = None

    # 本任务没有自己的标定（全部沿用各子任务）；calibrate_dialog 据此不渲染任何标定项。
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        if not self._enabled_steps(ctx):
            problems.append("还没勾选任何任务 —— 请在「日常」页勾选要串起来跑的任务")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」），"
                            "请先打开游戏并在「选择窗口」里选好")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        self._any_real_run = False     # 本趟是否有任何任务真跑（演练/未就绪被跳过的都不算）
        self._team_ready = False       # 本趟是否已组好队：第一个多人步组好后，后续多人步复用（不重复组队）
        self.current_log_source = None  # 开场/收尾日志先按「日常」；进子任务时由 _begin_step/_run_collective 改写
        steps = self._enabled_steps(ctx)
        if not steps:
            ctx.log("没有勾选任何任务，已停止。", level="error")
            return

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)
        tick = 0.5
        time_limit = self._time_limit(ctx)
        start_ts = time.time()
        deadline = start_ts + time_limit * 60 if time_limit > 0 else None

        # 用户拍板：窗口基准尺寸检查/归一化在「开始日常」时只做一次；链内各子任务不再重复做。
        win_mod.ensure_windows_normalized_once(ctx, move_windows=False)

        wins = ctx.select_windows()
        if not wins:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        if multi:
            wctxs = [ctx.make_child(w, f"号{self._window_no(w, i)}") for i, w in enumerate(wins)]
        else:
            ctx.window = wins[0]
            wctxs = [ctx]
        chains = [self._new_chain(w) for w in wctxs]

        titles = " → ".join(self._title_of(n, ctx) for n in steps)
        mode = f"多开 {len(wctxs)} 个号·每窗口独立任务链轮转" if multi else "单号"
        ctx.log(f"★ 日常启动（{mode}）：{titles} ★", level="warn")
        barriers = [n for n in steps if n in COLLECTIVE_BARRIER]
        if barriers:
            ctx.log("集体步=汇合屏障：" + "、".join(self._title_of(n, ctx) for n in barriers)
                    + " —— 各号走到它就汇合等待，组队/多人任务/解散由主流程集体执行后一起放行。")
        if multi:
            ctx.log("各号独立推进单人任务链，谁先跑完谁先进下一个，互不等待。")
        seq_steps = [n for n in steps if getattr(get_task(n), "CHAIN_SEQUENTIAL", False)]
        if seq_steps and multi:
            ctx.log("其中 " + "、".join(self._title_of(n, ctx) for n in seq_steps)
                    + " 按逐号顺序执行：一个号完整跑完再轮下一个号，不做跨号轮转。")
        if time_limit > 0:
            ctx.log(f"整体时间上限 {time_limit:g} 分钟（仅安全网；正常按各任务自身条件跑完）。")

        while not ctx.should_stop():
            if deadline and time.time() >= deadline:
                ctx.log(f"已达整体时间上限 {time_limit:g} 分钟，停止。", level="warn")
                break
            if all(c["done"] for c in chains):
                break
            alive = [c for c in chains if not c["done"]]
            if not alive:
                break

            # —— 集体屏障：未完成的号是否都停靠在同一个多人步？都到齐就集体跑一次、放行 ——
            parked = [c for c in alive if self._at_barrier(c, steps)]
            if parked and len(parked) == len(alive):
                cur = steps[parked[0]["idx"]]
                if cur in TEAM_STEPS:
                    self._run_team_step(ctx, cur)
                else:
                    self._run_collective(ctx, cur)
                    # —— 多人组最后一个多人步跑完 → 其后若接单人任务：必须先解散（单人步=每窗口
                    # 独立链，残留队伍会干扰视角/点名，强制解散、无视开关）；但链中若已显式放了
                    # 「解散队伍」步骤（在单人步之前），就交给该步骤、这里不再自动解散，避免重复。
                    # 整条链到此为止（无后续步骤）时，才按旧 auto_disband 开关决定是否收尾解散
                    # （兼容旧日常页；同样尊重显式解散步骤，避免解散两次）。
                    rest = steps[parked[0]["idx"] + 1:]
                    explicit_disband = TEAM_DISBAND_STEP in rest
                    if rest:
                        if (not any(s in MULTI_BARRIER for s in rest)
                                and not explicit_disband):
                            self._disband_after_multi(ctx, wctxs, has_single=True)
                    elif bool(ctx.task_cfg("teaming").get("auto_disband", False))                             and not explicit_disband:
                        self._disband_after_multi(ctx, wctxs, has_single=False)
                for c in parked:
                    self._advance(c, steps)
                continue
            # 部分号已停靠等待：各提示一次（节流），免得用户以为卡住
            for c in parked:
                if not c["park_logged"]:
                    barrier_title = self._title_of(steps[c["idx"]], ctx)
                    c["wctx"].log(f"已完成前面的任务，等其它号汇合到「{barrier_title}」一起跑…")
                    c["park_logged"] = True

            # —— 推进各「未停靠、未完成」窗口的独立链一段 ——
            drivable = [c for c in alive if not self._at_barrier(c, steps)]
            # 顺序执行任务（三界奇缘/趣味鉴赏，CHAIN_SEQUENTIAL，用户拍板）：同一时刻只让
            # 一个号持有它、从头做到完整（blocking 推进）；排队的号本轮跳过、不做跨号轮转。
            seq_holder = None
            for c in drivable:
                if ctx.should_stop():
                    break
                is_seq = self._current_seq_chain(ctx, c, steps)
                if is_seq and seq_holder is not None:
                    if not c["seq_wait_logged"]:
                        c["wctx"].log(f"「{self._title_of(steps[c['idx']], ctx)}」排队等待："
                                      f"前一个号跑完才轮到本号。")
                        c["seq_wait_logged"] = True
                    continue
                wctx = c["wctx"]
                if wctx.window.rect() is None:
                    if not c["gone_warned"]:
                        wctx.log("目标窗口不见了，跳过该号（其余号继续）。", level="warn")
                        c["gone_warned"] = True
                    continue
                if multi:
                    if not wctx.window.activate():
                        if not c["fg_warned"]:
                            wctx.log("未能切到前台（系统拒绝焦点抢占），本轮跳过、下轮重试。", level="warn")
                            c["fg_warned"] = True
                        continue
                    if ctx.should_stop():
                        break
                if is_seq:
                    seq_holder = c     # 窗口可用才授予「顺序任务唯一推进者」持有权
                c["gone_warned"] = c["fg_warned"] = False
                self._drive_chain_until_yield(ctx, c, steps, blocking=is_seq)
                if seq_holder is c:
                    seq_holder = None
                if multi and len(drivable) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(tick, ctx))

        # —— 汇总 ——
        done_n = sum(1 for c in chains if c["done"])
        ctx.log(f"日常结束：{done_n}/{len(chains)} 个号跑完整条链，"
                f"用时 {(time.time() - start_ts) / 60:.1f} 分钟。")
        unfinished = [c for c in chains if not c["done"]]
        if unfinished and not ctx.should_stop():
            desc = "、".join(
                f"{c['wctx'].label or '该号'}停在「"
                f"{self._title_of(steps[c['idx']], ctx) if c['idx'] < len(steps) else '收尾'}」"
                for c in unfinished)
            ctx.log("未跑完：" + desc + "。", level="warn")
        # —— 跑完关机：所有号都跑完整条链才触发（手动停止/时间上限中断均不触发）——
        if not ctx.should_stop() and done_n == len(chains):
            self._maybe_shutdown(ctx)

    # ------------------------------------------------------------------
    # 每窗口独立链：链记录 + 推进引擎
    # ------------------------------------------------------------------
    @staticmethod
    def _new_chain(wctx):
        """每窗口一条独立链记录。idx=当前在 steps 的位置；sub_rec/sub_step=当前子任务的状态机与单步函数。
        sub_seq=当前子任务是否为「顺序执行」型（CHAIN_SEQUENTIAL，三界奇缘/趣味鉴赏），由 _begin_step 写入。"""
        return {"wctx": wctx, "idx": 0, "sub_rec": None, "sub_step": None,
                "sub_seq": False, "seq_wait_logged": False,
                "cur_title": None, "done": False,
                "gone_warned": False, "fg_warned": False, "park_logged": False}

    @staticmethod
    def _at_barrier(c, steps):
        """该窗口当前停靠在「多人步」集体屏障上（未完成、未在跑子任务、当前步是多人组任务）。"""
        return (not c["done"] and c["sub_step"] is None
                and c["idx"] < len(steps) and steps[c["idx"]] in COLLECTIVE_BARRIER)

    def _current_seq_chain(self, ctx, c, steps):
        """该窗口「当前所在步」是否是顺序执行任务（CHAIN_SEQUENTIAL，三界奇缘/趣味鉴赏）。
        已开跑的看 _begin_step 记录的 sub_seq；还没开跑的按任务类判定（首次遇到也认得出）。"""
        if c["sub_step"] is not None:
            return bool(c.get("sub_seq"))
        if c["idx"] < len(steps):
            _eff, task_cls = self._resolve(steps[c["idx"]], ctx)
            return bool(task_cls is not None and getattr(task_cls, "CHAIN_SEQUENTIAL", False))
        return False

    @staticmethod
    def _advance(c, steps):
        """把链游标推进到下一步（越界=整条链跑完）。"""
        c["idx"] += 1
        c["park_logged"] = False
        if c["idx"] >= len(steps):
            c["done"] = True

    @staticmethod
    def _organize_between(c):
        """任务间清扫（user 2026-10-08 拍板：自动整理背包只在「各任务之间」触发，单个任务
        执行中绝不触发）：上一个任务刚完成/被跳过、马上要开下一个任务时，自动整理一次背包。

        仅旧结构链（steps 里没有显式「整理背包」步）走这里；新结构链改为在链中走到
        ORGANIZE_STEP 时按位置执行（见 _run_organize_step）。

        新（user 2026-10-29 拍板）：先把画面带回主界面，再检测「背包满」——否则上一个任务
        没能回到主界面（如还停在活动列表/鉴赏界面），可能在错误界面下误检背包满，诊断截图
        也会截到活动界面而不是真正的背包画面。
        开关 tasks.organize_bag.auto_organize；未标定满图标/没满 → maybe_auto_organize 静默返回。"""
        wctx = c["wctx"]
        try:
            # 交接处先保证回到主界面，避免在活动/战斗/弹窗界面下误检背包满
            if not ui_state.is_main_screen(wctx.cfg, wctx.window):
                wctx.log("交接处未在主界面，先按 Esc/右键逐层退回主界面再检测背包…", level="info")
                ui_state.back_to_main_screen(wctx.cfg, wctx.window)
            wctx.maybe_auto_organize()
        except Exception as e:
            wctx.log(f"自动整理背包检测异常（已忽略，继续）：{e}", level="warn")

    @staticmethod
    def _run_organize_step(ctx, c):
        """链中显式「自动整理背包」步（user 2026-11-01 拍板）：每个号走到该步就在【本号】
        上整理一次（不汇合、不是集体屏障，多开时各号随各自进度独立整理）；整条流程可放多个。

        与交接点自动整理不同：这是用户显式排进流程的动作，故无视 auto_organize 旧开关强制执行
        （force=True），但仍需标定「背包满图标」、背包没满则静默跳过（没东西可整理）。"""
        wctx = c["wctx"]
        try:
            if not ui_state.is_main_screen(wctx.cfg, wctx.window):
                wctx.log("整理背包前未在主界面，先按 Esc/右键逐层退回主界面…", level="info")
                ui_state.back_to_main_screen(wctx.cfg, wctx.window)
            wctx.log("───── 流程步骤「自动整理背包」：整理本号背包 ─────", level="hit")
            wctx.maybe_auto_organize(force=True)
        except Exception as e:
            wctx.log(f"「自动整理背包」步骤异常（已忽略，继续）：{e}", level="warn")

    @staticmethod
    def _end_step(c):
        c["sub_rec"] = None
        c["sub_step"] = None
        c["sub_seq"] = False
        c["seq_wait_logged"] = False

    def _drive_chain_until_yield(self, ctx, c, steps, blocking=False):
        """切前台后连续推进本窗口的独立链：当前子任务能往下走就接着走、跑完就接下一个子任务，
        直到撞「等待点」(状态没变) / 撞多人屏障 / 整条链跑完 / 撞连续推进上限 才让出。
        与 core/rotation._drive_until_yield 同一套判据，只是这里跨「子任务边界」也能连推。

        blocking=True：顺序执行任务（CHAIN_SEQUENTIAL，三界奇缘/趣味鉴赏）的持有者专用——
        本窗口被主循环指定为「该任务的唯一推进者」时，忽略「状态没变就让出」与连续推进上限，
        把一个号从头到尾做完整（step 内部自带拟人 sleep 与各自的超时兜底，不会死循环）；
        做完整后：后续步骤仍是顺序型 → 继续持有连推；是普通任务/撞障碍/整条链跑完 → 让出交还轮转。"""
        cap, t0 = 0, time.time()
        # steps 里含「整理背包」伪步骤 = 新结构链：整理只在走到该步时按位置执行，
        # 不再在任务交接点自动触发（user 2026-11-01 拍板）；旧 config（无该步）维持交接点自动整理。
        auto_between = ORGANIZE_STEP not in steps
        while not ctx.should_stop():
            if c["sub_step"] is None:
                if c["idx"] >= len(steps):
                    c["done"] = True
                    return
                cur = steps[c["idx"]]
                if cur in COLLECTIVE_BARRIER:
                    return                              # 停靠集体屏障，交回主循环
                if cur == ORGANIZE_STEP:
                    # 走到「整理背包」步：在当前这个号上就地整理一遍（每号各自执行，不汇合），
                    # 随后推进到下一步。异常已在内部吞掉，绝不阻断整条链。
                    self.current_log_source = ORGANIZE_STEP
                    self._run_organize_step(ctx, c)
                    self._advance(c, steps)
                    continue
                # 任务交接点（上一个任务完成/被跳过 → 开下一个）：仅旧结构链在此自动整理背包。
                # 单个任务执行过程中绝不触发（user 2026-10-08 拍板）。
                self.current_log_source = cur   # 交接/整理日志按「即将开跑的任务」打标签
                if auto_between:
                    self._organize_between(c)
                if self._begin_step(ctx, c, cur) != "ready":
                    c["idx"] += 1                       # 跳过该步（未就绪/演练/不支持），接着下一步
                    continue
                if blocking and not c["sub_seq"]:
                    return                              # 顺序步已做完、后续不是顺序型任务 → 交还轮转
            rec = c["sub_rec"]
            before = rec["state"]
            try:
                c["sub_step"]()
            except Exception as e:                      # 单个子任务炸了不该带垮整条链
                import traceback
                c["wctx"].log(f"「{c['cur_title']}」运行异常：{e}\n{traceback.format_exc()}，跳过该任务。", level="error")
                self._end_step(c)
                c["idx"] += 1
                continue
            if rec.get("done"):
                c["wctx"].log(f"───── 「{c['cur_title']}」完成 ─────", level="hit")
                self._end_step(c)
                c["idx"] += 1
                continue                                # 接着在本窗口推进下一步
            if rec["state"] == before:
                if blocking:
                    continue                            # 顺序执行：状态没变也继续做，做完才让出
                return                                  # 等待点（监控盯屏/等响应）→ 让出
            cap += 1
            if not blocking and (cap >= 12 or time.time() - t0 >= 4.0):
                return                                  # 连续推进上限，强制让出（同 rotation）

    def _begin_step(self, ctx, c, name):
        """为某独立步在该窗口上建驱动器。返回 "ready"(已建) 或 "skip"(已跳过并记日志)。
        preflight 用主 ctx（检查全局：标定/快捷键/选中窗口）。"""
        wctx = c["wctx"]
        title = self._title_of(name, ctx)
        c["cur_title"] = title
        self.current_log_source = name       # 本步期间日志来源标签=该子任务（GUI 侧映射成短名）
        eff_name, task_cls = self._resolve(name, ctx)
        if task_cls is None:
            wctx.log(f"「{title}」无可跑任务（未选/未收录），跳过。", level="warn")
            return "skip"
        task = task_cls()
        if not getattr(task, "CHAINS_PER_WINDOW", False):
            wctx.log(f"「{title}」不支持每窗口独立链，跳过。", level="warn")
            return "skip"
        # 顺序执行型任务（三界奇缘/趣味鉴赏）：让主循环「一个号从头做完整再轮下一个号」
        c["sub_seq"] = bool(getattr(task, "CHAIN_SEQUENTIAL", False))
        ok, probs = task.preflight(ctx)
        if not ok:
            wctx.log(f"跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return "skip"
        rec, step_fn = task.make_chain_driver(wctx)
        c["sub_rec"], c["sub_step"] = rec, step_fn
        self._any_real_run = True        # 真正开跑了（非演练）
        wctx.log(f"───── 开始「{title}」 ─────", level="hit")
        return "ready"

    def _team_head(self, ctx):
        """多人步开场提示语：据「本趟队伍是否已就绪 / 用户是否勾了已组队」说明谁来组队。"""
        if bool(ctx.task_cfg("teaming").get("skip_team", False)):
            return "已组队，队长直接跑"
        if self._team_ready:
            return "队伍已就绪，队长直接跑"
        return "组队 → 队长跑"

    def _collective_run(self, ctx, task):
        """跑一个多人步，并把「队伍已就绪」在本趟日常内传递下去（跨副本、跨多人步只组一次）。

        本趟前序多人步已组好队时（_team_ready），临时把共享 tasks.teaming.skip_team 置 True 让本步
        跳过它自带的组队握手，结束（正常/异常/停止）都在 finally 恢复原值——只改内存、不落盘，
        既不影响后续手动单跑副本/抓鬼，也不影响其它多人任务的独立组队。
        跑完若任务报告队伍可用（task._team_formed，含「本就已在队中」），标记后续多人步一律复用。
        """
        team_c = ctx.cfg.setdefault("tasks", {}).setdefault("teaming", {})
        orig = bool(team_c.get("skip_team", False))
        reuse = self._team_ready and not orig
        if reuse:
            team_c["skip_team"] = True
        try:
            task.run(ctx)
        finally:
            if reuse:
                team_c["skip_team"] = orig
        if getattr(task, "_team_formed", False):
            self._team_ready = True

    def _run_team_step(self, ctx, step_name):
        """集体动作伪步骤：team_form=一键组队，team_disband=一键解散。
        各号在屏障汇合后由主 ctx 执行一次（任务壳自己 select_windows+make_child），
        复用 tasks.dungeon 的 DungeonTask（title=组队）/ tasks.disband 的 DisbandTask。"""
        if ctx.should_stop():
            return
        title = TEAM_STEP_TITLES.get(step_name, step_name)
        task_name = "dungeon" if step_name == TEAM_FORM_STEP else "disband"
        task_cls = get_task(task_name)
        if task_cls is None:
            ctx.log(f"「{title}」任务未收录，跳过该步。", level="warn")
            return
        self.current_log_source = task_name
        task = task_cls()
        ok, probs = task.preflight(ctx)
        if not ok:
            ctx.log(f"跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return
        ctx.log(f"───── 所有号已汇合，开始集体动作「{title}」 ─────", level="hit")
        try:
            self._any_real_run = True
            task.run(ctx)
            if step_name == TEAM_FORM_STEP:
                # 只有任务壳确认队伍真的组好才置位（失败/超时只告警不抛异常，不能误标，
                # 否则后续多人步会跳过握手）。契约同副本/抓鬼的 _team_formed。
                if getattr(task, "_team_formed", False):
                    self._team_ready = True
            else:
                self._team_ready = False
        except Exception as e:
            ctx.log(f"「{title}」执行异常：{e}，继续后续任务。", level="error")

    def _run_collective(self, ctx, step_name):
        """集体跑一次「多人步」：组队 → 队长线性跑完（→可选解散）。用主 ctx（任务自带 select+组队）。
        阻塞执行，期间各队员号在屏障上待命（本就在队伍里被传送/自动战斗）。
        组队只在「本趟第一个真正跑的多人步」做一次，后续多人步复用同一队伍（见 _collective_run）：
        队伍要活到最后一个多人步跑完才解散（_disband_after_multi），中途重跑组队既徒增耗时，
        队长已在队中还会让「创建队伍」落空。dungeon=把勾选的全部副本按勾选顺序一个个刷完；
        zhuagui=抓鬼一次。"""
        if step_name == "dungeon":
            self._run_collective_dungeons(ctx)
            return
        if step_name in DUNGEON_SUB_TASKS:
            self._run_collective_single_dungeon(ctx, step_name)
            return
        self.current_log_source = step_name   # 集体步日志来源标签=该多人任务
        title = self._title_of(step_name, ctx)
        eff_name, task_cls = self._resolve(step_name, ctx)
        if task_cls is None:
            ctx.log(f"「{title}」无可跑任务（未收录/未选），跳过该步。", level="warn")
            return
        task = task_cls()
        ok, probs = task.preflight(ctx)
        if not ok:
            ctx.log(f"跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return
        ctx.log(f"───── 所有号已汇合，开始集体「{title}」（{self._team_head(ctx)}）─────", level="hit")
        try:
            self._any_real_run = True
            self._collective_run(ctx, task)
        except Exception as e:
            ctx.log(f"「{title}」运行异常：{e}，继续后续任务。", level="error")
            return
        if not ctx.should_stop():
            ctx.log(f"───── 「{title}」完成 ─────", level="hit")

    def _run_collective_single_dungeon(self, ctx, dname):
        """集体跑单个副本子任务（如 60侠士/70侠士/60普通1/60普通2/70普通）。
        只刷该本，进本「进入」序号按该副本在标签区展示顺序里的位置点。
        是否组队由流程中「已组队」开关决定（见 _collective_run / skip_team）。"""
        self.current_log_source = dname
        cls = get_task(dname)
        if cls is None:
            ctx.log(f"副本「{dname}」未收录，跳过。", level="warn")
            return
        task = cls()
        ok, probs = task.preflight(ctx)
        if not ok:
            title = cls.title
            ctx.log(f"跳过副本「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return
        title = cls.title
        ctx.log(f"───── 所有号已汇合，集体刷副本「{title}」({self._team_head(ctx)}) ─────", level="hit")
        et = enter_target_for(dname)
        if et:
            (ctx.cfg.setdefault("tasks", {}).setdefault(DUNGEON_NS, {}))["enter_target"] = et
        try:
            self._any_real_run = True
            self._collective_run(ctx, task)
        except Exception as e:
            ctx.log(f"「{title}」运行异常：{e}，继续后续任务。", level="error")
            return
        if not ctx.should_stop():
            ctx.log(f"───── 「{title}」完成 ─────", level="hit")


    def _run_collective_dungeons(self, ctx):
        """「刷副本」集体步：把勾选中的副本**按展示顺序**（侠士本在前、类内等级低→高，
        然后普通本）逐一刷完（各自 run() 自带组队，但队伍由 _collective_run 保证整趟只组一次：
        第一个副本组好后，后面直接复用）。勾选只决定「刷不刷」，不决定顺序（见 _selected_dungeons）。
        某副本演练/未就绪/异常 → 跳过继续下一个，最后汇总（行为与「刷副本」页顺序队列一致）。"""
        self.current_log_source = "dungeon"   # 整个刷副本步日志来源标签=刷副本
        names = self._selected_dungeons(ctx)
        if not names:
            # 正常到不了这（_enabled_steps 已把空选的刷副本步整步剔掉），纯防御网。
            ctx.log("「刷副本」未勾选任何副本，请在「日常」页「刷副本」步勾选，本步跳过。",
                    level="warn")
            return
        tc = ctx.task_cfg(DUNGEON_NS)      # 副本共享命名空间（标定/演练/loop 全在这）
        total = len(names)
        done = 0
        for i, dname in enumerate(names, 1):
            if ctx.should_stop():
                return
            cls = get_task(dname)
            if cls is None:
                ctx.log(f"副本「{dname}」未收录，跳过。", level="warn")
                continue
            title = cls.title
            task = cls()
            ok, probs = task.preflight(ctx)
            if not ok:
                ctx.log(f"跳过副本「{title}」（未就绪）：" + "；".join(probs), level="warn")
                continue
            ctx.log(f"─── 所有号已汇合，集体刷副本 {i}/{total}「{title}」"
                    f"（{self._team_head(ctx)}）───", level="hit")
            try:
                # 进本要点的「进入」序号：写进内存 cfg 的 tasks.dungeon.enter_target（运行时字段，
                # 不入盘）。同标签区几个「进入」长得一样，dungeon_base 按「该副本在标签区展示顺序里的
                # 第几个」来点——此前此字段随旧刷副本运行页删除后没人再写，导致全部副本都点第 1 个。
                et = enter_target_for(dname)
                if et:
                    (ctx.cfg.setdefault("tasks", {}).setdefault(DUNGEON_NS, {}))["enter_target"] = et
                self._any_real_run = True
                self._collective_run(ctx, task)
            except Exception as e:
                ctx.log(f"「{title}」运行异常：{e}，继续下一个副本。", level="error")
                continue
            if ctx.should_stop():
                return
            done += 1
            if done < total:
                ctx.log(f"─── 副本 {i}/{total}「{title}」完成，接下一个副本 ───", level="hit")
        ctx.log(f"刷副本汇总：{done}/{total} 个副本完成。", level="hit" if done == total else "warn")

    def _disband_after_multi(self, ctx, wctxs, has_single=False):
        """多人步跑完后决定并执行一次解散。has_single=True=后面就是单人任务组（单人步在队伍外跑，
        残留队伍会干扰视角/点名）→ 【强制解散】，无视「跑完解散」开关；has_single=False=整条链已到此
        结束，仅当用户勾了「跑完多人任务后解散队伍」才收尾解散。解散对「本就不在队」的号也安全收尾。
        单开/不足 2 个号直接跳过；失败只记日志，不阻塞整条龙。"""
        self.current_log_source = None   # 解散/收尾类日志回退「日常」标签
        if ctx.should_stop() or len(wctxs) < 2:
            return
        team_tc = ctx.task_cfg("teaming")
        if not has_single and not team_tc.get("auto_disband", False):
            ctx.log("多人任务已全部跑完（本次为最后一段），未勾「解散队伍」，保留队伍。", level="warn")
            return
        if not (team_tc.get("templates", {}) or {}).get("team_quit"):
            ctx.log(("解散队伍需先在「任务配置」页「组队（共享）」卡标定「退出队伍」模板 —— 单人任务必须在队伍外跑，"
                     "本次未解散，单人任务可能受影响。") if has_single
                    else "解散队伍需先在「任务配置」页「组队（共享）」卡标定「退出队伍」模板 —— 本次保留队伍。", level="warn")
            return
        reason = ("后面是单人任务组，单人步必须在队伍外跑，强制解散") if has_single \
                 else "已勾选「跑完多人任务后解散队伍」，收尾解散"
        ctx.log(f"───── 多人任务全部完成，{reason} ─────", level="warn")
        try:
            cap = int(team_tc.get("captain_index", 0) or 0)
            cap_child = wctxs[cap] if 0 <= cap < len(wctxs) else wctxs[0]
            assignments = [(cap_child, TeamFormation.ROLE_CAPTAIN)] + \
                          [(wc, TeamFormation.ROLE_MEMBER) for wc in wctxs if wc is not cap_child]
            team = TeamFormation(ctx, assignments, team_tc, dry_run=False)
            ok, reason = team.run_disband()
            if ok:
                ctx.log("队伍已解散，开始单人任务。", level="hit")
            else:
                ctx.log(f"解散队伍未完成（{reason}），继续后续任务。", level="warn")
        except Exception as e:
            ctx.log(f"解散队伍异常：{e}，继续后续任务。", level="warn")

    def _maybe_shutdown(self, ctx):
        """「跑完关机」：整条龙全部跑完且本趟有实跑任务后，先进关机倒计时——
        倒计时期间按「停止」/急停热键/甩角（都会置 should_stop）即取消、不发关机指令；
        倒计时走完才真正 `shutdown`。全演练/全部未就绪被跳过也不关（避免误关机）。"""
        tc = ctx.task_cfg(self.name)
        loop = tc.get("loop", {})
        if not loop.get("shutdown_after", False):
            return
        if not self._any_real_run:
            ctx.log("勾了「跑完关机」，但本趟没有任何任务真跑（全在演练/未就绪被跳过），不关机。",
                    level="warn")
            return
        try:
            delay = max(5, int(loop.get("shutdown_delay_sec", 60)))
        except (TypeError, ValueError):
            delay = 60
        ctx.log(f"★ 跑完关机：所有任务已执行完毕，进入 {delay} 秒关机倒计时"
                f"（按「停止」或急停热键可取消）★", level="warn")
        t0 = time.time()
        last_ann = None
        while not ctx.should_stop():
            remaining = int(delay - (time.time() - t0))
            if remaining <= 0:
                break
            ann = (remaining // 10) * 10
            if ann != last_ann:
                last_ann = ann
                ctx.log(f"…约 {ann} 秒后系统关机，按「停止」或急停热键可取消…")
            self._interruptible_sleep(ctx, 0.5)
        if ctx.should_stop():
            ctx.log("已取消关机（急停/停止）。", level="warn")
            return
        ctx.log("关机倒计时结束，立即关机。", level="warn")
        try:
            subprocess.Popen(["shutdown", "/s", "/t", "0"])
            ctx.log("已发送关机指令。", level="hit")
        except Exception as e:
            ctx.log(f"发送关机指令失败：{e}", level="error")

    # ------------------------------------------------------------------
    # 配置读取
    # ------------------------------------------------------------------
    @classmethod
    def _title_of(cls, name, ctx):
        if name == "dungeon":
            # 具体刷哪些副本在「刷副本」页勾选，日志里只显示一步「刷副本」，不挂具体副本名。
            return "刷副本"
        if name == ORGANIZE_STEP:
            return ORGANIZE_STEP_TITLE
        if name in TEAM_STEP_TITLES:
            return TEAM_STEP_TITLES[name]
        c = get_task(name)
        return c.title if c else name

    @staticmethod
    def _selected_dungeons(ctx):
        """读副本中枢勾选的【全部】有效副本名，**一律按展示顺序**返回。

        勾选框只决定「刷不刷」，不决定顺序：这里拿展示基准 base.dungeon_display_names()
        去过滤勾选结果（侠士本在前、类内等级低→高，然后普通本）。所以 config 里
        `tasks.dungeon.selected` 的存放顺序无关紧要——历史遗留或手改的乱序也不会影响跑的顺序，
        界面勾选区怎么排、跑的顺序就怎么排。

        **一个都没勾 = 本步不跑**：`_enabled_steps` 直接把这一步从 steps 里剔掉（与「整行关掉」
        完全同语义——不设屏障、不解散、不打日志），集体步里那句守卫只是防御网。缺失/非列表
        一律当空；勾了但全是未收录的名字，过滤后同样为空。"""
        order = dungeon_display_names()
        sel = ctx.task_cfg("dungeon").get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if not isinstance(sel, list):
            sel = []
        chosen = {s for s in sel if isinstance(s, str)}
        return [n for n in order if n in chosen]   # 按展示顺序过滤；未收录的名字自然落选

    @staticmethod
    def _selected_dungeon(ctx):
        """副本中枢勾选的首个有效副本名（标题/单副本场景用）。"""
        out = DailyTask._selected_dungeons(ctx)
        return out[0] if out else None

    @classmethod
    def _resolve(cls, name, ctx):
        """把链步名解析成真正可跑的 (任务名, 任务类)。
        'dungeon' 是副本中枢步：跑的是 tasks.dungeon.selected 选中的那个副本（is_dungeon），
        而非 DungeonTask 本身（它只组队即停）。其余任务名直接返回自身。"""
        if name != "dungeon":
            return name, get_task(name)
        sel = cls._selected_dungeon(ctx)
        return sel, (get_task(sel) if sel else None)

    def _enabled_steps(self, ctx):
        """返回本趟真正会跑的任务名列表，顺序=执行顺序，**严格按界面流程控制弹窗里设置的顺序**：
        ① 行级已勾选；② 组级开关（tasks.daily.group_enabled，{single,multi}，缺省 True）开启——
        整组关掉时该组全部任务不进日常（组内行级 enabled 独立保留，重开整组即恢复）；
        ③ 存储 steps 里没有的新任务补到末尾（默认启用，见下）；
        ④ 顺序：弹窗存过的链（flow_ordered=True）或含组队/解散/整理伪步骤时，严格按 steps
           存储绝对顺序（用户拖成啥就跑成啥，单人/多人可任意穿插）；没经弹窗保存的旧 config
           才按 GROUPS 拼段（多人组在前、区内保持存储相对顺序），行为零回归。

        「刷副本」步额外看有没有勾副本：**开着但一个都没勾 = 这步不存在**（用户拍板，与整行
        关掉完全等效——否则会白设一道没人跑的集体屏障、还可能误触发收尾/强制解散）。
        判据复用 _selected_dungeons（按展示顺序过滤已收录名），UI 侧 enabled_names /
        日常页 _refresh_exec_order、_renumber 必须同源，三处不一致就会「界面显示这步、引擎
        忽略」。"""
        tc = ctx.task_cfg(self.name)
        ge = tc.get("group_enabled") or {}
        def group_on(name):
            g = GROUP_OF.get(name)
            return ge.get(g, True) if g else True
        def dungeon_ok(name):
            return name != "dungeon" or self._selected_dungeons(ctx)
        # 单跑：内存覆盖的 steps 就是「只跑这个任务」，**不做缺行补齐**——否则会把其它
        # CHAINABLE 任务全补回来、单跑变成跑整条链（_single_build_cfg 置的标记）。
        explicit_only = bool(tc.get("explicit_steps"))
        # 「移除任务」标记：日常流程控制页明确移出的任务，存了也不跑、也不再当缺行补回
        # （空白占位 task="" 本就不在 CHAINABLE，天然跳过）。
        removed = set(tc.get("removed_tasks") or ())
        stored, seen = [], set()
        has_structure_step = False
        for step in tc.get("steps", []):
            if not isinstance(step, dict):
                continue
            name = step.get("task")
            if name in TEAM_STEPS:
                # 流程控制的集体动作：没有组/勾选门控，放在链里就执行；位置=存储绝对顺序。
                # 可在整条流程里出现多次（重复组队/解散），不做去重。
                stored.append(name)
                has_structure_step = True
                continue
            if name == ORGANIZE_STEP:
                # 整理背包：可重复出现的「每号独立」步骤，走到才整理（不是集体屏障）。
                # 停用的行跳过但仍计入结构标记，且绝不补回/去重。
                has_structure_step = True
                if step.get("enabled", True):
                    stored.append(name)
                continue
            if name not in CHAINABLE or name in removed:
                continue
            seen.add(name)          # 存在即可（禁用的行也算「存过」，见下：不能被当成缺行补回来）
            if not dungeon_ok(name):
                continue
            if step.get("enabled") and group_on(name):
                stored.append(name)
        # 存储 steps 里没有、CHAINABLE 里有 = 新任务上线、老 config 还没存这行：
        # 与界面「缺的补到末尾（默认启用）」同口径补进来，否则「界面显示这行、引擎却跳过」。
        # 用户在界面关掉的行是带 enabled=False 存着的（已在 seen 里），绝不会被这里补回来。
        # 单跑（explicit_steps）时跳过补齐，保持「只跑指定的那一个」。
        #
        # 聚合「刷副本」步 "dungeon" 不在补齐之列（user 2026-11-01 拍板）：流程控制弹窗早已把
        # 副本拆成 5 个独立子任务行（dt_*，勾选哪几个跑哪几个），存链时不再写聚合行；若这里还
        # 把它当缺行补到链尾，就会在「所有拆分子本刷完」之后、链的末尾又把勾选的全部副本集体
        # 重刷一遍（实测：活跃度奖励是最后一步，领完竟又跑「集体刷副本 1/2」）。显式存了聚合行
        # 的旧 config 仍照常执行（上面 stored 收集没过滤它），只修「没存却被误补」这一种。
        # UI 侧同口径隐藏集见 ui/pages/daily._HIDDEN_TASKS / daily_card / daily_flow（都排除 "dungeon"）。
        if not explicit_only:
            for name in CHAINABLE:
                if name == "dungeon":
                    continue
                if name not in seen and name not in removed \
                        and group_on(name) and dungeon_ok(name):
                    stored.append(name)
        # 严格按存储绝对顺序执行（=UI 流程控制弹窗里设置/拖拽的顺序）的两种情形：
        #   ① 含组队/解散/整理伪步骤（旧判定，自然是弹窗排过的新结构链）；
        #   ② tasks.daily.flow_ordered=True：弹窗保存纯普通任务链时也会打这个标记——
        #      用户要求「引擎按 UI 上的流程顺序跑」，即便没有伪步骤也不能再按多人/单人分区重排。
        # 两种情形都只对 CHAINABLE 普通任务去重（team/organize 允许重复，不能被 dedup 合并）。
        # 都不满足（没经弹窗保存过的旧日常页/旧 config）：维持「多人组在前」的按区拼段，零回归。
        absolute_order = has_structure_step or bool(tc.get(FLOW_ORDERED_KEY))
        if absolute_order:
            out, dedup = [], set()
            for name in stored:
                if name in REPEATABLE_STEPS:
                    out.append(name)
                elif name not in dedup:
                    out.append(name)
                    dedup.add(name)
            return out
        out = stored
        # 按区拼段（分段顺序与界面共用 tasks.daily.GROUPS），否则会「界面显示这步先跑、
        # 引擎却先跑另一步」——尤其是还没被界面保存过的旧 config（存的原序不一定是区序）。
        seg = {g: [n for n in out if GROUP_OF.get(n) == g] for g in GROUPS}
        return [n for g in GROUPS for n in seg.get(g, [])]

    def _time_limit(self, ctx):
        tc = ctx.task_cfg(self.name)
        try:
            return float(tc.get("loop", {}).get("time_limit_min", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
