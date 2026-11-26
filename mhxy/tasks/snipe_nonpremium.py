# -*- coding: utf-8 -*-
"""
抢非珍品任务（snipe_nonpremium）：针对你手动打开的「某一物品的在售商品页」，循环刷新，
刷出价格低于设定阈值的那一条就点该商品行 → 购买 → 确认购买，买到指定个数后自动停止。

与「抢关注」(sniper) 的区别：
  · 抢关注是模板匹配认装备图、命中即抢、不做比价、无限循环；
  · 本任务要【读价格数字】（core.ocr，RapidOCR 只跑识别模型 + 自己做行切分）并与目标限价比较，
    且有明确完成条件（成功买到 target_count 个），到数自停。
    旧版要手框 0~9 十个数字模板，已废弃（标定项删除、配置里的旧键迁移时清掉）。
进场约定：运行前请在游戏里手动打开要抢物品的商品页（本任务不自动进商城、不切页），
所以和 sniper 一样 ENSURE_MAIN_ON_START=False（绝不 ESC 关掉你开好的面板）、
POPUP_GUARD_OFF=True（商城面板右上角自带「×」，会被弹窗守卫误关）。

刷新方式与抢关注完全相同：依次点「坊」标识 →「摊」标识（refresh_1 / refresh_2）。
"""

import time

from ..core import vision
from ..core import ocr as ocr_mod
from ..core.config import BUY_BUTTON_TPL_KEY, CAPTURES_DEBUG_DIR
from .base import Task, register


