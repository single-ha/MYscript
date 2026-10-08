# -*- coding: utf-8 -*-
"""
登录游戏：按账号库里【已挑选的有序账号】逐号起一个客户端进程走完登录。

流程（每个号各起一次客户端，user 2026-09-29 拍板）：
    启动客户端 → 等它的窗口出现（按 hwnd 差集认「刚弹出来那个」，绝不碰已开好的号）
    → 点「用户」→ 点「切换账号」→ 点「选择账号」标签页
    → 在账号列表区滚轮翻找该号的账号卡片模板并点它 → 点「登录」→ 点「进入游戏」→ 等进游戏完成。

与其它任务的三处刻意不同（别照抄到别的任务）：
  · ENSURE_MAIN_ON_START=False：启动时游戏还没开，基类那步「先带回主界面」只会空转/误关界面。
  · POPUP_GUARD_OFF=True：登录/切号这些界面本身就带关闭按钮，弹窗守卫会把界面当公告弹窗点掉。
  · 账号【不在本任务的标定向导里】：账号库（最多 10 个账号图、可挑 5 个）由「日常」页登录区的
    「账号库」弹窗维护（core/account_history.py 存图与列表，ui/account_gallery.py 画面），
    本任务只读「挑好的有序队列」+ 按账号名去取对应槽的模板图。

preflight 不要求桌面上已有游戏窗口（号是本任务自己开的），但要求：客户端路径存在、五个流程模板
与账号列表区已标、队列里每个账号的图真实存在。
"""

import os
import subprocess
import time

from ..core import account_history as ah
from ..core import scan
from ..core import vision
from ..core import window as win_mod
from ..core.config import (LOGIN_FLOW_TPL_KEYS, LOGIN_FLOW_TPL_LABELS, LOGIN_MAX_ACCOUNTS,
                           LOGIN_REQUIRED_REGION)
from .base import Task, register

_NS = "login"


