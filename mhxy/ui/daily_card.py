# -*- coding: utf-8 -*-
"""起始页「日常任务卡」：把「日常」页的运行控制（开始/停止 · 调整窗口/刷新 · 各设置开关）与
「已勾选任务」清单搬到起始页，做成「一眼可跑」的主面板。

设计要点（改这里前先读）：
- 运行仍由「日常」页单例持有的 runner 承载——本卡只是入口 + 状态镜像：点「开始日常」=
  调 ``DailyPage._toggle_run(save=False)``（设置已由本卡自行落盘，免得 DailyPage 用它内存里
  可能过期的开关值回写覆盖）；按钮文字/配色每 tick 从 DailyPage 的 runner 镜像（见 ``pump``）。
- 设置各写自己的配置命名空间（``tasks.daily.loop`` / ``tasks.teaming`` / ``tasks.organize_bag``），
  与「日常」页读同一份配置（唯一事实源）——切页时 ``App._show`` 会 refresh，两边自动同步。
- 「已勾选任务」清单**只读**：只列出当前启用、会真正跑的任务（按执行顺序编号 + 就绪状态），
  勾选/排序统一去「日常」页改，本卡不放开关（避免误触 + 两套交互）。
- 整卡可折叠：点标题/箭头收起内容（设置 + 清单），只留标题与「开始日常」按钮。
- 为什么不直接复用 DailyPage 的控件：Tk 控件不能跨页重挂父容器，只能按同一套配置键重建一套；
  故这里把「配置键 + 解散联动规则」收成模块级纯函数，UI 重建但逻辑单一来源。
"""

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..tasks import get_task
from ..tasks.daily import (CHAINABLE, GROUP_OF, GROUPS, get_selected_dungeon_titles)
from .common import Tooltip

# 区顺序：与「日常」页/引擎同源（tasks.daily.GROUPS）——多人组在前、个人组在后，区内按配置原序。
_GROUPS = GROUPS


def task_title(name, cfg=None):
    """任务显示名：刷副本展开为当前勾选的副本标题列表（按展示顺序），其余取任务类的 title。"""
    if name == "dungeon" and cfg is not None:
        titles = get_selected_dungeon_titles(cfg)
        if titles:
            return " -> ".join(titles)
        return "刷副本（未勾选）"
    cls = get_task(name)
    return cls.title if cls else name


def has_single_selected(cfg):
    """本趟流程里是否含「单人任务」（整组启用 且 至少勾了一个单人任务）——决定解散开关是否锁定。
    与 DailyPage._has_single_selected 同口径，读的是同一份配置。"""
    d = ((cfg.get("tasks", {}) or {}).get("daily", {}) or {})
    if not (d.get("group_enabled") or {}).get("single", True):
        return False
    for s in (d.get("steps") or []):
        if isinstance(s, dict) and s.get("enabled", True) and GROUP_OF.get(s.get("task")) == "single":
            return True
    return False


def ordered_steps(cfg):
    """把配置里的 steps 规整成「含全部可串联任务、按区拼段」的有序列表（与 DailyPage._normalize 同口径）。"""
    d = ((cfg.get("tasks", {}) or {}).get("daily", {}) or {})
    out, seen = [], set()
    for s in (d.get("steps") or []):
        if isinstance(s, dict) and s.get("task") in CHAINABLE and s["task"] not in seen:
            out.append({"task": s["task"], "enabled": bool(s.get("enabled", True))})
            seen.add(s["task"])
    for t in CHAINABLE:
        if t not in seen:
            out.append({"task": t, "enabled": True})
            seen.add(t)
    seg = {g: [s for s in out if GROUP_OF[s["task"]] == g] for g in _GROUPS}
    return [s for g in _GROUPS for s in seg.get(g, [])]


def enabled_names(cfg):
    """本趟真正会跑的任务名（按执行顺序）：行级启用 且 所属整组启用。与「日常」页 _refresh_exec_order
    / 引擎 _enabled_steps 同语义——整组或单行停用的都剔除。
    「刷副本」开着但一个副本都没勾 = 这步不跑（与整行关掉等效），同样剔除。"""
    d = ((cfg.get("tasks", {}) or {}).get("daily", {}) or {})
    ge = d.get("group_enabled") or {}
    return [s["task"] for s in ordered_steps(cfg)
            if s["enabled"] and ge.get(GROUP_OF[s["task"]], True)
            and not (s["task"] == "dungeon" and not get_selected_dungeon_titles(cfg))]


