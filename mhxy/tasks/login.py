# -*- coding: utf-8 -*-
"""
登录游戏：按账号库里【已挑选的有序账号】逐号起一个客户端进程走完登录。

流程（每个号各起一次客户端，user 2026-09-29 拍板；顺序 2026-10-09 按用户实测改版）：
    启动客户端 → 等它的窗口出现（按 hwnd 差集认「刚弹出来那个」，绝不碰已开好的号）
    → 先调整到基准尺寸 targets.base_size（模板/区域坐标按它标定，各号同尺寸才好点）
    → 点「切换账号」（直连入口）→ 在账号列表区滚轮翻找该号的账号卡片模板并点它
    → 点「进入游戏」→ 点「更换角色」→ 等「已有角色」标签：1 秒（loop.switch_role_tab_wait_sec）
    内没出现就再点一次「更换角色」，最多 loop.switch_role_retry_max 次 → 点该号的角色卡片
    → 等进游戏完成：主界面（商城图标）判定到即放行；判不到则边清挡图标弹窗边等，
      enter_game_settle_sec 兜底带截图放行（见 _wait_entered），单个号绝不卡死整条链。

与其它任务的三处刻意不同（别照抄到别的任务）：
  · ENSURE_MAIN_ON_START=False：启动时游戏还没开，基类那步「先带回主界面」只会空转/误关界面。
  · POPUP_GUARD_OFF=True：登录/切号这些界面本身就带关闭按钮，弹窗守卫会把界面当公告弹窗点掉。
  · 账号【不在本任务的标定向导里】：账号库（每号两张图：账号卡片 + 角色卡片，最多 10 个账号、
    可挑 5 个）由「日常」页登录区的「账号库」弹窗维护（core/account_history.py 存图与列表，
    ui/account_gallery.py 画面），本任务只读「挑好的有序队列」+ 按账号名去取对应槽的两张图。

preflight 不要求桌面上已有游戏窗口（号是本任务自己开的），但要求：客户端路径存在、四个流程模板
（切换账号/进入游戏/更换角色/已有角色标签）与账号列表区已标、队列里每个账号的账号卡**和角色卡**
图都真实存在。
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
    description = "按所选账号顺序逐个启动客户端：切换账号→选号→进入游戏→更换角色→选角色→进主界面"
    ENSURE_MAIN_ON_START = False    # 启动时游戏还没开，跳过基类「先带回主界面」
    POPUP_GUARD_OFF = True         # 登录/切号界面自带关闭钮，弹窗守卫会把界面点掉

    CALIBRATION = {
        "regions": [
            (LOGIN_REQUIRED_REGION, "账号列表区域",
             "点「切换账号」后那片账号列表（滚轮在此翻找账号卡片）。建议只框列表本身，"
             "框太宽会连带滚到别处。"),
        ],
        "templates": [
            ("switch_account", "「切换账号」",
             "客户端界面上的「切换账号」（登录流程第一步，直连入口，不再经「用户」菜单）。"),
            ("enter_game", "「进入游戏」",
             "选好该号账号卡片之后要点的「进入游戏」。"),
            ("switch_role", "「更换角色」按钮",
             "点「进入游戏」后角色界面里的「更换角色」按钮（点它打开角色列表）。"),
            ("existing_role_tab", "「已有角色」标签",
             "角色列表里的「已有角色」标签页（点它才显示已有角色卡）。"),
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
            if not ah.char_slot_exists(s):
                problems.append(f"账号「{self.slot_name(ctx.cfg, s)}」的角色卡图未标定（槽位 {s}）—— "
                                "登录流程最后要「选择角色」，请到账号库里点它的「标角色」框选角色卡")
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
        threshold = float(loop.get("match_threshold", 0.98))
        templates = tc.get("templates") or {}
        flags = {k: (vision.load_template(templates.get(k)) if templates.get(k) else None)
                 for k in LOGIN_FLOW_TPL_KEYS}
        acc_flags = {s: vision.load_template(ah.slot_rel(s)) for s in slots}
        char_flags = {s: vision.load_template(ah.char_slot_rel(s)) for s in slots}
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
                                     flags, acc_flags.get(slot), char_flags.get(slot),
                                     list_region)
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
    def _login_one(self, ctx, slot, name, client, args, loop, threshold, flags,
                   acc_tpl, char_tpl, list_region):
        """起一个客户端、走完切号登录、进游戏。成功 True / 失败 False。

        流程（2026-10-09 用户实测改版）：窗口出现 → 先调基准尺寸（targets.base_size）
        → 切换账号 → 翻账号卡并点 → 进入游戏 → 更换角色（1 秒没见「已有角色」标签就再点一次，
        见 _role_tab_step）→ 点该号角色卡。"""
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

        # 先调整到基准尺寸再走登录：模板/区域坐标都按 targets.base_size 标定，各号窗口同尺寸
        # 才点得准（多开同尺寸铁律）。只调尺寸不动位置——多开排布归「通用/日常」页的「调整窗口」。
        base = ((ctx.cfg or {}).get("targets") or {}).get("base_size")
        if base and len(base) >= 2:
            bw, bh = int(base[0]), int(base[1])
            if wctx.window.activate():
                if wctx.window.resize_to(bw, bh):
                    self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
            r2 = wctx.window.rect()
            if r2 and abs(r2[2] - bw) <= 4 and abs(r2[3] - bh) <= 4:
                wctx.log(f"已调整窗口到基准尺寸 {bw}×{bh}（实际 {r2[2]}×{r2[3]}）。", level="hit")
            else:
                sz = f"（实际 {r2[2]}×{r2[3]}）" if r2 else "（窗口已失效）"
                wctx.log(f"调整窗口到基准尺寸 {bw}×{bh} 未完全生效{sz}——多半锁了分辨率档位，"
                         "界面大小与本机标定不一致可能会点偏，建议检查窗口分辨率。", level="warn")
        else:
            wctx.log("未设置基准尺寸（targets.base_size），跳过窗口调整，照常登录"
                     "（建议先在「通用」页设基准尺寸，模板/区域坐标是按它标定的）。", level="warn")

        step_t = float(loop.get("step_timeout_sec", 30))
        poll = float(loop.get("poll_sec", 0.3))
        settle = float(loop.get("settle_sec", 0.6))

        def _step(key, extra=False):
            label = LOGIN_FLOW_TPL_LABELS[key]
            if not self._wait_click(wctx, flags.get(key), label, threshold,
                                    max(step_t, 60.0) if extra else step_t, poll, settle):
                wctx.log(f"{label}一直没出现/点不到，{name} 跳过。", level="warn")
                return False
            return True

        if not _step("switch_account"):
            return False
        if not self._pick_account(wctx, name, acc_tpl, threshold, loop, list_region):
            wctx.log(f"账号列表里翻完整段也没认出 {name} 的账号卡片（该账号的图错了/卡片样子变了），"
                     "该号跳过。", level="warn")
            return False
        # 选完账号后「进入游戏」在选区/服列表之后，等得比前面几步久一点
        if not _step("enter_game", extra=True):
            return False
        if not _step("switch_role"):
            return False
        if not self._role_tab_step(wctx, flags, threshold, loop, step_t, poll, settle):
            wctx.log(f"「{LOGIN_FLOW_TPL_LABELS['existing_role_tab']}」一直没出现，{name} 跳过。", "warn")
            return False
        if not self._wait_click(wctx, char_tpl, f"{name}的角色卡", threshold, step_t, poll, settle):
            wctx.log(f"「已有角色」列表里没找到 {name} 的角色卡（角色卡图没标或样子变了），"
                     "该号跳过。", level="warn")
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

    def _wait_present(self, ctx, tpl, threshold, timeout, poll):
        """只等不点：timeout 内轮询抓图，模板出现返回 True（超时/被停返回 False）。"""
        if tpl is None:
            return False
        end = time.time() + max(0.3, timeout)
        while not ctx.should_stop() and time.time() < end:
            rect = ctx.window.rect()
            scene = ctx.window.grab_screen(rect) if rect is not None else None
            if scene is not None and vision.match(scene, tpl, threshold) is not None:
                return True
            self._interruptible_sleep(ctx, poll)
        return False

    def _role_tab_step(self, ctx, flags, threshold, loop, step_t, poll, settle):
        """点完「更换角色」后等「已有角色」标签、出现即点它切入已有角色列表；等不到就再点一次「更换角色」。

        实测点一次「更换角色」有时不弹角色列表（标签不出现）。按 user 2026-10-09 要求：
        每次点击后等 switch_role_tab_wait_sec（默认 1 秒）看标签，没出现=过一秒再点一次
        「更换角色」，最多 switch_role_retry_max 次（含 `_step("switch_role")` 的首次）；
        次数用完后最后一次点击再给一次 step_timeout_sec 的完整等待（标签可能慢半拍）。
        ⚠ 标签出现≠已切换：必须再点它一下才显示「已有角色」列表，否则后面找角色卡会扑空
        （曾只等不点、角色卡一直找不到，见 user 2026-10-09 实测反馈）。
        返回「已有角色」标签是否点到。"""
        sw, tab = flags.get("switch_role"), flags.get("existing_role_tab")
        if tab is None or sw is None:
            ctx.log("「更换角色」或「已有角色」模板未标定，无法走选角色这步——"
                    "请到「任务配置」页「登录游戏」卡点「标定」补上。", level="warn")
            return False
        wait = max(0.3, float(loop.get("switch_role_tab_wait_sec", 1.0)))
        tries = max(1, int(loop.get("switch_role_retry_max", 5)))
        sw_label = LOGIN_FLOW_TPL_LABELS["switch_role"]
        tab_label = LOGIN_FLOW_TPL_LABELS["existing_role_tab"]

        def _click_tab():
            return self._wait_click(ctx, tab, tab_label, threshold, step_t, poll, settle)

        if self._wait_present(ctx, tab, threshold, wait, poll):
            return _click_tab()
        for n in range(2, tries + 1):
            if ctx.should_stop():
                return False
            ctx.log(f"{wait:g} 秒内没出现「{tab_label}」，再点一次「{sw_label}」（{n}/{tries}）。",
                    level="warn")
            if not self._wait_click(ctx, sw, sw_label, threshold, step_t, poll, settle):
                ctx.log(f"「{sw_label}」按钮点不到，本号跳过。", level="warn")
                return False
            if self._wait_present(ctx, tab, threshold, wait, poll):
                return _click_tab()
        if ctx.should_stop():
            return False
        if not self._wait_present(ctx, tab, threshold, step_t, poll):
            return False
        return _click_tab()

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
        """点完角色卡后等这个号真正进游戏。

        以主界面判定（通用页公共标定里的商城图标，见 ui_state.is_main_screen）为准：
          · True  → 已进入主界面，立即放行（下一个号继续登录）；
          · None  → 未标定图标/抓图失败判不了，到 enter_game_settle_sec 兜底按完成收尾（向后兼容）；
          · False → 画面能抓、图标已标定却没匹配上（最常见：进游戏后公告/活动弹窗盖住商城图标）——
                    每轮先做受限弹窗关闭（_close_login_popup），到兜底时间仍 False 就存现场截图 + warn
                    按已进入放行。⚠ 绝不 300 秒死等这个单一信号：曾因弹出挡住图标导致整套登录链
                    卡在「账号1 已完成但账号2 迟迟不开」（user 2026-10-09 反馈）。"""
        from ..ui import ui_state
        wait_sec = max(1.0, float(loop.get("enter_game_wait_sec", 300)))
        settle_sec = max(0.5, float(loop.get("enter_game_settle_sec", 8.0)))
        end = time.time() + wait_sec
        blind_until = time.time() + settle_sec
        while not ctx.should_stop() and time.time() < end:
            st = ui_state.is_main_screen(ctx.cfg, ctx.window)
            if st is True:
                ctx.log("已进入游戏主界面，本号完成。", level="hit")
                return True
            if st is None:
                if time.time() >= blind_until:
                    ctx.log("未标定「商城图标」无法确认是否已进游戏，按固定等待收尾"
                            "（要精确判定请到「通用」页标定公共区域里的商城图标）。", level="warn")
                    return True
            else:  # False：图标已标定、画面能抓到但没匹配上
                self._close_login_popup(ctx)
                if time.time() >= blind_until:
                    self._warn_entered_unconfirmed(ctx)
                    return True
            self._interruptible_sleep(ctx, 0.5)
        if ctx.should_stop():
            return False
        ctx.log("等进游戏超时，本号按未完成处理。", level="warn")
        return False

    def _close_login_popup(self, ctx):
        """登录「等进游戏」阶段的受限弹窗关闭：在前台号上命中 popup_close 就拟人点掉。

        全局守卫对 LoginTask 是挂起的（POPUP_GUARD_OFF=True，因为切号/选号界面自带「×」，
        怕把界面本身当弹窗点掉）；但到了「等进游戏」这个阶段画面必然是「进入中/主界面」，
        此时清掉挡画面的公告/活动弹窗既安全又有必要（它能盖住商城图标导致 _wait_entered 误判）。
        只点「×」、不升级 Esc；只在前台窗口动作；节流 popup_guard.interval_sec；
        popup_close 未标定 / popup_guard.enabled=false 时静默跳过。"""
        cfg = ctx.cfg or {}
        pg = cfg.get("popup_guard") or {}
        if not pg.get("enabled", True):
            return
        window = ctx.window
        if window is None or window.rect() is None or not window.is_foreground():
            return
        now = time.time()
        if now - getattr(self, "_login_popup_last", 0.0) < float(pg.get("interval_sec", 2.0)):
            return
        self._login_popup_last = now
        from ..ui import ui_state
        point = ui_state.find_popup_close(cfg, window, float(pg.get("match_threshold", 0.85)))
        if point is None:
            return
        ctx.mouse.human_move(point[0], point[1])
        ctx.mouse.click(point[0], point[1])
        ctx.log("登录期弹窗守卫：点掉挡画面的公告/活动弹窗「×」。", level="hit")

    def _warn_entered_unconfirmed(self, ctx):
        """确认不到进游戏成功：存现场截图 + 商城图标对照分，供分辨「遮挡 vs 标定偏差」后放行。"""
        rect = ctx.window.rect()
        scene = ctx.window.grab_screen(rect) if rect is not None else None
        cap = self._save_capture(scene, "login_entered_unconfirmed") if scene is not None else None
        score = self._shop_icon_best_score(ctx, scene) if scene is not None else None
        sc = ("，商城图标对照分 %.3f（阈值 0.8）" % score) if score is not None else ""
        extra = ("（截图 %s）" % cap) if cap else ""
        ctx.log("进游戏后没确认到商城图标%s——多半被公告/活动弹窗挡住或图标样子变了，"
                "已按进入完成放行、继续下一个号。若图标确实在画面上却频繁出现此提示，"
                "请到「通用」页重校公共区域的商城图标。%s" % (sc, extra), level="warn")

    def _shop_icon_best_score(self, ctx, scene):
        """诊断用：商城图标在当前画面里的最高匹配分（未标定/取不到返回 None）。"""
        try:
            shared = ((ctx.cfg or {}).get("tasks", {}) or {}).get("shared", {}) or {}
            path = (shared.get("templates") or {}).get("shop_icon")
            tpl = vision.load_template(path)
            if tpl is None or scene is None:
                return None
            return float(vision.best_score(scene, tpl)[0])
        except Exception:
            return None