@register
class LoginTask(Task):
    name = _NS
    title = "登录游戏"
    description = "按所选账号顺序逐个启动客户端：用户→切换账号→选号→登录→进入游戏"
    ENSURE_MAIN_ON_START = False    # 启动时游戏还没开，跳过基类「先带回主界面」
    POPUP_GUARD_OFF = True         # 登录/切号界面自带关闭钮，弹窗守卫会把界面点掉

    CALIBRATION = {
        "regions": [
            (LOGIN_REQUIRED_REGION, "账号列表区域",
             "「选择账号」界面里那片账号列表（滚轮在此翻找账号卡片）。建议只框列表本身，"
             "框太宽会连带滚到别处。"),
        ],
        "templates": [
            ("user_menu", "「用户」菜单",
             "客户端主界面上的「用户」菜单入口（点它弹出切号菜单）。"),
            ("switch_account", "「切换账号」",
             "「用户」菜单展开后里面的「切换账号」。"),
            ("select_account_tab", "「选择账号」标签页",
             "切号界面里的「选择账号」标签页（点它才翻账号列表）。"),
            ("login", "「登录」按钮",
             "选好账号之后要点的「登录」按钮。"),
            ("enter_game", "「进入游戏」按钮",
             "登录后选区里最后要点的「进入游戏」按钮。"),
        ],
        "watchlist": False,
    }

    # ------------------------------------------------------------------
    # 登录队列（账号库里挑好的、有序的槽位号）
    # ------------------------------------------------------------------
    @staticmethod
    def selected_slots(cfg):
        """登录队列 = 有序账号槽位号（读账号库那一份，见 core/account_history.get_selection）。"""
        return ah.get_selection(cfg)

    @staticmethod
    def slot_name(cfg, slot):
        """槽位号 -> 账号名（只用于日志，日志里显示人看得懂的名字而不是「槽3」）。"""
        return ah.get_names(cfg).get(int(slot), "槽%d" % (int(slot) + 1))

    def preflight(self, ctx):
        problems = []
        tc = ctx.task_cfg(_NS)
        client = (tc.get("client_path") or "").strip()
        if not client:
            problems.append("没设置客户端路径 —— 请到「任务配置」页「登录游戏」卡点「浏览」选客户端 exe")
        elif not os.path.isfile(client):
            problems.append(f"客户端路径不存在：{client} —— 请到「任务配置」页「登录游戏」卡重新选择")
        slots = self.selected_slots(ctx.cfg)
        if not slots:
            problems.append("账号库里没挑要登录的号 —— 请在「日常」页顶部「登录游戏」区点「账号库」"
                            f"，从库里挑最多 {LOGIN_MAX_ACCOUNTS} 个号（按挑选顺序依次登录）")
        for s in slots:
            if not ah.slot_exists(s):
                problems.append(f"账号「{self.slot_name(ctx.cfg, s)}」的图已丢失（槽位 {s}）—— "
                                "请到账号库里重新标定它")
        regions = tc.get("regions") or {}
        if not regions.get(LOGIN_REQUIRED_REGION):
            problems.append("区域『账号列表区域』未标定 —— 请到「任务配置」页「登录游戏」卡点「标定」框选")
        templates = tc.get("templates") or {}
        for key in LOGIN_FLOW_TPL_KEYS:
            if not templates.get(key):
                problems.append(f"模板『{LOGIN_FLOW_TPL_LABELS[key]}』未标定 —— "
                                "请到「任务配置」页「登录游戏」卡点「标定」框选")
        return (len(problems) == 0), problems

    # ------------------------------------------------------------------
    def _run(self, ctx):
        tc = ctx.task_cfg(_NS)
        loop = tc.get("loop") or {}
        client = (tc.get("client_path") or "").strip()
        args = [a for a in str(tc.get("client_args") or "").split() if a]
        slots = self.selected_slots(ctx.cfg)
        names = {s: self.slot_name(ctx.cfg, s) for s in slots}
        threshold = float(loop.get("match_threshold", 0.85))
        templates = tc.get("templates") or {}
        flags = {k: (vision.load_template(templates.get(k)) if templates.get(k) else None)
                 for k in LOGIN_FLOW_TPL_KEYS}
        acc_flags = {s: vision.load_template(ah.slot_rel(s)) for s in slots}
        list_region = (tc.get("regions") or {}).get(LOGIN_REQUIRED_REGION)

        if args:
            ctx.log(f"启动参数：{' '.join(args)}")
        ctx.log("开始登录：%d 个号（%s）" % (len(slots), " → ".join(names[s] for s in slots)),
                level="hit")

        done, failed = [], []
        for i, slot in enumerate(slots):
            if ctx.should_stop():
                break
            if i > 0:
                self._interruptible_sleep(ctx, self._jitter(float(loop.get("client_between_sec", 3.0)), ctx))
            try:
                ok = self._login_one(ctx, slot, names[slot], client, args, loop, threshold,
                                     flags, acc_flags.get(slot), list_region)
            except Exception as e:
                ctx.log(f"{names[slot]} 登录异常，跳过该号（其余号继续）：{e}", level="error")
                ok = False
            (done if ok else failed).append(slot)

        ctx.log("★ 登录结束：成功 %d 个%s。" % (
            len(done), ("（%s）" % "、".join(names[s] for s in done)) if done else ""), level="hit")
        if failed:
            ctx.log("未完成 %d 个（%s）：见上面各号的原因。" % (
                len(failed), "、".join(names[s] for s in failed)), level="warn")
        if len(done) > 1 and not ((ctx.cfg.get("targets") or {}).get("multi")):
            ctx.log("提示：当前是单开模式，只会用第一个窗口；多个号要一起跑请到「通用」页选多开"
                    "（多开勾选留空=自动跟踪全部窗口，新开的号会自动纳入）。", level="warn")

    # ------------------------------------------------------------------
    # 单号流程
    # ------------------------------------------------------------------
    def _login_one(self, ctx, slot, name, client, args, loop, threshold, flags, acc_tpl, list_region):
        """起一个客户端、走完切号登录、进游戏。成功 True / 失败 False。"""
        ctx.log(f"{name}：启动客户端…", level="hit")
        before = win_mod.snapshot_hwnds()
        try:
            subprocess.Popen([client] + args, cwd=os.path.dirname(client) or None)
        except Exception as e:
            ctx.log(f"启动客户端失败（{client}）：{e}", level="error")
            return False
        win = win_mod.wait_new_game_window(
            before, ctx.cfg.get("window_title", "梦幻西游"), ctx.cfg.get("window_offset", [0, 0]),
            timeout=float(loop.get("client_launch_timeout_sec", 180)),
            accept_any=bool(loop.get("accept_any_new_window", True)),
            should_stop=ctx.should_stop, sleep=lambda s: self._interruptible_sleep(ctx, s))
        if win is None:
            if ctx.should_stop():
                ctx.log("已停止。", level="warn")
            else:
                ctx.log("启动客户端后等不到它的窗口（超时）。请确认：①路径能正常打开游戏；"
                        "②该客户端没在运行（已在运行时再启动往往只把旧窗口调到前台、不会新开窗口）；"
                        "③「通用」页的窗口标题/进程名与客户端实际值是否对得上。", level="error")
            return False
        rect = win.rect()
        if rect is None:
            ctx.log("刚启动的窗口立刻失效（被关掉了？）。", level="error")
            return False
        # 绑定到这个号：日志带「[账号名] 」前缀，且鼠标/日志/停止信号与主 ctx 共用
        wctx = ctx.make_child(win, name)
        wctx.log(f"客户端窗口已出现（{rect[2]}×{rect[3]}）。", level="hit")

        step_t = float(loop.get("step_timeout_sec", 30))
        poll = float(loop.get("poll_sec", 0.3))
        settle = float(loop.get("settle_sec", 0.6))
        for key in ("user_menu", "switch_account", "select_account_tab"):
            if not self._wait_click(wctx, flags.get(key), LOGIN_FLOW_TPL_LABELS[key],
                                    threshold, step_t, poll, settle):
                wctx.log(f"{LOGIN_FLOW_TPL_LABELS[key]}一直没出现/点不到，{name} 跳过。", level="warn")
                return False
        if not self._pick_account(wctx, name, acc_tpl, threshold, loop, list_region):
            wctx.log(f"账号列表里翻完整段也没认出 {name} 的卡片（该账号的图标错了/卡片样子变了），"
                     "该号跳过。", level="warn")
            return False
        if not self._wait_click(wctx, flags.get("login"), LOGIN_FLOW_TPL_LABELS["login"],
                                threshold, step_t, poll, settle):
            wctx.log(f"{LOGIN_FLOW_TPL_LABELS['login']}没出现/点不到，{name} 跳过。", level="warn")
            return False
        # 「进入游戏」在选区/服列表之后，等得比前面几步久一点
        if not self._wait_click(wctx, flags.get("enter_game"), LOGIN_FLOW_TPL_LABELS["enter_game"],
                                threshold, max(step_t, 60.0), poll, settle):
            wctx.log(f"{LOGIN_FLOW_TPL_LABELS['enter_game']}没出现/点不到，{name} 跳过。", level="warn")
            return False
        return self._wait_entered(wctx, loop)

    # ------------------------------------------------------------------
    # 各步小工具
    # ------------------------------------------------------------------
    def _wait_click(self, ctx, tpl, label, threshold, timeout, poll, settle):
        """盯一个模板：出现即先确保前台再拟人点它，点完等落定返回 True；超时/被停返回 False。

        点前必查前台（铁律：绝不在后台窗口点——点击会被吞或落到别的窗口上）；切前台失败
        宁可不点、等下一轮，绝不盲点。"""
        if tpl is None:
            return False
        end = time.time() + max(0.5, timeout)
        while not ctx.should_stop() and time.time() < end:
            rect = ctx.window.rect()
            scene = ctx.window.grab_screen(rect) if rect is not None else None
            if scene is not None:
                m = vision.match(scene, tpl, threshold)
                if m is not None:
                    x, y = rect[0] + m[0], rect[1] + m[1]
                    if ctx.window.is_foreground() or ctx.window.activate():
                        ctx.mouse.human_move(x, y)
                        ctx.mouse.click(x, y)
                        ctx.log(f"点「{label}」（{m[2]:.3f}）。", level="hit")
                        self._interruptible_sleep(ctx, settle)
                        return True
                    ctx.log(f"切前台失败，不点「{label}」（宁缺勿错，等下一轮）。", level="warn")
            self._interruptible_sleep(ctx, poll)
        return False

    def _pick_account(self, ctx, name, acc_tpl, threshold, loop, list_region):
        """在「选择账号」的账号列表区滚轮翻找该账号的卡片模板，找到就点它。返回是否点到。

        走 core/scan.scroll_search：先滚到顶再向下逐屏找，帧差判「滚到底」才算翻完整段——
        账号多/列表长都不漏（不靠固定翻屏数猜）。"""
        if acc_tpl is None:
            ctx.log(f"{name} 的账号图读不出来（图片丢失？），无法定位。", level="warn")
            return False

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region) if list_region
                    else ctx.window.rect())

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None
            m = vision.match(scene, acc_tpl, threshold)
            if m is None:
                return scan.SCROLL, None
            x, y = rect[0] + m[0], rect[1] + m[1]
            if not (ctx.window.is_foreground() or ctx.window.activate()):
                ctx.log("切前台失败，不点账号卡片（宁缺勿错，原地重试）。", level="warn")
                return scan.STAY, None
            ctx.mouse.human_move(x, y)
            ctx.mouse.click(x, y)
            ctx.log(f"在账号列表里点到 {name}（{m[2]:.3f}）。", level="hit")
            return scan.ACCEPT, (x, y, m[2])

        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, s),
            scroll_step=int(loop.get("scroll_step", -3)),
            max_tries=int(loop.get("scroll_max_tries", 10)),
            settle_sec=float(loop.get("scroll_settle_sec", 0.35)),
            reset_to_top=bool(loop.get("scroll_reset_top", True)),
            end_diff=float(loop.get("scroll_end_diff", 2.0)),
            reset_max=int(loop.get("scroll_reset_max", 20)),
            grab_fn=ctx.window.grab_screen,
            log=lambda m: ctx.log(m), label="账号列表")
        return res.found

    def _wait_entered(self, ctx, loop):
        """点完「进入游戏」后等这个号真正进游戏。

        以主界面判定（通用页公共标定里的「商城图标」，见 ui/ui_state.is_main_screen）为准；
        没标商城图标判不了（返回 None）就盲等 enter_game_settle_sec 收尾，不占着整条龙；
        到 enter_game_wait_sec 上限仍未判到=按未完成处理。"""
        from ..ui import ui_state
        end = time.time() + max(1.0, float(loop.get("enter_game_wait_sec", 300)))
        blind_until = time.time() + max(0.5, float(loop.get("enter_game_settle_sec", 8.0)))
        while not ctx.should_stop() and time.time() < end:
            st = ui_state.is_main_screen(ctx.cfg, ctx.window)
            if st is True:
                ctx.log("已进入游戏主界面，本号完成。", level="hit")
                return True
            if st is None and time.time() >= blind_until:
                ctx.log("未标定「商城图标」无法确认是否已进游戏，按固定等待收尾"
                        "（要精确判定请到「通用」页标定公共区域里的商城图标）。", level="warn")
                return True
            self._interruptible_sleep(ctx, 0.5)
        if ctx.should_stop():
            return False
        ctx.log("等进游戏超时，本号按未完成处理。", level="warn")
        return False

    # ------------------------------------------------------------------
    # 各步小工具
    # ------------------------------------------------------------------
    def _wait_click(self, ctx, tpl, label, threshold, timeout, poll, settle):
        """盯一个模板：出现即先确保前台再拟人点它，点完等落定返回 True；超时/被停返回 False。

        点前必查前台（铁律：绝不在后台窗口点——点击会被吞或落到别的窗口上）；切前台失败
        宁可不点、等下一轮，绝不盲点。"""
        if tpl is None:
            return False
        end = time.time() + max(0.5, timeout)
        while not ctx.should_stop() and time.time() < end:
            rect = ctx.window.rect()
            scene = ctx.window.grab_screen(rect) if rect is not None else None
            if scene is not None:
                m = vision.match(scene, tpl, threshold)
                if m is not None:
                    x, y = rect[0] + m[0], rect[1] + m[1]
                    if ctx.window.is_foreground() or ctx.window.activate():
                        ctx.mouse.human_move(x, y)
                        ctx.mouse.click(x, y)
                        ctx.log(f"点「{label}」（{m[2]:.3f}）。", level="hit")
                        self._interruptible_sleep(ctx, settle)
                        return True
                    ctx.log(f"切前台失败，不点「{label}」（宁缺勿错，等下一轮）。", level="warn")
            self._interruptible_sleep(ctx, poll)
        return False

    def _pick_account(self, ctx, slot, acc_tpl, threshold, loop, list_region):
        """在「选择账号」的账号列表区滚轮翻找该号的卡片模板，找到就点它。返回是否点到。

        走 core/scan.scroll_search：先滚到顶再向下逐屏找，帧差判「滚到底」才算翻完整段——
        账号多/列表长都不漏（不靠固定翻屏数猜）。"""
        if acc_tpl is None:
            ctx.log(f"账号{slot} 的卡片模板没标，无法定位。", level="warn")
            return False

        def grab_rect():
            return (ctx.window.region_to_screen_rect(list_region) if list_region
                    else ctx.window.rect())

        def probe(scene, rect):
            if scene is None:
                return scan.SCROLL, None
            m = vision.match(scene, acc_tpl, threshold)
            if m is None:
                return scan.SCROLL, None
            x, y = rect[0] + m[0], rect[1] + m[1]
            if not (ctx.window.is_foreground() or ctx.window.activate()):
                ctx.log("切前台失败，不点账号卡片（宁缺勿错，原地重试）。", level="warn")
                return scan.STAY, None
            ctx.mouse.human_move(x, y)
            ctx.mouse.click(x, y)
            ctx.log(f"在账号列表里点到账号{slot}（{m[2]:.3f}）。", level="hit")
            return scan.ACCEPT, (x, y, m[2])

        res = scan.scroll_search(
            grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
            should_stop=ctx.should_stop,
            sleep=lambda s: self._interruptible_sleep(ctx, s),
            scroll_step=int(loop.get("scroll_step", -3)),
            max_tries=int(loop.get("scroll_max_tries", 10)),
            settle_sec=float(loop.get("scroll_settle_sec", 0.35)),
            reset_to_top=bool(loop.get("scroll_reset_top", True)),
            end_diff=float(loop.get("scroll_end_diff", 2.0)),
            reset_max=int(loop.get("scroll_reset_max", 20)),
            grab_fn=ctx.window.grab_screen,
            log=lambda m: ctx.log(m), label="账号列表")
        return res.found

    def _wait_entered(self, ctx, loop):
        """点完「进入游戏」后等这个号真正进游戏。

        以主界面判定（通用页公共标定里的「商城图标」，见 ui/ui_state.is_main_screen）为准；
        没标商城图标判不了（返回 None）就盲等 enter_game_settle_sec 收尾，不占着整条龙；
        到 enter_game_wait_sec 上限仍未判到=按未完成处理。"""
        from ..ui import ui_state
        end = time.time() + max(1.0, float(loop.get("enter_game_wait_sec", 300)))
        blind_until = time.time() + max(0.5, float(loop.get("enter_game_settle_sec", 8.0)))
        while not ctx.should_stop() and time.time() < end:
            st = ui_state.is_main_screen(ctx.cfg, ctx.window)
            if st is True:
                ctx.log("已进入游戏主界面，本号完成。", level="hit")
                return True
            if st is None and time.time() >= blind_until:
                ctx.log("未标定「商城图标」无法确认是否已进游戏，按固定等待收尾"
                        "（要精确判定请到「通用」页标定公共区域里的商城图标）。", level="warn")
                return True
            self._interruptible_sleep(ctx, 0.5)
        if ctx.should_stop():
            return False
        ctx.log("等进游戏超时，本号按未完成处理。", level="warn")
        return False
