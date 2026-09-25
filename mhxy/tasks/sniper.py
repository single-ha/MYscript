# -*- coding: utf-8 -*-
"""
秒装备任务：循环刷新摆摊/市场列表，命中监控清单里的装备就立刻点它→购买→确认。

设计取舍（用户已拍板）：命中即抢，不做 OCR 比价（模板匹配认不了“任意低于X价”）。
所有点击走拟人化鼠标，间隔带抖动、偶尔走神。
"""

import time

from ..core import vision
from ..core import window as win_mod
from .base import Task, register


@register
class SniperTask(Task):
    name = "sniper"
    title = "秒装备"
    description = "盯市场列表，目标装备一出现就秒下单"

    # 秒装备停在商城/摆摊界面，开跑前绝不能 ESC 关面板回主界面（否则商城被关）；弹窗守卫同理——
    # 商城右上角自带「×」，会被当成弹窗点掉，一并挂起。
    ENSURE_MAIN_ON_START = False
    POPUP_GUARD_OFF = True

# 标定向导用：区域项即原 REGION_ITEMS；秒装备有「装备清单」卡片，无标志模板。
    CALIBRATION = {
        "regions": [
            ("listing", "货架/列表区域", "留空=整个窗口当检测区(推荐)；想提速/避免误识可框小一点", True),
            ("market_tab_region", "商城标签页区域", "摆摊界面顶部一横排页签所在的滚动区域；找「关注」页签时在这里滚动"),
        ],
        "templates": [
            ("market_tab", "摆摊页签", "点开商城后顶部的页签（如「摆摊」）；自动进商城的最后一步点它。"
             "不标=跳过这步（需手动已在摆摊页）"),
            ("focus_tab", "「关注」页签", "摆摊页签条里的「关注」；启动时在标签页区域滚动找到并点它"),
            ("buy_want", "「我要购买」按钮", "进入「关注」页后的「我要购买」按钮"),
            ("refresh_1", "「坊」标识", "刷新：依次点它 + 「摊」标识，不点条目进货架"),
            ("refresh_2", "「摊」标识", "刷新：先点「坊」标识之后点它"),
            ("buy_button", "购买按钮", "选中摊位后出现的「购买」按钮"),
            ("confirm_button", "确认购买按钮", "二次确认弹窗的按钮，没有可不标"),
        ],
        "watchlist": True,
    }

    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        regions = tc.get("regions", {})
        # listing 留空=整窗检测，不再强制标定
        if not tc.get("templates", {}).get("refresh_1"):
            problems.append("『「坊」标识』未标定 —— 刷新要靠它与「摊」标识")
        if not tc.get("templates", {}).get("refresh_2"):
            problems.append("『「摊」标识』未标定 —— 刷新要靠它与「坊」标识")
        watchlist = tc.get("watchlist", [])
        if not watchlist:
            problems.append("监控清单为空 —— 请先添加要抢的装备")
        for it in watchlist:
            if vision.load_template(it["template"]) is None:
                problems.append(f"模板图丢失：{it['template']}（{it.get('name','?')}）")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」）"
                            "，请先打开游戏并在「选择窗口」里选好")
        return (len(problems) == 0), problems

    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        dry_run = False

        templates = [(it, vision.load_template(it["template"])) for it in tc["watchlist"]]
        templates = [(it, tpl) for it, tpl in templates if tpl is not None]

        threshold = loop["match_threshold"]
        refresh_interval = loop["refresh_interval_sec"]
        cooldown = loop["after_buy_cooldown_sec"]
        # 命中后「下单那一下」用更激进的速度倍率（只在抢的瞬间生效，巡航点击仍保持常速拟人化）
        snipe_speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        listing = regions.get("listing")        # 空=整窗检测
        # 把每轮要用的参数打包，传给 _snipe_one_round（避免一长串形参）
        pkg = (loop, regions, listing, templates, threshold, cooldown, snipe_speed, dry_run)

        multi = ctx.cfg.get("targets", {}).get("multi", False)
        switch_delay = ctx.cfg.get("targets", {}).get("switch_delay_sec", 0.15)

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏窗口在前台时鼠标可能无法移动/点击（UIPI 拦截）。"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, multi)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return

        ctx.log(f"启动完成：{('多开轮转 ' + str(len(contexts)) + ' 个号') if multi else '单号'}，"
                f"监控 {len(templates)} 件装备，阈值 {threshold}，检测区："
                f"{'整窗' if not listing else '手动框选'}")
        ctx.log("演练模式（只识别不下单）" if dry_run else "★ 实战模式：命中会真正下单 ★",
                level="warn" if not dry_run else "info")

        # 自动进商城（取消「运行前必须手动打开商城」的限制）：每个号先回主界面再点商城图标。
        for wctx in contexts:
            if ctx.should_stop():
                break
            self._open_market(wctx, regions)

        rounds = 0
        while not ctx.should_stop():
            # 窗口可能被关/移动：多开时若有窗口失效就重新枚举选择
            contexts = self._ensure_contexts(ctx, contexts, multi)
            if not contexts:
                self._interruptible_sleep(ctx, 2.0)
                continue

            for wctx in contexts:
                if ctx.should_stop():
                    break
                if not self._prepare_window(wctx, multi):
                    continue
                wctx.mouse.maybe_idle()
                self._snipe_one_round(wctx, pkg, refresh_interval)
                # 多开：号与号之间留个小间隔，别太机械
                if multi and len(contexts) > 1:
                    self._interruptible_sleep(ctx, self._jitter(switch_delay, ctx))

            rounds += 1
            # 一整轮（所有号过一遍）之间留间隔（带抖动）
            self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))

        ctx.log(f"已停止。共循环 {rounds} 轮。")

    # ---- 多开轮转：窗口上下文的构建与维护 ----
    def _resolve_contexts(self, ctx, multi):
        """按选择把目标窗口包成「要轮转的上下文」列表。
        单开→复用主 ctx 并把它绑到选中的那个窗口；多开→每个号一个子上下文(带「号N」标签)。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        if multi:
            return [ctx.make_child(w, f"号{self._window_no(w, i)}") for i, w in enumerate(wins)]
        ctx.window = wins[0]        # 单开：直接操作选中的那个窗口（不再每轮 locate 选最大）
        return [ctx]

    def _ensure_contexts(self, ctx, contexts, multi):
        """每轮开头校验窗口是否还在；任一失效（被关/最小化）就重新枚举选择。"""
        if contexts and all(c.window.rect() is not None for c in contexts):
            return contexts
        fresh = self._resolve_contexts(ctx, multi)
        if not fresh:
            ctx.log("暂时没检测到目标窗口，等待…", level="warn")
        elif len(fresh) != len(contexts):
            ctx.log(f"目标窗口数变化：现 {len(fresh)} 个。", level="info")
        return fresh

    def _prepare_window(self, wctx, multi):
        """操作某个号前的准备：校验窗口有效，并把它切到前台，确保点击落在这个号身上。
        单开也要切（GUI 点「运行」时前台是脚本窗口，不切会点到脚本界面上）；切前台失败
        （被系统拒绝焦点抢占）就跳过该号本轮、下轮重试。"""
        if wctx.window.rect() is None:
            return False
        if not wctx.window.activate():
            return False
        if wctx.should_stop():
            return False
        return True

    def _snipe_one_round(self, ctx, pkg, refresh_interval):
        """对单个号跑「一轮」：重进货架 → 等加载 → 识别 → 命中下单。"""
        loop, regions, listing, templates, threshold, cooldown, snipe_speed, dry_run = pkg
        # 刷新 = 依次点 标定的「「坊」标识 →「摊」标识」（货架不会自动上新，必须这步重刷）。
        if not self._enter_shelf(ctx):
            self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
            return

        # 截检测区并匹配（listing 空=整窗）。自适应等加载：画面一静止就识别。
        list_rect = ctx.detection_rect(listing)
        if list_rect is None:
            self._interruptible_sleep(ctx, 0.5)
            return
        scene = self._wait_shelf_loaded(ctx, list_rect, loop)
        if scene is None:
            return

        for it, tpl in templates:
            if ctx.should_stop():
                break
            hit = vision.match(scene, tpl, threshold)
            if hit is None:
                continue
            cx, cy, score = hit
            screen_xy = (list_rect[0] + cx, list_rect[1] + cy)
            ctx.log(f"★ 命中【{it['name']}】相似度 {score:.3f} @ {screen_xy}", level="hit")
            shot = self._save_capture(scene, it["name"])
            ctx.log(f"  已存命中截图 captures/{shot}")

            if dry_run:
                ctx.log("  [演练] 不下单。确认无误后到设置里切换为实战。")
            else:
                self._buy_sequence(ctx, screen_xy, snipe_speed)
                ctx.log("  已执行购买动作序列（极速）。")
                self._interruptible_sleep(ctx, cooldown)
            break  # 一轮处理一件即可

    # ---- 内部小工具（_is_admin/_jitter/_frame_diff/_click_region/_save_capture/_interruptible_sleep 已上移 Task 基类）----
    def _open_market(self, ctx, regions):
        """自动进商城：先回主界面（ESC 关面板），再点「商城图标」进入商城——省去运行前手动开商城的一步。
        依赖共享标定「商城图标」(tasks.shared.templates.shop_icon)；未标定/模板缺失/屏幕上没识别到
        → 打日志说明并按现状继续（相当于仍要求手动停在商城界面），绝不瞎点。已在商城界面也会先回主界面
        再重新打开（页面上自带「×」，不做「是否已在商城」判定，保持逻辑简单可靠）。"""
        cfg = ctx.cfg or {}
        tpl_path = (cfg.get("tasks", {}) or {}).get("shared", {}).get("templates", {}).get("shop_icon")
        if not tpl_path:
            ctx.log("未标定「商城图标」(通用页·公共区域)，无法自动进商城——本次需手动停在商城界面再运行。",
                    level="warn")
            return False
        from ..ui import ui_state
        tpl_img = vision.load_template(tpl_path)
        if not ctx.window.activate():
            ctx.log("切前台失败，跳过自动进商城（本轮仍会重试切前台）。", level="warn")
            return False
        rect = ctx.window.rect()
        if tpl_img is None or rect is None:
            ctx.log("商城图标模板缺失或窗口失效，跳过自动进商城。", level="warn")
            return False
        ui_state.back_to_main_screen(cfg, ctx.window)      # ESC 逐层关面板回主界面
        self._interruptible_sleep(ctx, self._jitter(0.4, ctx))
        scene = win_mod.grab(rect)
        if scene is None:
            ctx.log("截图失败，跳过自动进商城。", level="warn")
            return False
        hit = vision.match(scene, tpl_img, 0.75)
        if hit is None:
            ctx.log("主界面上没识别到商城图标，跳过自动进商城（若误判可到「通用」页核对公共区域标定）。",
                    level="warn")
            return False
        cx, cy, score = hit
        sx, sy = rect[0] + cx, rect[1] + cy
        ctx.mouse.human_move(sx, sy)
        ctx.mouse.click(sx, sy)
        ctx.log(f"已自动进入商城 @ ({sx},{sy}) 相似度 {score:.3f}", level="info")
        self._interruptible_sleep(ctx, self._jitter(1.0, ctx))   # 等商城界面打开
        # 最后一步：点「摆摊」页签（可选，未标定=跳过，需确保已停在摆摊页）。
        self._click_market_tab(ctx)
        # 启动阶段进「关注」页：在标签页区域滚动找「关注」页签→点它→点「我要购买」。
        # 失败不阻断——本轮起每轮仍按「坊→摊」刷新跑，只是停留在普通摆摊列表。
        self._enter_focus_tab(ctx)
        return True

    def _scroll_tab_find(self, ctx, tab_key, label):
        """在「商城标签页区域」滚动查找某个页签模板：先向下翻、翻完没找到再向上翻。
        找到返回 (屏幕x, y, score)，未标定/模板缺失/翻完没有 → None。滚动只在标签页区域内进行。"""
        tc = ctx.task_cfg(self.name)
        tpl_path = (tc.get("templates") or {}).get(tab_key)
        tab_region = (tc.get("regions") or {}).get("market_tab_region")
        if not tpl_path or not tab_region:
            ctx.log(f"未标定「{label}」页签或「商城标签页区域」，跳过找页签。", level="warn")
            return None
        img = vision.load_template(tpl_path)
        if img is None:
            ctx.log(f"「{label}」页签模板缺失，跳过找页签。", level="warn")
            return None
        from ..core import scan

        def grab_rect():
            return ctx.window.region_to_screen_rect(tab_region)

        def probe(scene, rect):
            hit = vision.match(scene, img, 0.75)
            if hit is None:
                return scan.SCROLL, None
            return scan.ACCEPT, (rect[0] + hit[0], rect[1] + hit[1], hit[2])

        def sleep(sec):
            self._interruptible_sleep(ctx, self._jitter(sec, ctx))

        for step in (-3, 3):          # 先下后上，双向各翻一遍
            res = scan.scroll_search(
                grab_rect=grab_rect, probe=probe, mouse=ctx.mouse,
                should_stop=ctx.should_stop, sleep=sleep,
                scroll_step=step, max_tries=8, settle_sec=0.35,
                reset_to_top=False, label=f"标签页区域找「{label}」")
            if res.found:
                return res.payload
            if ctx.should_stop():
                return None
        return None

    def _enter_focus_tab(self, ctx):
        """启动阶段进「关注」页：滚动找「关注」页签 → 点它 → 点「我要购买」。
        任一步缺标定/找不到 → 诊断日志后跳过（本轮起仍按普通摆摊列表 + 每轮「坊→摊」刷新跑，不中断）。"""
        found = self._scroll_tab_find(ctx, "focus_tab", "关注")
        if found is None:
            ctx.log("未找到「关注」页签，跳过进关注页（仍按普通摆摊列表跑）。", level="warn")
            return False
        fx, fy, score = found
        ctx.mouse.human_move(fx, fy)
        ctx.mouse.click(fx, fy)
        ctx.log(f"已点「关注」页签 @ ({fx},{fy}) 相似度 {score:.3f}", level="info")
        self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
        if self._click_template(ctx, "buy_want"):
            ctx.log("已点「我要购买」。", level="info")
            self._interruptible_sleep(ctx, self._jitter(0.8, ctx))
            return True
        ctx.log("未识别到「我要购买」按钮，跳过（仍按普通摆摊列表跑）。", level="warn")
        return False

    def _click_market_tab(self, ctx):
        """自动进商城的收尾：识别「摆摊」页签模板并点击。
        未标定/模板缺失/当前屏没识别到 → 打日志并跳过（不瞎点）。"""
        tc = ctx.task_cfg(self.name)
        tab_path = (tc.get("templates") or {}).get("market_tab")
        rect = ctx.window.rect()
        if not tab_path or rect is None:
            ctx.log("未标定「摆摊」页签，跳过点页签（若不在摆摊页请手动切过去）。", level="warn")
            return False
        img = vision.load_template(tab_path)
        if img is None:
            ctx.log("「摆摊」页签模板缺失，跳过点页签。", level="warn")
            return False
        scene = win_mod.grab(rect)
        hit = vision.match(scene, img, 0.78) if scene is not None else None
        if hit is None:
            ctx.log("没识别到「摆摊」页签，跳过（已在商城界面也可继续识别）。", level="warn")
            return False
        tx, ty, score = hit
        mx, my = rect[0] + tx, rect[1] + ty
        ctx.mouse.human_move(mx, my)
        ctx.mouse.click(mx, my)
        ctx.log(f"已点「摆摊」页签 @ ({mx},{my}) 相似度 {score:.3f}", level="info")
        self._interruptible_sleep(ctx, self._jitter(0.6, ctx))
        return True

    def _click_template(self, ctx, key, threshold=0.75, speed=None):
        """在窗口内找某「标志模板」并拟人点击其中心。未标定/模板缺失/窗口失效/识别不到 → False。
        供「坊/摊」标识刷新、购买/确认按钮这些会移动/变化的元素使用（不点固定坐标）。"""
        tc = ctx.task_cfg(self.name)
        tpl_path = (tc.get("templates") or {}).get(key)
        rect = ctx.window.rect()
        if not tpl_path or rect is None:
            return False
        img = vision.load_template(tpl_path)
        if img is None:
            return False
        scene = win_mod.grab(rect)
        hit = vision.match(scene, img, threshold) if scene is not None else None
        if hit is None:
            return False
        cx, cy, _ = hit
        ctx.mouse.click(rect[0] + cx, rect[1] + cy, speed=speed)
        return True

    def _enter_shelf(self, ctx):
        """刷新动作：依次点 标定的「「坊」标识」→「「摊」标识」（模板识别点击，不再点条目进货架）。
        等加载交给 _wait_shelf_loaded 自适应处理，这里只负责点击。
        任一步缺标定/识别不到/被停止则返回 False（主循环会跳过本轮识别）。"""
        tc = ctx.task_cfg(self.name)
        tpls = tc.get("templates") or {}
        if not tpls.get("refresh_1") or not tpls.get("refresh_2"):
            ctx.log("「坊/摊」标识未标定，无法刷新。", level="warn")
            return False
        if not self._click_template(ctx, "refresh_1"):
            ctx.log("没识别到「坊」标识，无法刷新。", level="warn")
            return False
        ctx.mouse.sleep(0.25, 0.5)                 # 两步之间留点间隔
        if ctx.should_stop():
            return False
        if not self._click_template(ctx, "refresh_2"):
            ctx.log("没识别到「摊」标识，无法刷新。", level="warn")
            return False
        return not ctx.should_stop()

    def _wait_shelf_loaded(self, ctx, list_rect, loop):
        """自适应等货架加载：先等一个最短时间，再每隔一小段截图比上一帧，
        画面一旦静止（两帧几乎无差异）就认为加载完、立即返回该帧用于识别；
        始终不超过 shelf_load_wait_sec（上限/超时）。被停止则返回 None。"""
        min_w = self._jitter(loop.get("shelf_load_min_sec", 0.25), ctx)
        max_w = max(min_w, loop.get("shelf_load_wait_sec", 1.2))
        STABLE_DIFF = 1.5        # 两帧平均像素差低于此即视为画面静止（加载完成）
        POLL = 0.06              # 轮询间隔

        self._interruptible_sleep(ctx, min_w)
        if ctx.should_stop():
            return None
        prev = win_mod.grab(list_rect)
        deadline = time.time() + max(0.0, max_w - min_w)
        while time.time() < deadline:
            if ctx.should_stop():
                return None
            time.sleep(POLL)
            cur = win_mod.grab(list_rect)
            if cur is None:
                return prev
            if self._frame_diff(prev, cur) < STABLE_DIFF:
                return cur       # 画面静止 → 加载完成，立即识别
            prev = cur
        return prev

    def _buy_sequence(self, ctx, hit_xy, speed=None):
        """命中后的下单序列。speed 传入『极速』倍率，让这一连串点击尽量快——抢货成败就在这里。
        购买/确认按钮都是标志模板（会变化/弹窗），走 _click_template 视觉匹配点击。"""
        ctx.mouse.click(hit_xy[0], hit_xy[1], speed=speed)      # 点中装备
        self._snipe_sleep(0.18, speed)
        self._click_template(ctx, "buy_button", speed=speed)     # 购买
        self._snipe_sleep(0.18, speed)
        self._click_template(ctx, "confirm_button", speed=speed) # 确认（可空标定）

    @staticmethod
    def _snipe_sleep(base, speed):
        """下单中间的极短等待，按极速倍率压缩（仍留一点随机抖动，避免完全等距）。"""
        import random
        spd = max(0.2, float(speed)) if speed else 1.0
        s = base / spd
        time.sleep(max(0.0, s * (1 + random.uniform(-0.2, 0.2))))
