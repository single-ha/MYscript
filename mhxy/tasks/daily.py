# -*- coding: utf-8 -*-
"""
日常一条龙：把已有任务按用户勾选、分「单人任务/多人任务」两组串起来跑完。

分组（用户拍板，2026-09-07 重做，与「单人任务/多人任务」两个页面对齐，两区顺序可互换）：
  · 个人组（CHAINABLE_SINGLE）= 宝图 / 运镖 / 秘境降妖 / 三界奇缘 / 帮派签到 / 活跃度奖励
        —— 每窗口独立任务链，互不制约：号1 自己 运镖→宝图→… 一路跑，号2 独立同样跑，
        靠一只鼠标在号间轮转交错推进，谁也不等谁。
  · 多人组（MULTI_BARRIER）= 刷副本 / 抓鬼
        —— 本质跨窗口协作（必须先组队、且只有队长操作），做不成「每窗口独立」，
        故是【集体屏障】：各窗口在自己的链里走到这一步就【停靠等待】，直到所有未完成的号都汇合到
        这一步，才集体跑一次（组队→队长线性跑完→可选解散），跑完一起放行、各自继续后面的独立链。
        多人步可排在顺序里的任意位置（常放最前先刷活跃度），不强制最后；多个多人步按列表顺序逐一到齐跑。
  · 组顺序存 tasks.daily.group_order（["single","multi"] 或反序），steps 全局有序=执行顺序，
    界面「⇅ 两区互换」把两组整段对调（组内顺序保留）。

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

为什么不含「秒装备」：它是【无限盯市场抢货】、不会自己跑完，串进来会一直卡住，后面的任务永远轮不到，
不属于一条龙这种「跑通即进入下一个」的流程（用户拍板排除）。
单开模式：只有一个窗口，自然就是顺序跑完一条链（同一套引擎，窗口数=1）。
"""

import subprocess
import time

from .base import Task, register, get_task, dungeon_tasks
from .dungeon_base import DUNGEON_NS

# 可进一条龙的任务（这些都有明确「完成条件」、会自动结束）。秒装备 sniper 不在此列。
# 个人组：每窗口独立链（各自都能经 make_chain_driver 逐窗口跑）。
CHAINABLE_SINGLE = ["treasure_map", "secret_realm", "escort", "sanjie",
                    "guild_checkin", "activity_reward", "appreciation"]
# 多人组：跨窗口协作，作为「集体屏障」——各号走到该步停靠，汇合齐才集体跑一次。
# "dungeon" 是「刷副本」中枢步：跑时解析成 tasks.dungeon.selected 选中的那个副本（见 _resolve）。
MULTI_BARRIER = ["dungeon", "zhuagui"]
CHAINABLE = CHAINABLE_SINGLE + MULTI_BARRIER
GROUP_OF = {n: "single" for n in CHAINABLE_SINGLE}
GROUP_OF.update({n: "multi" for n in MULTI_BARRIER})
GROUP_ORDER_DEFAULT = ["single", "multi"]   # 界面的「区顺序」默认个人在前（可互换）
GROUP_TITLES = {"single": "单人任务", "multi": "多人任务"}
# 以后要加新分区：这里补 GROUP_OF 的映射 + GROUP_TITLES 标题即可；一条龙页的 _GROUPS、
# 引擎的 steps 分段、config 的 group_order 都会自动跟进，无需改别处。


