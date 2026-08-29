import customtkinter as ctk
from .task_component_base import TaskComponentBase
from .. import theme as T
from .. import util

from ...core import config as cfg_mod
from ...core.runner import TaskRunner
from ...tasks import get_task


class Team(TaskComponentBase):
    def __init__(self, master, main_view):
        super().__init__(master, main_view)
        self.runner = None
        self._leader_thumbs = []
        self.init()

    def init(self):
        # ── 组队（跨任务共享：任何用到组队的任务都自动读这份标定）──
        c_team = util.card(self.master)
        c_team.pack(fill="x", pady=(0, T.SP_3), padx=2)
        head_t = ctk.CTkFrame(c_team, fg_color="transparent")
        head_t.pack(fill="x", padx=16, pady=(14, 4))
        head_t.grid_columnconfigure(0, weight=1)
        txt_t = ctk.CTkFrame(head_t, fg_color="transparent")
        txt_t.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt_t, text="组队（共享）", font=self.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        team_tc = cfg_mod.task_config(self.cfg, "teaming")
        treg, ttpl = team_tc.get("regions", {}), team_tc.get("templates", {})
        trg_keys = list(treg.keys())
        ttpl_keys = list(ttpl.keys())
        rdone = sum(1 for k in trg_keys if treg.get(k))
        tdone = sum(1 for k in ttpl_keys if ttpl.get(k))
        ready = (rdone == len(trg_keys) and tdone == len(ttpl_keys))
        ctk.CTkLabel(txt_t, text=f"组队标定：必要区域 {rdone}/{len(trg_keys)}，"
                                 f"必要模板 {tdone}/{len(ttpl_keys)}"
                                 + ("　✓ 已就绪" if ready else "　（还需标定）"),
                     font=self.fonts["body"],
                     text_color=T.SUCCESS if ready else T.WARN).pack(anchor="w", pady=(4, 0))
        sub_t = ctk.CTkLabel(txt_t, text="队长建队→队员申请→接受→关窗，是跨任务的共享能力。"
                                         "刷副本等任何用到组队的任务都自动读这份标定"
                                         "（队长ID、创建/申请/接受/申请入队、好友列表区/队伍面板区等）。",
                             font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub_t.pack(fill="x", pady=(2, 0))
        T.bind_wraplength(sub_t)
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
                      command=lambda: self.open_window_picker(self.refresh, captain_ns="teaming")).pack(
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
        T.bind_wraplength(self.lbl_team_status)

    def _open_team_calibrate(self):
        """打开组队标定（共享命名空间 teaming）。队长ID 已移到「标定队长ID」按钮单独标，这里不再列出。"""
        self.calib_singleton("_team_cal_dialog", None, "打开组队标定失败", exclude=["leader_id"])

    def _refresh_leader_btn(self):
        """重读激活队长ID图，更新行内按钮缩略图（无图则回退纯文字「标定队长ID」）。"""
        btn = getattr(self, "btn_leader", None)
        if btn is None:
            return
        self._leader_thumbs.clear()
        img = util.load_thumb("templates/tm_leader_id.png", self._leader_thumbs, max_h=26)
        try:
            btn.configure(image=img, text=" 队长ID" if img is not None else "标定队长ID")
        except Exception:
            pass

    def _render_team_action(self):
        """据当前 self._win_count + teaming.captain_index 渲染队长状态行（纯本地数据，秒回）。
        队长已挪进「选择窗口/队长」里选（带缩略图），这里只读出来显示。"""
        if self.lbl_team_status is None:
            return
        n = self._win_count
        team_tc = cfg_mod.task_config(self.cfg, "teaming")
        cap = team_tc.get("captain_index", 0)
        if not (0 <= cap < n):
            cap = 0
        multi = self.cfg.get("targets", {}).get("multi", False)
        if n >= 2:
            tip = f"已选 {n} 个号，队长=第{cap + 1}个所选号，其余当队员。点「一键组队」开始。"
        elif not multi:
            tip = "组队需多开：请点「选择窗口/队长」切到多开、勾 2~5 个号并指定队长。"
        else:
            tip = f"已选 {n} 个号，组队至少 2 个号（队长+≥1 队员）。"
        self.lbl_team_status.configure(text=tip)

    def _start_teaming(self):
        if self.runner and self.runner.is_running():
            self.runner.stop()
            self.log_line("正在停止…", "warn", "组队")
            self.btn_team.configure(text="停止中…", state="disabled")
            return
        # 强制实战（一键组队是显式动作，不走演练）。窗口选择与队长都在「选择窗口/队长」里定好了。
        cfg = cfg_mod.load_config()
        tc = cfg_mod.task_config(cfg, "teaming")
        tc["dry_run"] = False
        cfg_mod.set_task_config(cfg, "teaming", tc)
        cfg_mod.save_config(cfg)
        self.cfg = cfg

        task_cls = get_task("dungeon")
        if task_cls is None:
            self.log_line("找不到组队任务。", "error", "组队")
            return
        self.runner = TaskRunner(task_cls(), self.cfg)
        ok, problems = self.runner.start()
        if not ok:
            for p in problems:
                self.log_line("无法开始组队：" + p, "error", "组队")
            self.runner = None
            return
        self.log_line("开始一键组队…", "hit", "组队")
        self.btn_team.configure(text="■  停止组队", fg_color=T.DANGER, hover_color=T.DANGER_HOVER, state="normal")

    def _start_disband(self):
        pass

    def _open_leader_gallery(self):
        pass
