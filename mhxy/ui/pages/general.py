# -*- coding: utf-8 -*-
"""通用页：跨任务功能（公共区域标定〔活动/背包列表 + 商城/活动图标〕、组队标定+一键组队/一键解散、窗口尺寸归一化）。
整理背包已迁到「工具」分类页（OrganizeBagPage，见 organize_bag.py）；拓印标定也迁到「工具」页「拓印」（TuoyingPage）。
独立页面类，由 App 统一导入（App.PAGE_CLASSES）。"""

import threading

import customtkinter as ctk

from .. import theme as T
from ...core import config as cfg_mod
from ...core import window as win_mod
from ...core.runner import TaskRunner
from ...tasks import get_task
from ...core.teaming import TEAM_REQUIRED_REGIONS, TEAM_CALIB_TPL_KEYS
from ...core.config import (SHARED_REGION_KEYS, MAIN_ICON_TPL_KEYS, BATTLE_FLAG_TPL_KEY, CLOCK_TPL_KEY)
from ..common import Card, load_thumb, bind_wraplength


class GeneralPage(ctk.CTkFrame):
    """通用页：集中放与具体任务无关的功能。
    目前：窗口尺寸归一化；公共区域标定（活动/背包列表，全任务共用）；组队标定 + 一键组队（选队长→把所选多开窗口组成一队）。
    整理背包入口已移到「工具」页（OrganizeBagPage）。"""

    LOG_SOURCE = "通用"   # 组队日志在 pump 里各自覆盖来源标签

    def __init__(self, parent, app):
        super().__init__(parent, fg_color="transparent")
        self.app = app
        self.fonts = app.fonts
        self.cfg = app.cfg
        self.runner = None          # 一键组队跑的后台任务（DungeonTask）
        self.runner_db = None       # 一键解散跑的后台任务（DisbandTask），与组队互斥（同用鼠标/队伍面板）
        self._team_cal_dialog = None    # 「标定（组队）」去重槽（队长ID 走无弹窗直接标定，无需去重槽）
        self._shared_cal_dialog = None  # 「标定（公共区域）」去重槽
        self.btn_team = None
        self._enum_pending = None   # 窗口枚举结果暂存：worker 线程写，主线程 pump 取走渲染
        self._win_count = 0         # 已选多开窗口数（resolve_targets），供状态行显示
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
        sub = ctk.CTkLabel(head, text="跨任务的通用功能：公共区域标定（活动/背包列表 + 组队标定，全任务共用）+ 一键组队 / 一键解散 / 还原窗口尺寸。各任务专属的标定与参数在「任务配置」页（运行唯一入口在「日常」页）。",
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
        sub2 = ctk.CTkLabel(txt2, text="「还原尺寸」把所有窗口拉回基准尺寸；「调整窗口」（在「日常」页"
                                       "点开始左侧）把所选窗口（最多5个）按基准尺寸排成 2列×2行 —— 第1排贴屏幕顶、"
                                       "最后1排贴任务栏、最左列距左侧留空白（config.arrange_left_margin，默认100像素）、"
                                       "第5个居中。脚本点位按此基准尺寸标定。在下面窗口列表点「设为基准」来设定基准尺寸。",
                            font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub2.pack(fill="x", pady=(2, 0))
        bind_wraplength(sub2)
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="还原尺寸", font=self.fonts["body"], height=36, width=100,
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

        # ── 公共区域（全局共享：活动列表/背包列表 + 商城/活动图标，任意任务标一次全任务通用）──
        c_shared = self._card()
        head_s = ctk.CTkFrame(c_shared, fg_color="transparent")
        head_s.pack(fill="x", padx=16, pady=(14, 4))
        head_s.grid_columnconfigure(0, weight=1)
        txt_s = ctk.CTkFrame(head_s, fg_color="transparent")
        txt_s.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_s, text="公共区域（全局共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        # 区域：活动列表 / 背包列表（必标）。战斗标识（运镖/宝图必标）、小闹钟（副本/抓鬼必标）；
        # 商城/活动图标（可选）只计数展示、不拖累「已就绪」。
        # 拓印临摹资产已迁到「工具」页「拓印」（tasks.tuoying），不在这里标。
        shared_tc = cfg_mod.task_config(cfg, "shared")
        sreg = shared_tc.get("regions", {})
        shared_region_keys = SHARED_REGION_KEYS
        sdone = sum(1 for k in shared_region_keys if sreg.get(k))
        sready = sdone == len(shared_region_keys)
        stpl = shared_tc.get("templates", {})
        shared_tpl_keys = (BATTLE_FLAG_TPL_KEY, CLOCK_TPL_KEY) + MAIN_ICON_TPL_KEYS
        tdone = sum(1 for k in shared_tpl_keys if stpl.get(k))
        # 商城/活动图标（主界面判定用）是可选项：只计数展示，不拖累「已就绪」（不标=无法做主界面判定，任务照常跑）。
        # 战斗标识运镖/宝图必标、小闹钟副本/抓鬼必标（各任务 preflight 也会拦），缺了这里不显示「已就绪」。
        already = (sready and bool(stpl.get(BATTLE_FLAG_TPL_KEY))
                   and bool(stpl.get(CLOCK_TPL_KEY)))
        ctk.CTkLabel(txt_s, text=f"区域：{sdone}/{len(shared_region_keys)}　标志模板：{tdone}/{len(shared_tpl_keys)}"
                                 + ("　✓ 已就绪" if already else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if already else T.WARN).pack(anchor="w", pady=(4, 0))
        sub_s = ctk.CTkLabel(txt_s, text="「活动」界面那一片卡片列表、打开背包后那一片物品列表，几乎所有任务的画面都一样——"
                                        "在这里框一次，宝图 / 运镖 / 秘境降妖 / 三界奇缘 / 抓鬼 / 刷副本 / 整理背包自动通用，"
"不用每个任务各标一遍。「任务配置」页里的同名两项也会自动显示共用。"
                                         "「战斗界面标志」是进战斗后的画面元素（运镖/宝图必标，否则一进战斗就误判结束；秘境仅日志用）。"
                                         "「小闹钟」是任务栏那个寻路图标（刷副本/抓鬼必标：副本内寻路、每轮收尾和抓鬼点任务条目寻路都靠它）。"
                                         "「商城/活动图标」用来判断是否回到主界面：把主界面顶部的商城、活动按钮各框一次即可。"
                                        "「拓印」临摹（刷副本偶发的描图案校验）的标题/上传/绘制区已移到「工具」页「拓印」里标。",
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
        tdone = sum(1 for k in TEAM_CALIB_TPL_KEYS if ttpl.get(k))
        calib_ready = (rdone == len(TEAM_REQUIRED_REGIONS) and tdone == len(TEAM_CALIB_TPL_KEYS))
        ctk.CTkLabel(txt_t, text=f"组队标定：区域 {rdone}/{len(TEAM_REQUIRED_REGIONS)}，"
                                 f"模板 {tdone}/{len(TEAM_CALIB_TPL_KEYS)}"
                                 + ("　✓ 已就绪" if calib_ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if calib_ready else T.WARN).pack(anchor="w", pady=(4, 0))
        self.lbl_leader_status = ctk.CTkLabel(txt_t, text="", font=self.fonts["body"])
        self.lbl_leader_status.pack(anchor="w", pady=(2, 0))
        self._refresh_leader_status()
        sub_t = ctk.CTkLabel(txt_t, text="队长建队→队员申请→接受→关窗，是跨任务的共享能力。"
                                        "刷副本等任何用到组队的任务都自动读这份标定"
                                        "（创建/申请/接受/申请入队 + 好友列表区/队伍面板区）。"
                                        "「箭头」可选、不标也能用固定偏移兜底；「退出队伍」是解散用的。",
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

        # 日志已统一到 App 右侧的全局日志面板（组队打「组队」标签），本页不再单独建日志框。
        # 重建后：按窗口数即时渲染队长下拉/状态 + 据 runner 复位按钮，再后台刷新窗口数
        self._render_team_action()
        if self.runner and self.runner.is_running():
            self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        if self.runner_db and self.runner_db.is_running():
            self.btn_disband.configure(text="■  停止解散", fg_color=T.DANGER, hover_color=T.DANGER_HOVER)
        self._kick_count_windows()

    def _kick_enum_windows(self, holder, base):
        """后台枚举窗口，完成后把结果暂存 _enum_pending，由主线程的 pump() 取出渲染。

        为什么不用后台线程直接 app.after(0, ...)：从非主线程调 Tk 的 after 在启动早期
        （主线程尚在 __init__、未进入 mainloop 的瞬间）会抛 RuntimeError 被吞掉、回调永久丢失，
        导致窗口列表永远停在「正在检测窗口…」（用户首开必现、手点「刷新」又能出）。改为 worker
        只写实例字段、主线程 pump 轮询，彻底绕开跨线程摸 Tk。"""
        cfg = self.cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        token = object()
        self._enum_token = token
        self._enum_empty_retries = 0

        def work():
            try:
                wins = win_mod.locate_all(title, offset)
                data = [(w, w.rect()) for w in wins]
            except Exception:
                data = []
            self._enum_pending = (holder, base, data, token)

        threading.Thread(target=work, daemon=True).start()

    def _drain_enum(self):
        """主线程 pump() 每帧调用：把 worker 暂存的窗口枚举结果渲染出来。无结果则不动。"""
        pend = getattr(self, "_enum_pending", None)
        if pend is None:
            return
        self._enum_pending = None
        holder, base, data, token = pend
        try:
            self._fill_win_rows(holder, data, base, token)
        except Exception:
            pass

    def _fill_win_rows(self, holder, data, base, token):
        """在主线程把枚举结果渲染进 holder。过期结果/控件已销毁则丢弃。
        空结果（开局时偶然的瞬时读空）自动重试几次再放弃，不必等用户手动点「刷新」。"""
        if token is not getattr(self, "_enum_token", None):
            return
        try:
            if not holder.winfo_exists():
                return
        except Exception:
            return
        if not data:
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
        """打开公共区域标定（共享命名空间 shared：活动/背包列表区域 + 商城/活动图标，全局共用），按 _shared_cal_dialog 去重。
        拓印临摹资产在「工具」页「拓印」里标（tasks.tuoying），不在这里。"""
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

    def _refresh_leader_status(self):
        """刷新卡片上「队长ID」独立一行状态（新建/切换队长ID后由广播刷新）。"""
        lbl = getattr(self, "lbl_leader_status", None)
        if lbl is None:
            return
        team_tc = cfg_mod.task_config(self.app.cfg, "teaming")
        ok = bool((team_tc.get("templates") or {}).get("leader_id"))
        lbl.configure(text=("队长ID：✓ 已标定" if ok else "队长ID：⚠ 未标定（点「标定队长ID」）"),
                      text_color=T.SUCCESS if ok else T.WARN)

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
        窗口/队长由「选择窗口」对话框（左侧栏目标窗口状态点开）里选定。"""
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
            tip = "组队需多开：请从左侧栏点目标窗口状态进「选择窗口」，切到多开并勾 2~5 个号。"
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
        self._drain_enum()   # 窗口列表枚举结果：worker 写字段、这里主线程取出渲染
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

    def _log_line(self, msg, level="info", source=None):
        # 日志统一汇到 App 右侧全局面板；source 缺省用本页 LOG_SOURCE（「通用」），
        # 组队/解散在 pump 与各自的开始/停止消息里显式传「组队」「解散」。
        self.app.log_line(msg, level, source or self.LOG_SOURCE)