@register
class DailyTask(Task):
    name = "daily"
    title = "日常一条龙"
    description = "勾选已有任务按顺序跑完；单人任务每窗口独立跑、多人任务（刷副本/抓鬼）集体汇合后跑"

    # 本任务没有自己的标定（全部沿用各子任务）；calibrate_dialog 据此不渲染任何标定项。
    CALIBRATION = {"regions": [], "templates": [], "watchlist": False}

    # ------------------------------------------------------------------
    def preflight(self, ctx):
        problems = []
        if not self._enabled_steps(ctx):
            problems.append("还没勾选任何任务 —— 请在「日常一条龙」页勾选要串起来跑的任务")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」），"
                            "请先打开游戏并在「选择窗口」里选好")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        self._any_real_run = False     # 本趟是否有任何任务真跑（演练/未就绪被跳过的都不算）
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
        ctx.log(f"★ 日常一条龙启动（{mode}）：{titles} ★", level="warn")
        barriers = [n for n in steps if n in MULTI_BARRIER]
        if barriers:
            ctx.log("多人步=集体屏障：" + "、".join(self._title_of(n, ctx) for n in barriers)
                    + " —— 各号走到它就汇合等待，组队后由队长统一跑，跑完一起放行。")
        if multi:
            ctx.log("各号独立推进单人任务链，谁先跑完谁先进下一个，互不等待。")
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
                self._run_collective(ctx, steps[parked[0]["idx"]])
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
            for c in drivable:
                if ctx.should_stop():
                    break
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
                c["gone_warned"] = c["fg_warned"] = False
                try:
                    wctx.maybe_auto_organize()
                except Exception as e:
                    wctx.log(f"自动整理背包检测异常（已忽略，继续）：{e}", level="warn")
                self._drive_chain_until_yield(ctx, c, steps)
                if multi and len(drivable) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))
            self._interruptible_sleep(ctx, self._jitter(tick, ctx))

        # —— 汇总 ——
        done_n = sum(1 for c in chains if c["done"])
        ctx.log(f"日常一条龙结束：{done_n}/{len(chains)} 个号跑完整条链，"
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
        """每窗口一条独立链记录。idx=当前在 steps 的位置；sub_rec/sub_step=当前子任务的状态机与单步函数。"""
        return {"wctx": wctx, "idx": 0, "sub_rec": None, "sub_step": None,
                "cur_title": None, "done": False,
                "gone_warned": False, "fg_warned": False, "park_logged": False}

    @staticmethod
    def _at_barrier(c, steps):
        """该窗口当前停靠在「多人步」集体屏障上（未完成、未在跑子任务、当前步是多人组任务）。"""
        return (not c["done"] and c["sub_step"] is None
                and c["idx"] < len(steps) and steps[c["idx"]] in MULTI_BARRIER)

    @staticmethod
    def _advance(c, steps):
        """把链游标推进到下一步（越界=整条链跑完）。"""
        c["idx"] += 1
        c["park_logged"] = False
        if c["idx"] >= len(steps):
            c["done"] = True

    @staticmethod
    def _end_step(c):
        c["sub_rec"] = None
        c["sub_step"] = None

    def _drive_chain_until_yield(self, ctx, c, steps):
        """切前台后连续推进本窗口的独立链：当前子任务能往下走就接着走、跑完就接下一个子任务，
        直到撞「等待点」(状态没变) / 撞多人屏障 / 整条链跑完 / 撞连续推进上限 才让出。
        与 core/rotation._drive_until_yield 同一套判据，只是这里跨「子任务边界」也能连推。"""
        cap, t0 = 0, time.time()
        while not ctx.should_stop():
            if c["sub_step"] is None:
                if c["idx"] >= len(steps):
                    c["done"] = True
                    return
                if steps[c["idx"]] in MULTI_BARRIER:
                    return                              # 停靠集体屏障，交回主循环
                if self._begin_step(ctx, c, steps[c["idx"]]) != "ready":
                    c["idx"] += 1                       # 跳过该步（未就绪/演练/不支持），接着下一步
                    continue
            rec = c["sub_rec"]
            before = rec["state"]
            try:
                c["sub_step"]()
            except Exception as e:                      # 单个子任务炸了不该带垮整条链
                c["wctx"].log(f"「{c['cur_title']}」运行异常：{e}，跳过该任务。", level="error")
                self._end_step(c)
                c["idx"] += 1
                continue
            if rec.get("done"):
                c["wctx"].log(f"───── 「{c['cur_title']}」完成 ─────", level="hit")
                self._end_step(c)
                c["idx"] += 1
                continue                                # 接着在本窗口推进下一步
            if rec["state"] == before:
                return                                  # 等待点（监控盯屏/等响应）→ 让出
            cap += 1
            if cap >= 12 or time.time() - t0 >= 4.0:
                return                                  # 连续推进上限，强制让出（同 rotation）

    def _begin_step(self, ctx, c, name):
        """为某独立步在该窗口上建驱动器。返回 "ready"(已建) 或 "skip"(已跳过并记日志)。
        preflight 用主 ctx（检查全局：标定/快捷键/选中窗口）。"""
        wctx = c["wctx"]
        title = self._title_of(name, ctx)
        c["cur_title"] = title
        eff_name, task_cls = self._resolve(name, ctx)
        if task_cls is None:
            wctx.log(f"「{title}」无可跑任务（未选/未收录），跳过。", level="warn")
            return "skip"
        task = task_cls()
        if not getattr(task, "CHAINS_PER_WINDOW", False):
            wctx.log(f"「{title}」不支持每窗口独立链，跳过。", level="warn")
            return "skip"
        if ctx.task_cfg(eff_name).get("dry_run", True):
            wctx.log(f"「{title}」处于演练模式，一条龙不实跑它（到该任务页关掉「演练」再纳入），跳过。",
                     level="warn")
            return "skip"
        ok, probs = task.preflight(ctx)
        if not ok:
            wctx.log(f"跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return "skip"
        rec, step_fn = task.make_chain_driver(wctx)
        c["sub_rec"], c["sub_step"] = rec, step_fn
        self._any_real_run = True        # 真正开跑了（非演练）
        wctx.log(f"───── 开始「{title}」 ─────", level="hit")
        return "ready"

    def _run_collective(self, ctx, step_name):
        """集体跑一次「多人步」：组队 → 队长线性跑完（→可选解散）。用主 ctx（任务自带 select+组队）。
        阻塞执行，期间各队员号在屏障上待命（本就在队伍里被传送/自动战斗）。
        dungeon=把刷副本页勾选的全部副本按勾选顺序一个个刷完（每个副本 run() 自带组队）；
        zhuagui=抓鬼一次（自带组队）。"""
        if step_name == "dungeon":
            self._run_collective_dungeons(ctx)
            return
        title = self._title_of(step_name, ctx)
        eff_name, task_cls = self._resolve(step_name, ctx)
        if task_cls is None:
            ctx.log(f"「{title}」无可跑任务（未收录/未选），跳过该步。", level="warn")
            return
        task = task_cls()
        if ctx.task_cfg(eff_name).get("dry_run", True):
            ctx.log(f"「{title}」处于演练模式，一条龙不实跑，跳过该步。", level="warn")
            return
        ok, probs = task.preflight(ctx)
        if not ok:
            ctx.log(f"跳过「{title}」（未就绪）：" + "；".join(probs), level="warn")
            return
        ctx.log(f"───── 所有号已汇合，开始集体「{title}」（组队 → 队长跑）─────", level="hit")
        try:
            self._any_real_run = True
            task.run(ctx)
        except Exception as e:
            ctx.log(f"「{title}」运行异常：{e}，继续后续任务。", level="error")
            return
        if not ctx.should_stop():
            ctx.log(f"───── 「{title}」完成 ─────", level="hit")

    def _run_collective_dungeons(self, ctx):
        """「刷副本」集体步：把刷副本页勾选的副本按勾选顺序逐一刷完（每个副本 run() 自带组队）。
        某副本演练/未就绪/异常 → 跳过继续下一个，最后汇总（行为与「刷副本」页顺序队列一致）。"""
        names = self._selected_dungeons(ctx)
        if not names:
            ctx.log("「刷副本」无可跑副本（未选/未收录），跳过该步。", level="warn")
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
            if tc.get("dry_run", True):
                ctx.log(f"「{title}」处于演练模式，一条龙不实跑，跳过。", level="warn")
                continue
            task = cls()
            ok, probs = task.preflight(ctx)
            if not ok:
                ctx.log(f"跳过副本「{title}」（未就绪）：" + "；".join(probs), level="warn")
                continue
            ctx.log(f"─── 所有号已汇合，集体刷副本 {i}/{total}「{title}」（组队 → 队长跑）───", level="hit")
            try:
                self._any_real_run = True
                task.run(ctx)
            except Exception as e:
                ctx.log(f"「{title}」运行异常：{e}，继续下一个副本。", level="error")
                continue
            if ctx.should_stop():
                return
            done += 1
            if done < total:
                ctx.log(f"─── 副本 {i}/{total}「{title}」完成，接下一个副本 ───", level="hit")
        ctx.log(f"刷副本汇总：{done}/{total} 个副本完成。", level="hit" if done == total else "warn")

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
        c = get_task(name)
        return c.title if c else name

    @staticmethod
    def _selected_dungeons(ctx):
        """读副本中枢勾选的【全部】有效副本名（tasks.dungeon.selected 是列表，按勾选顺序）；
        空/非法则退回首个已收录副本（保证至少能跑一个）。"""
        names = [c.name for c in dungeon_tasks()]
        sel = ctx.task_cfg("dungeon").get("selected")
        if isinstance(sel, str):
            sel = [sel]
        if isinstance(sel, list):
            out = [s for s in sel if s in names]
            if out:
                return out
        return names[:1]

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
        """返回【按存储顺序】、已勾选且可串联的任务名列表。
        组级开关（tasks.daily.group_enabled，{single: bool, multi: bool}，缺省 True）也参与过滤：
        整个组被关掉时，该组全部任务不进一条龙（组内行级 enabled 独立保留，重开整组即恢复）。"""
        tc = ctx.task_cfg(self.name)
        ge = tc.get("group_enabled") or {}
        def group_on(name):
            g = GROUP_OF.get(name)
            return ge.get(g, True) if g else True
        out = []
        for step in tc.get("steps", []):
            if not isinstance(step, dict):
                continue
            name = step.get("task")
            if (step.get("enabled") and group_on(name)
                    and name in CHAINABLE and name not in out):
                out.append(name)
        return out

    def _time_limit(self, ctx):
        tc = ctx.task_cfg(self.name)
        try:
            return float(tc.get("loop", {}).get("time_limit_min", 0) or 0)
        except (TypeError, ValueError):
            return 0.0