@register
class SnipeNonpremiumTask(Task):
    name = "snipe_nonpremium"
    title = "抢非珍品"
    description = "在已打开的商品页循环刷新，价格低于限价就秒买，买到指定个数自停"

    # 停在商品页界面：开跑不能 ESC 回主界面（会把面板关掉）；弹窗守卫也挂起（面板自带×会被误关）。
    ENSURE_MAIN_ON_START = False
    POPUP_GUARD_OFF = True

    CALIBRATION = {
        "regions": [
            ("price_region", "价格列区域",
             "商品列表里【价格数字所在的一整列】（要能覆盖每一行的价格）；脚本在此切分数字、逐行读价。"
             "尽量只框价格列、别框进商品名或图标，避免杂字干扰"),
            ("listing", "列表/点击区域(可选)",
             "可选：识别到低价后点击的商品行所在区域。留空=整窗，按价格行中心横向位置点该行", True),
        ],
        "templates": [
            # —— 刷新与下单（与抢关注同款，可复用已标定思路，但存本任务命名空间）——
            # 读价已改 OCR（core.ocr），不再需要逐个框选数字 0~9 模板。
            ("refresh_1", "「坊」标识", "刷新：依次点它 + 「摊」标识（与抢关注同一套刷新动作）"),
            ("refresh_2", "「摊」标识", "刷新：先点「坊」标识之后点它"),
            ("confirm_button", "确认购买按钮(可选)", "二次确认弹窗的按钮；没标则只点「购买」并告警", True),
        ],
        "watchlist": False,
    }

    # ---- 启动前自检 ----
    def preflight(self, ctx):
        tc = ctx.task_cfg(self.name)
        problems = []
        tpls = tc.get("templates", {})
        if not (tc.get("regions") or {}).get("price_region"):
            problems.append("「价格列区域」未标定 —— 读价要靠它")
        if not ocr_mod.is_available():
            problems.append("OCR 依赖未安装 —— 请在项目目录执行 pip install -r requirements.txt（rapidocr）")
        if not tpls.get("refresh_1"):
            problems.append("「坊」标识未标定 —— 刷新要靠它与「摊」标识")
        if not tpls.get("refresh_2"):
            problems.append("「摊」标识未标定 —— 刷新要靠它与「坊」标识")
        loop = tc.get("loop", {})
        if int(loop.get("target_count", 0)) <= 0:
            problems.append("购买个数未设置 —— 请在页面填「要买几个」")
        if int(loop.get("max_price", 0)) <= 0:
            problems.append("目标限价未设置 —— 请在页面填「低于多少才买」")
        if not ctx.select_windows():
            problems.append(f"没找到/没选中目标窗口（标题含「{ctx.window.title_substr}」），请先打开游戏并选好窗口")
        return (len(problems) == 0), problems

    # ---- 主体 ----
    def _run(self, ctx):
        tc = ctx.task_cfg(self.name)
        loop = tc["loop"]
        regions = tc["regions"]
        ocr_thr = float(loop.get("ocr_threshold", 0.5))   # OCR 单行置信度，低于此判认不出
        max_price = int(loop["max_price"])            # 低于此价才买（严格 <）
        target_count = int(loop["target_count"])      # 买够这么多个就停
        refresh_interval = float(loop.get("refresh_interval_sec", 0.2))
        cooldown = float(loop.get("after_buy_cooldown_sec", 2.0))
        snipe_speed = ctx.cfg.get("humanize", {}).get("snipe_speed", 3.0)
        dry_run = bool(loop.get("dry_run", False))

        if not ocr_mod.is_available():
            ctx.log("OCR 依赖未安装（rapidocr），无法读价，已停止。"
                    "请在项目目录执行 pip install -r requirements.txt。", level="error")
            return
        ctx.log("OCR 引擎首次加载模型约需 1~2 秒，请稍候…", level="info")

        if not self._is_admin():
            ctx.log("⚠ 当前非管理员权限：游戏窗口在前台时鼠标可能无法移动/点击（UIPI 拦截），"
                    "请用『以管理员身份运行』重开。", level="warn")

        contexts = self._resolve_contexts(ctx, False)
        if not contexts:
            ctx.log("没找到/没选中目标窗口，已停止。", level="error")
            return
        wctx = contexts[0]      # 单物品：只操作「选择窗口」里选的队长号，不做多开轮转
        price_region = regions.get("price_region")
        listing = regions.get("listing")

        ctx.log(f"启动完成：目标限价 < {max_price}，计划购买 {target_count} 个，"
                f"OCR 置信度阈值 {ocr_thr}，检测区：{'整窗' if not listing else '手动框选'}。"
                "请确认已在要抢物品的商品页。")
        ctx.log("演练模式（只读价不下单）" if dry_run else "★ 实战模式：达到限价会真正下单 ★",
                level="warn" if not dry_run else "info")

        bought = 0
        rounds = 0
        self._last_empty_log = 0.0
        self._last_prepare_log = 0.0
        while not ctx.should_stop():
            if wctx.window.rect() is None:
                # 窗口失效：尝试重新枚举
                fresh = self._resolve_contexts(ctx, False)
                if not fresh:
                    ctx.log("暂时没检测到目标窗口，等待…", level="warn")
                    self._interruptible_sleep(ctx, 2.0)
                    continue
                wctx = fresh[0]
            if not self._prepare_window(wctx, False):
                now = time.time()
                if now - self._last_prepare_log >= 2.0:
                    self._last_prepare_log = now
                    ctx.log("窗口切前台失败或已停止，本轮跳过、下轮重试…", level="warn")
                self._interruptible_sleep(ctx, refresh_interval)
                continue
            wctx.mouse.maybe_idle()
            rounds += 1

            # 1) 刷新（坊→摊），与抢关注同一套
            if not self._enter_shelf(wctx):
                self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
                continue
            # 2) 自适应等加载（沿用价格区域帧差判静止）
            price_rect = wctx.window.region_to_screen_rect(price_region)
            if price_rect is None:
                self._interruptible_sleep(ctx, 0.5)
                continue
            scene = self._wait_region_loaded(wctx, price_rect, loop)
            if scene is None:
                continue
            # 3) 读价：OCR 逐行得到 (price, 行中心y, 置信度)
            try:
                prices = ocr_mod.read_prices(scene, min_conf=ocr_thr)
            except RuntimeError as e:
                # 引擎初始化失败（依赖/模型缺失）——继续跑也没意义，直接终止
                ctx.log(str(e), level="error")
                return
            except Exception:
                # 单帧切分/识别异常：跳过本轮继续刷新（下方诊断会节流打日志），别让整单任务挂掉
                prices = []
            if not prices:
                # 读不出价格时打印诊断（节流，约每 3 秒一条），落盘标注图定位框选/识别问题
                now = time.time()
                if now - self._last_empty_log >= 3.0:
                    self._last_empty_log = now
                    diag = ocr_mod.diagnose_region(
                        scene, min_conf=ocr_thr, save_dir=self._diag_dir(ctx))
                    raw = "、".join(f"{cy:.0f}:{text}({score:.2f})"
                                    for cy, text, score, _price in diag.get("rows", [])[:12])
                    ctx.log(f"本屏没读出价格：{diag.get('reason', '无有效行')}；"
                            f"切出 {diag['n_rows']} 行，逐行 OCR[{raw}]。", level="warn")
                    if diag.get("annotated"):
                        ctx.log(f"诊断标注图：{diag['annotated']}；干净原图：{diag.get('raw_image')}。",
                                level="warn")
                self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
                continue
            # 4) 选「价格最低且低于限价」的那一行（同物品多在售，捡最便宜的）
            cand = min(prices, key=lambda p: p[0])
            price, row_cy, mean_s = cand
            # 每轮都打印当前识别到的最低价，方便核对识别准不准
            ctx.log(f"本屏识别到 {len(prices)} 行，最低价 {price}（限价 {max_price}，"
                    f"已买 {bought}/{target_count}）", level="info")
            if price >= max_price:
                self._interruptible_sleep(ctx, self._jitter(refresh_interval, ctx))
                continue
            # 5) 命中低价 → 点该商品行 → 购买 → 确认
            click_xy = self._row_click_xy(wctx, price_rect, row_cy, listing)
            ctx.log(f"★ 发现低价 {price} < {max_price}（OCR 置信度 {mean_s:.3f}）@ {click_xy}，下单…",
                    level="hit")
            if dry_run:
                ctx.log("  [演练] 不下单。确认无误后到页面关掉演练模式。")
            else:
                if self._buy_sequence(wctx, click_xy, snipe_speed, loop):
                    bought += 1
                    ctx.log(f"已完成第 {bought}/{target_count} 次购买（成交价 {price}）。", level="hit")
                    if bought >= target_count:
                        ctx.log(f"已买够 {target_count} 个，任务完成，自动停止。", level="hit")
                        break
                else:
                    ctx.log("本次下单序列未完整（可能没成交），不计入个数，继续刷新。", level="warn")
                self._interruptible_sleep(ctx, self._jitter(cooldown, ctx))
        ctx.log(f"已停止。共刷新 {rounds} 轮，成功计数 {bought}/{target_count}。")

    # ---- 读价诊断图落盘目录（captures_debug 下单独子目录）----
    def _diag_dir(self, ctx):
        return str(CAPTURES_DEBUG_DIR / "抢非珍品读价诊断")

    # ---- 单窗口上下文：只盯「选择窗口」里选的队长号，不做多开轮转 ----
    def _captain_index(self, ctx, n_wins):
        """复用全局共享 tasks.teaming.captain_index（「选择窗口」卡片上的队长单选），
        它是【所选窗口列表】里的序号（0 起），与抓鬼/周常等多人任务同一口径。
        没选/越界时回退到第一个选中号并告警，避免把点击落到错误的窗口。"""
        try:
            cap = int(ctx.task_cfg("teaming").get("captain_index", 0) or 0)
        except (TypeError, ValueError):
            cap = 0
        if not (0 <= cap < n_wins):
            ctx.log(f"队长序号 号{cap + 1} 越界（当前选中 {n_wins} 个窗口），"
                    "改用第一个选中号；请在侧栏「选择窗口」重选队长。", level="warn")
            return 0
        return cap

    def _resolve_contexts(self, ctx, multi=False):
        """按 targets 配置选出目标窗口并绑到主 ctx，返回 [ctx]；找不到返回 []。
        执行窗口 = 选中的队长号（不写死号1）；单开/多开都只操作这一个号。"""
        wins = ctx.select_windows()
        if not wins:
            return []
        cap = self._captain_index(ctx, len(wins))
        w = wins[cap]
        ctx.window = w
        ctx.log(f"抢非珍品在队长号执行：号{self._window_no(w, cap)}"
                f"（所选窗口第 {cap + 1}/{len(wins)} 个）", level="info")
        return [ctx]

    def _prepare_window(self, wctx, multi=False):
        """操作前确保窗口有效并切到前台（不切会点到脚本界面上）；
        切前台失败（系统拒绝焦点抢占）则本轮跳过、下轮重试。"""
        if wctx.window.rect() is None:
            return False
        if not wctx.window.activate():
            return False
        if wctx.should_stop():
            return False
        return True

    # ---- 把价格行中心换算成屏幕点击坐标 ----
    def _row_click_xy(self, ctx, price_rect, row_cy, listing):
        """价格区域里第 row_cy 行（局部 y）对应到屏幕坐标。
        有 listing 区就把点击 x 落在 listing 区中心（点商品行条目处），否则点价格行靠左一点的位置。"""
        if listing:
            lr = ctx.window.region_to_screen_rect(listing)
            if lr:
                return (lr[0] + lr[2] // 2, price_rect[1] + int(row_cy))
        # 默认点价格行最左端（通常紧邻商品名/图标，点该行即可选中）
        return (price_rect[0] + 6, price_rect[1] + int(row_cy))

    # ---- 刷新（坊→摊），逻辑同抢关注，读本任务命名空间的模板 ----
    def _enter_shelf(self, ctx):
        tc = ctx.task_cfg(self.name)
        tpls = tc.get("templates") or {}
        if not tpls.get("refresh_1") or not tpls.get("refresh_2"):
            ctx.log("「坊/摊」标识未标定，无法刷新。", level="warn")
            return False
        if not self._click_template(ctx, "refresh_1"):
            ctx.log("没识别到「坊」标识，无法刷新。", level="warn")
            return False
        ctx.mouse.sleep(0.25, 0.5)
        if ctx.should_stop():
            return False
        if not self._click_template(ctx, "refresh_2"):
            ctx.log("没识别到「摊」标识，无法刷新。", level="warn")
            return False
        return not ctx.should_stop()

    # ---- 自适应等区域加载（帧差判静止），与抢关注同思路，作用在指定矩形 ----
    def _wait_region_loaded(self, ctx, rect, loop):
        min_w = self._jitter(loop.get("shelf_load_min_sec", 0.15), ctx)
        max_w = max(min_w, loop.get("shelf_load_wait_sec", 1.2))
        STABLE_DIFF = 1.5
        POLL = 0.06
        self._interruptible_sleep(ctx, min_w)
        if ctx.should_stop():
            return None
        prev = ctx.window.grab_screen(rect)
        deadline = time.time() + max(0.0, max_w - min_w)
        while time.time() < deadline:
            if ctx.should_stop():
                return None
            time.sleep(POLL)
            cur = ctx.window.grab_screen(rect)
            if cur is None:
                return prev
            if self._frame_diff(prev, cur) < STABLE_DIFF:
                return cur
            prev = cur
        return prev

    # ---- 下单序列：点商品 → 购买 → 确认购买 ----
    def _buy_sequence(self, ctx, hit_xy, speed, loop):
        ctx.mouse.click(hit_xy[0], hit_xy[1], speed=speed)
        self._snipe_sleep(0.18, speed)
        if not self._click_template(ctx, BUY_BUTTON_TPL_KEY, speed=speed, timeout=0.6):
            ctx.log("未识别到『购买』按钮，跳过本次下单。", level="warn")
            return False
        ctx.log("已点「购买」。", level="info")
        self._snipe_sleep(0.18, speed)
        confirm_timeout = float(loop.get("confirm_timeout_sec", 1.5))
        if self._click_template(ctx, "confirm_button", speed=speed, timeout=confirm_timeout):
            ctx.log("已点「确认购买」。", level="info")
            return True
        # 确认按钮可选：没标/没识别到只告警、不按 Esc（避免误关面板）
        ctx.log("未识别到『确认购买』按钮，本次购买可能未完成（不计入个数）。", level="warn")
        return False

    # ---- 标志模板识别点击（同抢关注，购买按钮兼容读公共标定）----
    def _click_template(self, ctx, key, threshold=0.75, speed=None, timeout=0.0):
        tc = ctx.task_cfg(self.name)
        tpl_path = (tc.get("templates") or {}).get(key)
        if key == BUY_BUTTON_TPL_KEY and not tpl_path:
            tpl_path = ((((ctx.cfg or {}).get("tasks", {}) or {}).get("shared", {}) or {})
                        .get("templates", {}) or {}).get(BUY_BUTTON_TPL_KEY)
        rect = ctx.window.rect()
        if not tpl_path or rect is None:
            return False
        img = vision.load_template(tpl_path)
        if img is None:
            return False
        start = time.time()
        poll = 0.12
        while True:
            if ctx.should_stop():
                return False
            scene = ctx.window.grab_screen(rect)
            hit = vision.match(scene, img, threshold) if scene is not None else None
            if hit is not None:
                cx, cy, _ = hit
                ctx.mouse.click(rect[0] + cx, rect[1] + cy, speed=speed)
                return True
            if timeout <= 0.0:
                return False
            if time.time() - start > timeout:
                return False
            time.sleep(poll)

    @staticmethod
    def _snipe_sleep(base, speed):
        import random
        spd = max(0.2, float(speed)) if speed else 1.0
        s = base / spd
        time.sleep(max(0.0, s * (1 + random.uniform(-0.2, 0.2))))
