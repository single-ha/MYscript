# -*- coding: utf-8 -*-
"""通用页：跨任务功能（公共区域标定〔含活动/背包列表 + 拓印临摹模板〕、组队标定+一键组队/一键解散、整理背包、窗口尺寸归一化）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_REQUIRED_TEMPLATES
from ...core.inventory import _ALL_BTN_KEYS
from ...core.config import SHARED_REGION_KEYS, TASK_SHARED_REGIONS, TUOYING_TPL_KEYS
from ..common import Card, load_thumb, bind_wraplength


class GeneralPage(ctk.CTkFrame):
    """通用页：集中放与具体任务无关的功能。
    目前：公共区域标定（活动/背包列表，全任务共用）；组队标定 + 一键组队（选队长→把所选多开窗口组成一队）；
    窗口尺寸归一化。"""

    LOG_SOURCE = "通用"   # 组队/整理背包日志在 pump 里各自覆盖来源标签

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.cfg = app.cfg
        self.runner = None          # 一键组队跑的后台任务（DungeonTask）
        self.runner_db = None       # 一键解散跑的后台任务（DisbandTask），与组队互斥（同用鼠标/队伍面板）
        self.runner_ob = None       # 一键整理跑的后台任务（OrganizeBagTask），与组队并存互不干扰
        self._team_cal_dialog = None    # 「标定（组队）」去重槽（队长ID 走无弹窗直接标定，无需去重槽）
        self._shared_cal_dialog = None  # 「标定（公共区域）」去重槽
        self._ob_cal_dialog = None      # 「标定（整理背包）」去重槽
        self.btn_ob = None          # 「一键整理」按钮（_refresh_body 每次重建）
        self.switch_auto_ob = None  # 「自动整理背包」开关（任何任务检测到背包满自动整理）
        self._win_count = 0         # 已选多开窗口数（resolve_targets），供状态行显示
        self.btn_team = None
        self.btn_disband = None     # 「一键解散」按钮（_refresh_body 每次重建）
        self.btn_leader = None      # 行内队长ID按钮（_refresh_body 每次重建）
        self._leader_thumbs = []    # 行内队长ID缩略图防 GC
        self.lbl_team_status = None
        self._build()

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(head, text="通用", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        sub = ctk.CTkLabel(head, text="跨任务的通用功能：公共区域标定（活动/背包列表 + 拓印临摹模板，全任务共用）+ 组队标定 + 一键组队 / 一键解散 / 还原窗口尺寸。各任务专属的标定与「选择窗口」仍在对应任务页。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        sub.pack(fill="x", anchor="w", pady=(4, 0))
        bind_wraplength(sub)

        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=1, column=0, sticky="nsew")
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)
        # 不在构建时枚举窗口（省启动开销）；首次切到本页时 _show 会调 refresh() 填充。

    def _card(self):
        c = Card(self.body)
        c.pack(fill="x", pady=(0, T.SP_3), padx=2)
        return c

    # 切到本页或操作后都会调
    def refresh(self):
        self.cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg
        self._refresh_body()

    def _normalize_now(self):
        """点「还原尺寸」时触发：把所有尺寸≠基准的游戏窗口拉回基准尺寸。
        基准未设置时提示；没有窗口时提示。只对尺寸不符的窗口动手（已是基准的不碰）。"""
        cfg = cfg_mod.load_config()
        self.cfg = cfg
        self.app.cfg = cfg
        base = (cfg.get("targets") or {}).get("base_size")
        if not base or len(base) < 2:
            self.app.toast("请先设置基准尺寸（填上面两个框或在窗口列表点「设为基准」）")
            return
        bw, bh = int(base[0]), int(base[1])
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        try:
            wins = win_mod.locate_all(title, offset)
        except Exception:
            wins = []
        if not wins:
            self.app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
            return
        todo = []
        for w in wins:
            r = w.rect()
            if r and (abs(r[2] - bw) > 4 or abs(r[3] - bh) > 4):
                todo.append(w)
        if not todo:
            self.app.toast(f"所有窗口已是基准尺寸 {bw}×{bh}，无需还原")
            self.refresh()
            return
        ok = 0
        for w in todo:
            w.activate()
            if w.resize_to(bw, bh):
                ok += 1
        self.app._game_connected = None    # 尺寸变了，强制下次 tick 刷新药丸
        if ok < len(todo):
            self.app.toast(f"已还原 {ok}/{len(todo)} 个号到 {bw}×{bh}；部分窗口可能锁了分辨率档位")
        else:
            self.app.toast(f"已把 {ok} 个号还原到基准尺寸 {bw}×{bh}")
        self.refresh()

    def _arrange_windows(self):
        """点「调整窗口」时触发：把所选窗口（最多 5 个）调到基准尺寸并按 2列×2行 排布在屏幕上——
        第一排（窗口1、2）上边贴屏幕/工作区顶、第二排（窗口3、4）下边贴任务栏，第 5 个窗口放屏幕正中；
        最左列距左侧留 arrange_left_margin 像素空白（config 可改，默认 100）。
        用 resolve_targets 保证和任务实操是同一批号；点数不足 5 就只排排到的。"""
        cfg = cfg_mod.load_config()
        self.cfg = cfg
        self.app.cfg = cfg
        base = (cfg.get("targets") or {}).get("base_size")
        if not base or len(base) < 2:
            self.app.toast("请先设置基准尺寸（在窗口列表点「设为基准」）")
            return
        bw, bh = int(base[0]), int(base[1])
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        targets = cfg.get("targets", {})
        try:
            wins = win_mod.resolve_targets(title, offset, targets)[:5]
        except Exception:
            wins = []
        if not wins:
            self.app.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
            return
        wa = win_mod.work_area()
        wx, wy, ww, wh = wa[0], wa[1], wa[2], wa[3]
        margin = int(cfg.get("arrange_left_margin", 100))
        col1 = wx + margin             # 最左列距左侧留 margin 空白（config.arrange_left_margin 可调）
        col2 = wx + ww - bw            # 右列贴工作区右
        row1y = wy                   # 第一排上边贴屏幕/工作区顶
        row2y = wy + wh - bh         # 第二排（最后一行）下边贴任务栏(=工作区底)
        cx = wx + ww // 2
        cy = wy + wh // 2
        slots = [
            (col1, row1y),           # 号1 上左
            (col2, row1y),           # 号2 上右
            (col1, row2y),           # 号3 下左
            (col2, row2y),           # 号4 下右
            (cx - bw // 2, cy - bh // 2),   # 号5 屏幕正中
        ]
        ok = 0
        for w, (x, y) in zip(wins, slots):
            w.activate()
            if w.resize_to(bw, bh, move_to=(x, y)):
                ok += 1
        self.app._game_connected = None   # 尺寸/位置变了，强制下次 tick 刷新药丸
        extra = ", 第 5 个居中放屏幕正中" if len(wins) >= 5 else ""
        tip = ("已调整 {}/{} 个窗口到基准尺寸 {}×{}，并按 2列×2行 排布（第1排贴顶、最后1排贴任务栏"
               "{extra}）。分辨率锁档的号可能未移动。").format(ok, len(wins), bw, bh, extra=extra)
        self.app.toast(tip)
        self.refresh()

    def _build_window_card(self, base):
        """窗口尺寸归一化卡片（排在本页最上：基准尺寸/调整窗口先于其它功能）。"""
        c2 = self._card()
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(14, 4))
        head2.grid_columnconfigure(0, weight=1)
        txt2 = ctk.CTkFrame(head2, fg_color="transparent")
        txt2.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt2, text="窗口尺寸归一化", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        base_txt = f"{int(base[0])}×{int(base[1])}" if base and len(base) >= 2 else "未设置"
        ctk.CTkLabel(txt2, text=f"当前基准尺寸：{base_txt}", font=self.fonts["body"],
                     text_color=T.TEXT if base else T.WARN).pack(anchor="w", pady=(4, 0))
        sub2 = ctk.CTkLabel(txt2, text="「还原尺寸」把所有窗口拉回基准尺寸；「调整窗口」把所选窗口（最多5个）"
                                       "按基准尺寸排成 2列×2行 —— 第1排贴屏幕顶、最后1排贴任务栏、最左列距左侧留"
                                       "空白（config.arrange_left_margin，默认100像素）、第5个居中。"
                                       "脚本点位按此基准尺寸标定。在下面窗口列表点「设为基准」来设定基准尺寸。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub2.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub2)
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="调整窗口", font=self.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._arrange_windows).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="还原尺寸", font=self.fonts["body"], height=32, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._normalize_now).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="刷新", font=self.fonts["body"], height=30, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack()

        if not (base and len(base) >= 2):
            ctk.CTkLabel(c2, text="基准尺寸尚未设置：在下方窗口列表点「设为基准」即可。",
                         font=self.fonts["small"], text_color=T.WARN, justify="left").pack(
                             anchor="w", padx=16, pady=(2, 0))

        # 窗口列表（信息 + 快捷把某个窗口尺寸设为基准）
        ctk.CTkLabel(c2, text="检测到的窗口（点「设为基准」用该窗口的当前尺寸作为基准）：",
                     font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left").pack(
                         anchor="w", padx=16, pady=(8, 2))
        # 窗口枚举（getAllWindows）很慢、绝不能卡主线程：先放占位、后台线程枚举完再回主线程填。
        # 每次重建卡片重置「空结果重试」计数（一次全新枚举失败重试有限次，不会死循环）。
        self._enum_empty_retries = 0
        rows_holder = ctk.CTkFrame(c2, fg_color="transparent")
        rows_holder.pack(fill="x")
        ctk.CTkLabel(rows_holder, text="正在检测窗口…", font=self.fonts["body"],
                     text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
        self._kick_enum_windows(rows_holder, base)

    def _refresh_body(self):
        for w in self.body.winfo_children():
            w.destroy()
        cfg = self.cfg
        targets = cfg.get("targets", {})
        base = targets.get("base_size")

        # ── 窗口尺寸归一化（排最上：先统一各号尺寸/基准，其它功能都建立在它之上）──
        self._build_window_card(base)

        # ── 公共区域（全局共享：活动列表/背包列表，任意任务标一次全任务通用）──
        c_shared = self._card()
        head_s = ctk.CTkFrame(c_shared, fg_color="transparent")
        head_s.pack(fill="x", padx=16, pady=(14, 4))
        head_s.grid_columnconfigure(0, weight=1)
        txt_s = ctk.CTkFrame(head_s, fg_color="transparent")
        txt_s.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_s, text="公共区域（全局共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        # 区域：活动列表 / 背包列表 / 拓印绘制区（可选）。可选键计入总数，但全标齐才算区域就绪。
        shared_tc = cfg_mod.task_config(cfg, "shared")
        sreg = shared_tc.get("regions", {})
        shared_region_keys = SHARED_REGION_KEYS + TASK_SHARED_REGIONS.get("dungeon", ())
        sdone = sum(1 for k in shared_region_keys if sreg.get(k))
        sready = sdone == len(shared_region_keys)
        stpl = shared_tc.get("templates", {})
        tdone = sum(1 for k in TUOYING_TPL_KEYS if stpl.get(k))
        already = sready and tdone == len(TUOYING_TPL_KEYS)
        ctk.CTkLabel(txt_s, text=f"区域：{sdone}/{len(shared_region_keys)}　标志模板：{tdone}/{len(TUOYING_TPL_KEYS)}"
                                 + ("　✓ 已就绪" if already else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if already else T.WARN).pack(anchor="w", pady=(4, 0))
        sub_s = ctk.CTkLabel(txt_s, text="「活动」界面那一片卡片列表、打开背包后那一片物品列表，几乎所有任务的画面都一样——"
                                        "在这里框一次，宝图 / 运镖 / 秘境降妖 / 三界奇缘 / 抓鬼 / 刷副本 / 整理背包自动通用，"
                                        "不用每个任务各标一遍。各任务页里的同名两项也会自动显示共用。"
                                        "「拓印」临摹的标题/上传按钮也在这里标：刷副本点「进入」偶发的临摹弹窗，所有副本共用一份。",
                             font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub_s.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub_s)
        btns_s = ctk.CTkFrame(head_s, fg_color="transparent")
        btns_s.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns_s, text="标定（公共区域）", font=self.fonts["body"], height=36, width=130,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_shared_calibrate).pack()

        # ── 组队（跨任务共享：任何用到组队的任务都自动读这份标定）──
        c_team = self._card()
        head_t = ctk.CTkFrame(c_team, fg_color="transparent")
        head_t.pack(fill="x", padx=16, pady=(14, 4))
        head_t.grid_columnconfigure(0, weight=1)
        txt_t = ctk.CTkFrame(head_t, fg_color="transparent")
        txt_t.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_t, text="组队（共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        team_tc = cfg_mod.task_config(cfg, "teaming")
        treg, ttpl = team_tc.get("regions", {}), team_tc.get("templates", {})
        rdone = sum(1 for k in TEAM_REQUIRED_REGIONS if treg.get(k))
        tdone = sum(1 for k in TEAM_REQUIRED_TEMPLATES if ttpl.get(k))
        ready = (rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_REQUIRED_TEMPLATES))
        ctk.CTkLabel(txt_t, text=f"组队标定：必要区域 {rdone}/{len(TEAM_REQUIRED_REGIONS)}，"
                                 f"必要模板 {tdone}/{len(TEAM_REQUIRED_TEMPLATES)}"
                                 + ("　✓ 已就绪" if ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if ready else T.WARN).pack(anchor="w", pady=(4, 0))
        sub_t = ctk.CTkLabel(txt_t, text="队长建队→队员申请→接受→关窗，是跨任务的共享能力。"
                                        "刷副本等任何用到组队的任务都自动读这份标定"
                                        "（队长ID、创建/申请/接受/申请入队、好友列表区/队伍面板区等）。",
                             font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub_t.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub_t)
        btns_t = ctk.CTkFrame(head_t, fg_color="transparent")
        btns_t.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns_t, text="标定（组队）", font=self.fonts["body"], height=36, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_team_calibrate).pack()

        # —— 一键组队（选好队长，把所选多开窗口直接组成一队）——
        ctk.CTkFrame(c_team, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))
        act = ctk.CTkFrame(c_team, fg_color="transparent")
        act.pack(fill="x", padx=16, pady=(10, 0))
        self.btn_team = ctk.CTkButton(act, text="▶  一键组队", font=self.fonts["btn"], height=40, width=150,
                                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                      text_color=T.ON_ACCENT, command=self._start_teaming)
        self.btn_team.pack(side="left")
        # 一键解散：让所选各号都退出当前队伍（开队伍面板→退出队伍→关面板，每号同一套流程）
        self.btn_disband = ctk.CTkButton(act, text="⏏  一键解散", font=self.fonts["btn"], height=40, width=130,
                                         corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                         text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                         command=self._start_disband)
        self.btn_disband.pack(side="left", padx=(8, 0))
        # 「选择窗口」带缩略图，且队长就在这里选（卡片上勾「队长」）——下拉框「号123」看不出是哪个窗口，故移进来。
        ctk.CTkButton(act, text="选择窗口/队长", font=self.fonts["body"], height=36, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda: self.app.open_window_picker(self.refresh, captain_ns="teaming")).pack(
                          side="left", padx=(8, 0))
        # 队长ID 入口：带缩略图的小按钮，点开「队长ID 库」（当前+最近3历史可切换）；
        # 和刷副本页写同一处 teaming.leader_id，天然同步。
        self.btn_leader = ctk.CTkButton(act, text="标定队长ID", font=self.fonts["small"], height=36, width=110,
                                        corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                        text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                                        compound="left", command=self._open_leader_gallery)
        self.btn_leader.pack(side="left", padx=(8, 0))
        self._refresh_leader_btn()

        self.lbl_team_status = ctk.CTkLabel(c_team, text="", font=self.fonts["small"],
                                            text_color=T.TEXT_DIM, justify="left")
        self.lbl_team_status.pack(fill="x", padx=16, pady=(6, 0))
        bind_wraplength(self.lbl_team_status)

        # 日志已统一到 App 右侧的全局日志面板（组队打「组队」标签、整理背包打「整理背包」标签），本页不再单独建日志框。
        # 重建后：按窗口数即时渲染队长下拉/状态 + 据 runner 复位按钮，再后台刷新窗口数
        self._render_team_action()
        if self.runner and self.runner.is_running():
            self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        if self.runner_db and self.runner_db.is_running():
            self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self._kick_count_windows()

        # ── 整理背包（跨任务共享：任何任务流程都可穿插调用，这里可单独一键运行）──
        self._build_organize_card()

    def _kick_enum_windows(self, holder, base):
        """后台枚举窗口，完成后回主线程把列表填进 holder。用 token 丢弃过期结果（连续切页/刷新时）。
        每次重建卡片都会重设空结果重试计数。"""
        cfg = self.cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        token = object()
        self._enum_token = token

        def work():
            try:
                wins = win_mod.locate_all(title, offset)
                data = [(w, w.rect()) for w in wins]   # rect() 趁后台一并取好
            except Exception:
                data = []
            try:
                self.app.after(0, lambda: self._fill_win_rows(holder, data, base, token))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _fill_win_rows(self, holder, data, base, token):
        """在主线程把枚举结果渲染进 holder。过期结果/控件已销毁则丢弃。
        空结果（开局时与 _kick_locate 的 getAllWindows 并发会瞬时读到空）自动重试几次再放弃，
        不必等用户手动点「刷新」。"""
        if token is not getattr(self, "_enum_token", None):
            return
        try:
            if not holder.winfo_exists():
                return
        except Exception:
            return
        if not data:
            # 空结果多半是并发 getAllWindows 的瞬时抖动：稍后再试（最多 _enum_empty_retries 上限）
            n = getattr(self, "_enum_empty_retries", 0)
            if n < 3:
                self._enum_empty_retries = n + 1
                self.app.after(400, lambda: self._kick_enum_windows(holder, base))
                return
            try:
                for w in holder.winfo_children():
                    w.destroy()
            except Exception:
                pass
            ctk.CTkLabel(holder, text="没检测到游戏窗口，请先打开游戏再点「刷新」。",
                         font=self.fonts["body"], text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
            return
        try:
            for w in holder.winfo_children():
                w.destroy()
        except Exception:
            return
        for i, (w, r) in enumerate(data):
            row = ctk.CTkFrame(holder, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.pack(fill="x", padx=12, pady=4)
            row.grid_columnconfigure(0, weight=1)
            meta = f"号{i + 1}    {r[2]}×{r[3]}    @({r[0]},{r[1]})" if r else f"号{i + 1}    （窗口已失效）"
            is_base = bool(base and r and int(base[0]) == r[2] and int(base[1]) == r[3])
            ctk.CTkLabel(row, text=meta + ("   ✓ 当前基准" if is_base else ""),
                         font=self.fonts["body"],
                         text_color=T.SUCCESS if is_base else T.TEXT).grid(
                             row=0, column=0, sticky="w", padx=12, pady=8)
            ctk.CTkButton(row, text="设为基准", font=self.fonts["small"], width=84, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda w=w: self._set_base_from(w)).grid(row=0, column=1, padx=10)
        ctk.CTkFrame(holder, fg_color="transparent", height=6).pack()

    def _set_base_from(self, w):
        r = w.rect()
        if not r:
            self.app.toast("该窗口已失效，请点「刷新」")
            return
        cfg = cfg_mod.load_config()
        cfg.setdefault("targets", {})["base_size"] = [r[2], r[3]]
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self.app.toast(f"已设基准尺寸 {r[2]}×{r[3]}（点「还原尺寸」时其它号会归一化到这个大小）")
        self.refresh()

    def _calib_singleton(self, attr, only, fail_msg, exclude=None):
        """打开一个标定窗并按 attr 去重：已开着就 lift 回来，不叠开多个写同一处 teaming 的窗
        （叠开会「后关的覆盖先关的」，让用户以为没生效）。"""
        existing = getattr(self, attr, None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            setattr(self, attr, None)
            self.refresh()

        try:
            setattr(self, attr, CalibrateDialog(self.app, task_name="teaming",
                                                only=only, exclude=exclude, on_done=_after))
        except Exception as e:
            setattr(self, attr, None)
            self.app.toast(f"{fail_msg}：{e}")

    def _open_shared_calibrate(self):
        """打开公共区域标定（共享命名空间 shared：活动/背包列表区域 + 拓印临摹模板，全局共用），按 _shared_cal_dialog 去重。"""
        existing = self._shared_cal_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            self._shared_cal_dialog = None
            self.refresh()

        try:
            self._shared_cal_dialog = CalibrateDialog(self.app, task_name="shared", on_done=_after)
        except Exception as e:
            self._shared_cal_dialog = None
            self.app.toast(f"打开公共区域标定失败：{e}")

    def _open_team_calibrate(self):
        """打开组队标定（共享命名空间 teaming）。队长ID 已移到「标定队长ID」按钮单独标，这里不再列出。"""
        self._calib_singleton("_team_cal_dialog", None, "打开组队标定失败", exclude=["leader_id"])

    def _open_leader_gallery(self):
        """打开「队长ID 库」：当前+最近3历史可切换（共享 teaming.leader_id，与刷副本页同步）。"""
        from ..leader_gallery import LeaderIdGallery
        LeaderIdGallery.open(self.app)

    def _refresh_leader_btn(self):
        """重读激活队长ID图，更新行内按钮缩略图（无图则回退纯文字「标定队长ID」）。"""
        btn = getattr(self, "btn_leader", None)
        if btn is None:
            return
        self._leader_thumbs.clear()
        img = load_thumb("templates/tm_leader_id.png", self._leader_thumbs, max_h=26)
        try:
            btn.configure(image=img, text=" 队长ID" if img is not None else "标定队长ID")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # 一键组队（角色参数存共享命名空间 tasks.teaming；跑的是 DungeonTask）
    # ------------------------------------------------------------------
    def _render_team_action(self):
        """据当前 self._win_count + teaming.captain_index 渲染队长状态行（纯本地数据，秒回）。
        队长已挪进「选择窗口/队长」里选（带缩略图），这里只读出来显示。"""
        if self.lbl_team_status is None:
            return
        n = self._win_count
        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        cap = team_tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        multi = self.app.cfg.get("targets", {}).get("multi", False)
        if n >= 2:
            tip = f"已选 {n} 个号，队长=第{cap + 1}个所选号，其余当队员。点「一键组队」开始。"
        elif not multi:
            tip = "组队需多开：请点「选择窗口/队长」切到多开、勾 2~5 个号并指定队长。"
        else:
            tip = f"已选 {n} 个号，组队至少 2 个号（队长+≥1 队员）。"
        self.lbl_team_status.configure(text=tip)

    def _kick_count_windows(self):
        """后台枚举已选窗口数，变了再回主线程重渲染队长状态。token 丢弃过期结果。"""
        title = self.app.cfg.get("window_title", "梦幻西游")
        offset = self.app.cfg.get("window_offset", [0, 0])
        targets = self.app.cfg.get("targets", {})
        token = object()
        self._count_token = token

        def work():
            try:
                n = len(win_mod.resolve_targets(title, offset, targets))
            except Exception:
                n = 0

            def apply():
                if token is not getattr(self, "_count_token", None):
                    return
                self._win_count = n
                self._render_team_action()

            try:
                self.app.after(0, apply)
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def _start_teaming(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self._log_line("正在停止…", "warn", "组队")
            self.btn_team.configure(text="停止中…", state="disabled")
            return
        # 强制实战（一键组队是显式动作，不走演练）。窗口选择与队长都在「选择窗口/队长」里定好了。
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "teaming")
        tc["dry_run"] = False
        cfg_mod.set_task_config(cfg, "teaming", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg

        task_cls = get_task("dungeon")
        if task_cls is None:
            self._log_line("找不到组队任务。", "error", "组队")
            return
        self.runner = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始组队：" + p, "error", "组队")
            self.runner = None
            return
        self._log_line("开始一键组队…", "hit", "组队")
        self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_team_finished(self):
        if self.btn_team is not None:
            self.btn_team.configure(text="▶  一键组队", fg_color=T.ACCENT,
                                    hover_color=T.ACCENT_HOVER, state="normal")

    def _start_disband(self):
        if self.runner_db and self.runner_db.is_running():
            self.runner_db.stop()
            self._log_line("正在停止…", "warn", "解散")
            self.btn_disband.configure(text="停止中…", state="disabled")
            return
        # 强制实战（一键解散是显式动作，不走演练）。窗口在「选择窗口/队长」里选定。
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "teaming")
        tc["dry_run"] = False
        cfg_mod.set_task_config(cfg, "teaming", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg

        task_cls = get_task("disband")
        if task_cls is None:
            self._log_line("找不到解散队伍任务。", "error", "解散")
            return
        self.runner_db = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner_db.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始解散：" + p, "error", "解散")
            self.runner_db = None
            return
        self._log_line("开始一键解散…", "hit", "解散")
        self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER,
                                   state="normal")

    def _on_disband_finished(self):
        if self.btn_disband is not None:
            self.btn_disband.configure(text="⏏  一键解散", fg_color=T.BTN, hover_color=T.BTN_HOVER,
                                       text_color=T.TEXT, state="normal")

    # ---- 由 App._tick 驱动 ----
    def pump(self):
        if self.runner:
            q = self.runner.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "组队")
            if not self.runner.is_running() and self.btn_team is not None \
                    and self.btn_team.cget("text") != "▶  一键组队":
                self._on_team_finished()
        if self.runner_db:
            q = self.runner_db.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "解散")
            if not self.runner_db.is_running() and self.btn_disband is not None \
                    and self.btn_disband.cget("text") != "⏏  一键解散":
                self._on_disband_finished()
        if self.runner_ob:
            q = self.runner_ob.log_queue
            while not q.empty():
                level, msg = q.get()
                self._log_line(msg, level, "整理背包")
            if not self.runner_ob.is_running() and self.btn_ob is not None \
                    and self.btn_ob.cget("text") != "▶  一键整理":
                self._on_ob_finished()

    @staticmethod
    def _targets_summary(cfg):
        """只读 cfg.targets 拼一句「将操作哪些号」的说明，不去枚举/定位窗口（够快、给卡片当提示用）。
        多开未指定 multi_indices = 全体号；指定了就报个数；单开报号几。"""
        targets = (cfg or {}).get("targets", {}) or {}
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or []
            return f"多开 · 已选 {len(idxs)} 个号" if idxs else "多开 · 全体号"
        i = targets.get("single_index", 0)
        i = i if isinstance(i, int) and i >= 0 else 0
        return f"单开 · 号{i + 1}"

    # ------------------------------------------------------------------
    # 整理背包（跨任务共享：core.InventoryOrganizer + tasks.OrganizeBagTask；
    # 标定/物品/参数存共享命名空间 tasks.organize_bag；这里可单独一键运行）
    # ------------------------------------------------------------------
    def _build_organize_card(self):
        """在组队卡之后渲染「整理背包（共享）」卡片：完成度行 + 说明 + 按钮行。
        日志统一写到 App 右侧的全局日志面板（来源标签「整理背包」）。"""
        cfg = self.cfg
        ob_tc = cfg_mod.task_config(cfg, "organize_bag")
        items = ob_tc.get("items", []) or []
        tpl = ob_tc.get("templates", {}) or {}

        # 完成度 = 所有动作会用到的按钮模板（含可选「更多」与收尾的「整理」按钮）里已标定的数，
        # 与物品清单无关——避免「还没加物品时显示 0/0 还提示需标定」的误导。
        all_btn = set(_ALL_BTN_KEYS)
        done = sum(1 for k in all_btn if tpl.get(k))
        total = len(all_btn)
        ready = (done == total)

        c = self._card()
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        txt = ctk.CTkFrame(head, fg_color="transparent")
        txt.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt, text="整理背包（共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(txt, text=f"物品 {len(items)} 件；动作按钮 {done}/{total} 已标定"
                              + ("　✓ 已就绪" if ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if ready else T.WARN).pack(anchor="w", pady=(4, 0))
        sub = ctk.CTkLabel(txt, text="翻包裹找到标定的物品，逐个使用/丢弃/出售。是跨任务共享能力，"
                                     "任何任务流程都可穿插调用；这里可单独一键运行。",
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub)
        ctk.CTkLabel(txt, text="将整理：" + self._targets_summary(cfg),
                     font=self.fonts["small"], text_color=T.TEXT_DIM).pack(anchor="w", pady=(4, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns, text="标定（整理背包）", font=self.fonts["body"], height=36, width=130,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._open_organize_calibrate).pack()
        ctk.CTkButton(btns, text="管理物品", font=self.fonts["body"], height=32, width=130,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._open_organize_items).pack(pady=(6, 0))

        ctk.CTkFrame(c, fg_color=T.BORDER, height=1).pack(fill="x", padx=16, pady=(10, 0))
        act = ctk.CTkFrame(c, fg_color="transparent")
        act.pack(fill="x", padx=16, pady=(10, 14))
        self.btn_ob = ctk.CTkButton(act, text="▶  一键整理", font=self.fonts["btn"], height=40, width=150,
                                    corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                    text_color=T.ON_ACCENT, command=self._start_organize)
        self.btn_ob.pack(side="left")

        # 物品清单为空时单独提示（按钮标完 ≠ 能整理：一键整理的目标在「管理物品」里）
        if not items:
            warn_row = ctk.CTkFrame(c, fg_color="transparent")
            warn_row.pack(fill="x", padx=16, pady=(0, 8))
            ctk.CTkLabel(warn_row, text="⚠ 物品清单还是空的 —— 请点「管理物品」框选要整理的道具并设动作（如丢弃/商会出售），"
                                        "否则一键整理没有目标。",
                         font=self.fonts["small"], text_color=T.WARN, justify="left").pack(anchor="w")

        # 「自动整理背包」：跨任务全局开关。开了后，任何走多开轮转的任务（运镖/宝图/秘境/副本）
        # 运行中每隔一会儿检测一次背包「满」图标，满了就自动整理一遍（真整理/只识别跟随上面的实战开关）。
        auto_row = ctk.CTkFrame(c, fg_color="transparent")
        auto_row.pack(fill="x", padx=16, pady=(0, 12))
        self.switch_auto_ob = ctk.CTkSwitch(auto_row, text="自动整理背包（任何任务检测到背包满就自动清）",
                                            font=self.fonts["body"], command=self._toggle_auto_organize)
        self.switch_auto_ob.pack(anchor="w")
        if ob_tc.get("auto_organize"):
            self.switch_auto_ob.select()
        else:
            self.switch_auto_ob.deselect()
        auto_hint = ctk.CTkLabel(auto_row, text="开启后，运镖 / 宝图 / 秘境 / 副本等任务运行中会每隔一会儿检测一次背包"
                                                "「满」图标，满了就自动整理一遍 —— 需先在「标定（整理背包）」里框选"
                                                "『背包满图标』，否则无从判断、不会触发。",
                                 font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        auto_hint.pack(fill="x", anchor="w", pady=(2, 0))
        bind_wraplength(auto_hint)

        # 重建后：若整理在跑，恢复「停止整理」文案/颜色（照 btn_team 的恢复写法）
        if self.runner_ob and self.runner_ob.is_running():
            self.btn_ob.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)

    def _open_organize_calibrate(self):
        """打开整理背包标定（共享命名空间 organize_bag），按 _ob_cal_dialog 去重。"""
        existing = self._ob_cal_dialog
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return
            except Exception:
                pass
        from ..calibrate_dialog import CalibrateDialog

        def _after():
            self._ob_cal_dialog = None
            self.refresh()

        try:
            self._ob_cal_dialog = CalibrateDialog(self.app, task_name="organize_bag", on_done=_after)
        except Exception as e:
            self._ob_cal_dialog = None
            self.app.toast(f"打开整理背包标定失败：{e}")

    def _open_organize_items(self):
        """打开「管理物品」弹窗：增删物品、改每件的动作（使用/丢弃/出售）。关闭后刷新完成度。"""
        from ..inventory_items_dialog import InventoryItemsDialog
        try:
            InventoryItemsDialog(self.app, on_done=self.refresh)
        except Exception as e:
            self.app.toast(f"打开物品管理失败：{e}")

    def _toggle_auto_organize(self):
        """「自动整理背包」开关：存 tasks.organize_bag.auto_organize（任何任务流程检测到背包满自动整理）。"""
        on = bool(self.switch_auto_ob.get())
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "organize_bag")
        tc["auto_organize"] = on
        cfg_mod.set_task_config(cfg, "organize_bag", tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = self.cfg = cfg
        if on:
            tpl_ok = bool((tc.get("templates", {}) or {}).get("bag_full_icon"))
            self._log_line("已开启「自动整理背包」：任务流程中检测到背包满会自动整理。"
                           + ("" if tpl_ok else " ⚠ 但还没标定『背包满图标』，请先去「标定（整理背包）」框选，否则不会触发。"),
                           "warn" if not tpl_ok else "info", "整理背包")
        else:
            self._log_line("已关闭「自动整理背包」。", "info", "整理背包")

    def _start_organize(self):
        if self.runner_ob and self.runner_ob.is_running():
            self.runner_ob.stop()
            self._log_line("正在停止整理…", "warn", "整理背包")
            self.btn_ob.configure(text="停止中…", state="disabled")
            return
        # dry_run 由开关控制，这里不强改；只读最新配置开跑。
        cfg = cfg_mod.load_config()
        self.app.cfg = self.cfg = cfg
        task_cls = get_task("organize_bag")
        if task_cls is None:
            self._log_line("找不到整理背包任务。", "error", "整理背包")
            return
        self.runner_ob = TaskRunner(task_cls(), self.app.cfg)
        ok, problems = self.runner_ob.start()
        if not ok:
            for p in problems:
                self._log_line("无法开始整理：" + p, "error", "整理背包")
            self.runner_ob = None
            return
        self._log_line("开始一键整理背包…", "hit", "整理背包")
        self.btn_ob.configure(text="■  停止整理", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _on_ob_finished(self):
        if self.btn_ob is not None:
            self.btn_ob.configure(text="▶  一键整理", fg_color=T.ACCENT,
                                  hover_color=T.ACCENT_HOVER, state="normal")

    def _log_line(self, msg, level="info", source=None):
        # 日志统一汇到 App 右侧全局面板；source 缺省用本页 LOG_SOURCE（「通用」），
        # 组队/整理背包在 pump 与各自的开始/停止消息里显式传「组队」「整理背包」。
        self.app.log_line(msg, level, source or self.LOG_SOURCE)