def enabled_count(cfg):
    """本趟真正会跑的执行项数量：刷副本展开为勾选的副本数量，其余任务各算 1。
    与 enabled_names 同语义，但 dungeon 展开计数。"""
    names = enabled_names(cfg)
    count = 0
    for name in names:
        if name == "dungeon":
            titles = get_selected_dungeon_titles(cfg)
            count += len(titles) if titles else 0
        else:
            count += 1
    return count


class DailyRunCard(ctk.CTkFrame):
    """日常运行控制卡（起始页用）。构造后由宿主页 refresh()/pump() 驱动。"""

    RUN_LABEL = "▶  开始日常"

    def __init__(self, master, app, fonts):
        super().__init__(master, fg_color="transparent")
        self.app = app
        self.fonts = fonts
        self.grid_columnconfigure(0, weight=1)
        self._open = True        # 内容（设置 + 清单）是否展开
        self._run_sig = None     # 上一次套到按钮上的 (文字, 风格) 签名，用于去抖（见 sync_run）

        self._build_head()
        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=1, column=0, sticky="ew")
        self._body.grid_columnconfigure(0, weight=1)
        ctk.CTkFrame(self._body, fg_color=T.BORDER, height=1).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self._build_settings(self._body)
        self._build_checklist(self._body)

    # ------------------------------------------------------------------
    # 头部（常驻）：折叠箭头 + 标题 + 计数 + 按钮
    # ------------------------------------------------------------------
    def _build_head(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", pady=(2, 6))
        head.grid_columnconfigure(2, weight=1)
        self._chev = ctk.CTkLabel(head, text="▾", font=self.fonts["h2"], text_color=T.TEXT_DIM,
                                  cursor="hand2", width=22, anchor="w")
        self._chev.grid(row=0, column=0, sticky="w")
        ttl = ctk.CTkLabel(head, text="🐉 日常任务", font=self.fonts["h2"], text_color=T.TEXT,
                           cursor="hand2", anchor="w")
        ttl.grid(row=0, column=1, sticky="w")
        for w in (self._chev, ttl):
            w.bind("<Button-1>", lambda e: self._toggle_body())
        Tooltip(ttl, "把「日常」页的运行控制与勾选任务搬到这里，一眼看清这趟会跑哪些、直接开跑。"
                     "点标题/箭头可收起或展开；勾选与设置与「日常」页同一份配置，改哪边都同步。", self.fonts)
        self.lbl_badge = ctk.CTkLabel(head, text="", font=self.fonts["small"], text_color=T.TEXT_DIM, anchor="w")
        self.lbl_badge.grid(row=0, column=2, sticky="w", padx=(10, 0))

        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=3, sticky="e")
        ctk.CTkButton(btns, text="调整窗口", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._adjust_windows).pack(side="left")
        ctk.CTkButton(btns, text="刷新配置", font=self.fonts["body"], height=36, width=104,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack(side="left", padx=(8, 0))
        self.btn_run = ctk.CTkButton(btns, text=self.RUN_LABEL, font=self.fonts["btn"], height=36, width=150,
                                     corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                     text_color=T.ON_ACCENT, command=self._toggle_run)
        self.btn_run.pack(side="left", padx=(8, 0))

    def _toggle_body(self):
        self._open = not self._open
        if self._open:
            self._body.grid()
            self._chev.configure(text="▾", text_color=T.TEXT_DIM)
        else:
            self._body.grid_remove()
            self._chev.configure(text="▸", text_color=T.TEXT_DIM)

    # ------------------------------------------------------------------
    # 设置开关（与「日常」页同一套配置键）
    # ------------------------------------------------------------------
    def _build_settings(self, parent):
        opts = ctk.CTkFrame(parent, fg_color="transparent")
        opts.grid(row=1, column=0, sticky="ew", pady=(4, 8))
        opts.grid_columnconfigure(0, weight=1)
        opts.grid_columnconfigure(1, weight=1)

        def _row(r, c):
            f = ctk.CTkFrame(opts, fg_color="transparent")
            f.grid(row=r, column=c, sticky="w", padx=(0, 26), pady=(8, 0))
            return f

        # 时间上限
        lim = _row(0, 0)
        lim_lbl = ctk.CTkLabel(lim, text="整体时间上限(分钟，0=不限)", font=self.fonts["body"], text_color=T.TEXT)
        lim_lbl.pack(side="left")
        self.var_limit = ctk.StringVar(value="0")
        self.lim_ent = ctk.CTkEntry(lim, textvariable=self.var_limit, width=70, font=self.fonts["body"],
                                    fg_color=T.SURFACE_2, border_color=T.BORDER)
        self.lim_ent.pack(side="left", padx=(8, 0))
        for w in (lim_lbl, self.lim_ent):
            Tooltip(w, "只是安全网：正常会按各子任务自身条件跑完。未就绪（缺标定/缺窗口）的任务会自动跳过。", self.fonts)
        self.lim_ent.bind("<FocusOut>", lambda e: self._save_loop())
        self.lim_ent.bind("<Return>", lambda e: self._save_loop())

        # 跑完解散
        dis = _row(0, 1)
        dis_lbl = ctk.CTkLabel(dis, text="跑完多人任务后解散队伍", font=self.fonts["body"], text_color=T.TEXT)
        dis_lbl.pack(side="left")
        self.var_disband = ctk.BooleanVar(value=False)
        self.sw_disband = ctk.CTkSwitch(dis, text="", variable=self.var_disband, width=44,
                                        progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                                        command=self._on_disband)
        self.sw_disband.pack(side="left", padx=(8, 0))
        Tooltip(dis_lbl, "跑完「多人任务」整段后自动让所有号退队，再转后续任务。步骤里包含「单人任务」组时，"
                         "此开关会被强制打开并锁定（单人步要在队伍外跑）。与「日常」页同一开关。", self.fonts)

        # 跑完关机
        shut = _row(1, 0)
        shut_lbl = ctk.CTkLabel(shut, text="跑完关机", font=self.fonts["body"], text_color=T.TEXT)
        shut_lbl.pack(side="left")
        self.var_shutdown = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(shut, text="", variable=self.var_shutdown, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_shutdown).pack(side="left", padx=(8, 0))
        Tooltip(shut_lbl, "整条龙跑完后自动关机（先进入关机倒计时，期间按「停止」或急停热键即可取消）", self.fonts)

        # 定时延后执行
        sched = _row(1, 1)
        sched_lbl = ctk.CTkLabel(sched, text="定时延后执行", font=self.fonts["body"], text_color=T.TEXT)
        sched_lbl.pack(side="left")
        self.var_schedule_on = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(sched, text="", variable=self.var_schedule_on, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_schedule).pack(side="left", padx=(8, 0))
        ctk.CTkLabel(sched, text="执行时刻(时:分)", font=self.fonts["body"],
                     text_color=T.TEXT_DIM).pack(side="left", padx=(4, 0))
        self.var_schedule_time = ctk.StringVar(value="10:00")
        time_ent = ctk.CTkEntry(sched, textvariable=self.var_schedule_time, width=64,
                                font=self.fonts["body"], fg_color=T.SURFACE_2, border_color=T.BORDER)
        time_ent.pack(side="left", padx=(6, 0))
        for w in (sched_lbl, time_ent):
            Tooltip(w, "开启后，点「开始日常」会等到设定时刻才真正开始；若该时刻已过则立即执行。"
                       "等待中再点一次可取消。与「日常」页同一开关。", self.fonts)

        # 自动整理背包
        auto = _row(2, 0)
        auto_lbl = ctk.CTkLabel(auto, text="自动整理背包", font=self.fonts["body"], text_color=T.TEXT)
        auto_lbl.pack(side="left")
        self.switch_auto_organize = ctk.CTkSwitch(
            auto, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
            command=self._on_auto_organize)
        self.switch_auto_organize.pack(side="left", padx=(8, 0))
        Tooltip(auto_lbl, "日常串跑在任务交接处检测背包「满」图标，满了自动整理一遍；需先在"
                          "「工具 › 整理背包」页标定『背包满图标』。与「日常」页同一开关。", self.fonts)

        # 已组队
        skip = _row(2, 1)
        skip_lbl = ctk.CTkLabel(skip, text="已组队（跳过自动组队）", font=self.fonts["body"], text_color=T.TEXT)
        skip_lbl.pack(side="left")
        self.var_skip_team = ctk.BooleanVar(value=False)
        ctk.CTkSwitch(skip, text="", variable=self.var_skip_team, width=44,
                      progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
                      command=self._on_skip_team).pack(side="left", padx=(8, 0))
        Tooltip(skip_lbl, "已在游戏里自行组好队就勾上：刷副本/抓鬼等多人步跳过脚本自动组队，直接由队长开跑。"
                          "与「日常」页同一开关。", self.fonts)

        # 自动拓印（全局开关，存 tasks.tuoying.enabled）：关掉后整条链里遇到拓印弹窗不自动描摹、转手动
        tuo = _row(3, 0)
        tuo_lbl = ctk.CTkLabel(tuo, text="自动拓印（关闭后遇拓印弹窗转手动）", font=self.fonts["body"], text_color=T.TEXT)
        tuo_lbl.pack(side="left")
        self.switch_auto_tuoying = ctk.CTkSwitch(
            tuo, text="", width=44, progress_color=T.ACCENT, fg_color=T.BTN, button_color=T.ON_ACCENT,
            command=self._on_auto_tuoying)
        self.switch_auto_tuoying.pack(side="left", padx=(8, 0))
        Tooltip(tuo_lbl, "开启后（默认）：日常链/刷副本/宝图遇到「拓印」临摹弹窗会自动描摹并点「上传」。"
                         "关闭后：改为等你手动描完点「上传」，脚本检测到界面消失后自动继续。与「日常」页同一开关。",
                self.fonts)

    # ------------------------------------------------------------------
    # 已勾选任务清单（只读）：一条「刷副本 -> 抓鬼 -> 宝图 ...」的执行链 + 就绪摘要
    # ------------------------------------------------------------------
    def _build_checklist(self, parent):
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=2, column=0, sticky="ew", pady=(2, 2))
        box.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(head, text="已勾选任务", font=self.fonts["body_b"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="去「日常」页 →", font=self.fonts["small"], height=28, width=120,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._goto_daily).grid(row=0, column=2, sticky="e")

        self.lbl_chain = ctk.CTkLabel(box, text="", font=self.fonts["body"], text_color=T.ACCENT,
                                      anchor="w", justify="left")
        self.lbl_chain.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        T.bind_wraplength(self.lbl_chain)
        Tooltip(self.lbl_chain, "点「开始日常」会依次跑的任务，按实际执行先后拼接（多人组在前、单人组在后）。"
                                "勾选/排序在「日常」页调整。", self.fonts)

        self.lbl_ready = ctk.CTkLabel(box, text="", font=self.fonts["small"], text_color=T.TEXT_DIM,
                                      anchor="w", justify="left")
        self.lbl_ready.grid(row=2, column=0, sticky="ew", pady=(3, 0))
        T.bind_wraplength(self.lbl_ready)

    def _render_flow(self):
        """重绘执行链 + 就绪摘要（就地改文本，不重建控件）。"""
        names = enabled_names(self.app.cfg)
        if names:
            chain_text = " -> ".join(task_title(n, self.app.cfg) for n in names) + f" (共{enabled_count(self.app.cfg)}项)"
            self.lbl_chain.configure(text=chain_text, text_color=T.ACCENT)
        else:
            self.lbl_chain.configure(text="（无，开始日常只会空转）", text_color=T.TEXT_DIM)
        not_ready = [task_title(n, self.app.cfg) for n in names if not self._ready_bool(n)]
        if not names:
            self.lbl_ready.configure(text="")
        elif not_ready:
            self.lbl_ready.configure(text="⚠ 未就绪：" + "、".join(not_ready) + "（去「日常」页看该行为何未就绪）",
                                     text_color=T.WARN)
        else:
            self.lbl_ready.configure(text="✓ 全部就绪", text_color=T.SUCCESS)
        self._refresh_badge()

    def _ready_bool(self, name):
        """单任务就绪布尔：委托「日常」页的 _task_status（避免两处各算一套标定判定）。"""
        page = self.app.pages.get("daily")
        if page is not None and hasattr(page, "_task_status"):
            try:
                return bool(page._task_status(name))
            except Exception:
                return True
        return True

    def _refresh_badge(self):
        n = len(enabled_names(self.app.cfg))
        self.lbl_badge.configure(text=f"已勾选 {n} 个" if n else "未勾选任务",
                                 text_color=T.ACCENT if n else T.TEXT_DIM)

    # ------------------------------------------------------------------
    # 配置读写（各写自己的命名空间；读盘再改，避免覆盖别处刚写入的）
    # ------------------------------------------------------------------
    def _save_loop(self):
        cfg = cfg_mod.load_config()
        loop = cfg.setdefault("tasks", {}).setdefault("daily", {}).setdefault("loop", {})
        try:
            loop["time_limit_min"] = max(0.0, round(float(self.var_limit.get()), 1))
        except (TypeError, ValueError):
            pass
        loop["shutdown_after"] = bool(self.var_shutdown.get())
        loop["schedule"] = self._read_schedule()
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _read_schedule(self):
        if not self.var_schedule_on.get():
            return ""
        raw = self.var_schedule_time.get().strip().replace("：", ":")
        try:
            hh, mm = raw.split(":")
            hh, mm = max(0, min(23, int(hh))), max(0, min(59, int(mm)))
            return f"{hh:02d}:{mm:02d}"
        except (ValueError, TypeError):
            return ""

    def _save_teaming(self, **kw):
        cfg = cfg_mod.load_config()
        cfg.setdefault("tasks", {}).setdefault("teaming", {}).update(kw)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _save_auto_organize(self, on):
        cfg = cfg_mod.load_config()
        cfg.setdefault("tasks", {}).setdefault("organize_bag", {})["auto_organize"] = on
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg

    def _apply_disband_lock(self):
        """单人任务在流程中 → 强制开并锁定解散开关（并落盘），与「日常」页联动规则一致。"""
        locked = has_single_selected(self.app.cfg)
        if locked:
            self.var_disband.set(True)
        self.sw_disband.configure(state="disabled" if locked else "normal")
        if locked and not bool((self.app.cfg.get("tasks", {}).get("teaming", {}) or {}).get("auto_disband", False)):
            self._save_teaming(auto_disband=True)

    def _log(self, msg, level="info"):
        self.app.log_line(msg, level, "日常")

    # ------------------------------------------------------------------
    # 开关回调
    # ------------------------------------------------------------------
    def _on_shutdown(self):
        self._save_loop()
        self._log("已勾选「跑完关机」：整条龙跑完后进入关机倒计时。" if self.var_shutdown.get()
                  else "已取消「跑完关机」。", "warn" if self.var_shutdown.get() else "info")

    def _on_schedule(self):
        self._save_loop()
        if self.var_schedule_on.get():
            self._log(f"已开启定时延后执行：点「开始日常」会等到 {self.var_schedule_time.get()} 才真正执行"
                      "（若该时刻已过则立即执行）。", "warn")
        else:
            self._log("已关闭定时延后执行：点「开始日常」立即执行。", "info")

    def _on_disband(self):
        on = bool(self.var_disband.get())
        if has_single_selected(self.app.cfg):
            on = True
            self.var_disband.set(True)
        self._save_teaming(auto_disband=on)
        self._log("已开启：跑完多人任务后自动解散队伍。" if on
                  else "已关闭：跑完多人任务后保留队伍（不主动解散）。", "info")

    def _on_skip_team(self):
        on = bool(self.var_skip_team.get())
        self._save_teaming(skip_team=on)
        self._render_flow()
        self._log("已开启：副本/抓鬼 视为已组好队，跳过自动组队。" if on
                  else "已关闭：多人任务运行前会先自动组队。", "info")

    def _on_auto_organize(self):
        on = bool(self.switch_auto_organize.get())
        self._save_auto_organize(on)
        self._log("已开启「自动整理背包」。" if on else "已关闭「自动整理背包」。", "info")

    def _on_auto_tuoying(self):
        on = bool(self.switch_auto_tuoying.get())
        cfg = cfg_mod.load_config()
        cfg.setdefault("tasks", {}).setdefault("tuoying", {})["enabled"] = on
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._log(("已开启「自动拓印」：遇拓印弹窗会自动描摹并点「上传」。" if on
                   else "已关闭「自动拓印」：遇拓印弹窗不再自动描摹，请手动描完点「上传」，脚本会自动续跑。"),
                  "warn" if not on else "info")

    # ------------------------------------------------------------------
    # 运行控制（镜像 DailyPage 的 runner）
    # ------------------------------------------------------------------
    def _daily_page(self):
        try:
            page, _just = self.app._ensure_page("daily")
            return page
        except Exception:
            return None

    def _goto_daily(self):
        try:
            self.app._show("daily")
        except Exception:
            pass

    def _adjust_windows(self):
        page = self._daily_page()
        if page is not None and hasattr(page, "_adjust_windows"):
            page._adjust_windows()

    def _toggle_run(self):
        page = self._daily_page()
        if page is None:
            return
        # 先在点击瞬间落盘本卡的设置（时间上限输入框可能还没失焦），再让 DailyPage 直接按配置启动。
        self._save_loop()
        page._toggle_run(save=False)
        self.sync_run()

    def sync_run(self):
        """按钮状态镜像：读「日常」页 runner 的状态，套到本卡按钮上（每 tick 调）。

        ⚠ 必须**只在签名变化时**才 configure：customtkinter 的 CTkButton.configure 只要收到
        fg_color/hover_color 就无条件整只重绘（ctk_button.py 里 fg_color/hover_color 分支直接
        置 require_redraw=True），每 tick 无脑 configure 会让可见页的那一行以 ~6.7Hz 频闪。
        故这里先算出目标 (文字, 风格) 签名，与上次相同就直接返回（对齐 sniper._update_wait_button /
        App._refresh_run_status 的同款去抖写法）。"""
        page = self.app.pages.get("daily")
        if page is None or not hasattr(page, "btn_run"):
            return
        try:
            txt = page.btn_run.cget("text")
        except Exception:
            return
        if txt == getattr(page, "RUN_LABEL", self.RUN_LABEL):
            sig = (self.RUN_LABEL, "accent")
        elif txt.startswith("等待到"):
            sig = (txt, "warn")
        else:
            sig = (txt, "danger")
        if sig == self._run_sig:
            return
        self._run_sig = sig
        if sig[1] == "accent":
            self.btn_run.configure(text=self.RUN_LABEL, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, state="normal")
        elif sig[1] == "warn":
            self.btn_run.configure(text=txt, fg_color=T.WARN, hover_color=T.WARN_HOVER, state="normal")
        else:
            self.btn_run.configure(text=txt, fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    # ------------------------------------------------------------------
    # 宿主页驱动
    # ------------------------------------------------------------------
    def refresh(self):
        self.app.cfg = cfg_mod.load_config()
        cfg = self.app.cfg
        loop = ((cfg.get("tasks", {}).get("daily", {}) or {}).get("loop", {}) or {})
        self.var_limit.set(str(loop.get("time_limit_min", 0)))
        self.var_shutdown.set(bool(loop.get("shutdown_after", False)))
        sched = loop.get("schedule", "") or ""
        self.var_schedule_on.set(bool(sched))
        if sched and ":" in sched:
            self.var_schedule_time.set(sched)
        t = (cfg.get("tasks", {}).get("teaming", {}) or {})
        self.var_disband.set(bool(t.get("auto_disband", False)))
        self.var_skip_team.set(bool(t.get("skip_team", False)))
        ob = (cfg.get("tasks", {}).get("organize_bag", {}) or {})
        if self.switch_auto_organize is not None:
            (self.switch_auto_organize.select if ob.get("auto_organize") else self.switch_auto_organize.deselect)()
        tuo = (cfg.get("tasks", {}).get("tuoying", {}) or {})
        if getattr(self, "switch_auto_tuoying", None) is not None:
            (self.switch_auto_tuoying.select if tuo.get("enabled", True) else self.switch_auto_tuoying.deselect)()
        self._render_flow()
        self._apply_disband_lock()
        self.sync_run()

    def pump(self):
        self.sync_run()
