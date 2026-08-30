import customtkinter as ctk
import threading

from .task_component_base import TaskComponentBase

from .. import util
from .. import theme as T
from ..data import data

from ...core import window as win_mod
from ...core import config as cfg_mod


# ── 窗口尺寸归一化 ──
class Normalize(TaskComponentBase):
    def __init__(self, master, main_view):
        super().__init__(master, main_view)
        self.runner = None  # 一键组队跑的后台任务（DungeonTask）
        self.runner_db = None  # 一键解散跑的后台任务（DisbandTask），与组队互斥（同用鼠标/队伍面板）
        self.init()

    def init(self):
        targets = data.cfg.get("targets", {})
        self.base_size = targets.get("base_size")
        c2 = util.card(self.master)
        c2.pack(fill="x", pady=(0, T.SP_3), padx=2)
        head2 = ctk.CTkFrame(c2, fg_color="transparent")
        head2.pack(fill="x", padx=16, pady=(14, 4))
        head2.grid_columnconfigure(0, weight=1)
        txt2 = ctk.CTkFrame(head2, fg_color="transparent")
        txt2.grid(row=0, column=0, sticky="ew")
        ctk.CTkLabel(txt2, text="窗口尺寸归一化", font=T.fonts["h2"], text_color=T.TEXT).pack(anchor="w")
        base_txt = f"{int(self.base_size[0])}×{int(self.base_size[1])}" if self.base_size and len(
            self.base_size) >= 2 else "未设置"
        ctk.CTkLabel(txt2, text=f"当前基准尺寸：{base_txt}", font=T.fonts["body"],
                     text_color=T.TEXT if self.base_size else T.WARN).pack(anchor="w", pady=(4, 0))
        sub2 = ctk.CTkLabel(txt2, text="点「还原尺寸」把所有窗口拉回基准尺寸（脚本点位按此尺寸标定）。"
                                       "在下面窗口列表点「设为基准」来设定基准尺寸。",
                            font=T.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub2.pack(fill="x", pady=(2, 0))
        T.bind_wraplength(sub2)
        btns2 = ctk.CTkFrame(head2, fg_color="transparent")
        btns2.grid(row=0, column=1, padx=(12, 0))
        ctk.CTkButton(btns2, text="一键调整", font=T.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self.layout_wins).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="还原尺寸", font=T.fonts["body"], height=36, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._normalize_now).pack(pady=(0, 6))
        ctk.CTkButton(btns2, text="刷新", font=T.fonts["body"], height=30, width=100,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.refresh).pack()

        if not (self.base_size and len(self.base_size) >= 2):
            ctk.CTkLabel(c2, text="基准尺寸尚未设置：在下方窗口列表点「设为基准」即可。",
                         font=T.fonts["small"], text_color=T.WARN, justify="left").pack(
                anchor="w", padx=16, pady=(2, 0))

        # 窗口列表（信息 + 快捷把某个窗口尺寸设为基准）
        ctk.CTkLabel(c2, text="检测到的窗口（点「设为基准」用该窗口的当前尺寸作为基准）：",
                     font=T.fonts["small"], text_color=T.TEXT_DIM, justify="left").pack(
            anchor="w", padx=16, pady=(8, 2))
        # 窗口枚举（getAllWindows）很慢、绝不能卡主线程：先放占位、后台线程枚举完再回主线程填。
        self.rows_holder = ctk.CTkFrame(c2, fg_color="transparent")
        self.rows_holder.pack(fill="x")
        ctk.CTkLabel(self.rows_holder, text="正在检测窗口…", font=T.fonts["body"],
                     text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
        self._kick_enum_windows()

    def layout_wins(self):
        self._normalize_now(True)

    def _normalize_now(self, layout=False):
        """点「还原尺寸」时触发：把所有尺寸≠基准的游戏窗口拉回基准尺寸。
        基准未设置时提示；没有窗口时提示。只对尺寸不符的窗口动手（已是基准的不碰）。"""
        cfg = data.cfg
        base = (cfg.get("targets") or {}).get("base_size")
        if not base or len(base) < 2:
            self.main_view.toast("请先设置基准尺寸（填上面两个框或在窗口列表点「设为基准」）")
            return
        bw, bh = int(base[0]), int(base[1])
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        start_x = (cfg.get("targets") or {}).get("start_x")
        try:
            wins = win_mod.locate_all(title, offset)
        except Exception:
            wins = []
        if not wins:
            self.main_view.toast(f"没检测到游戏窗口（标题含「{title}」），请先打开游戏")
            return
        todo = []
        pos = []
        sw, sh = util.get_screen_size()
        x0 = bw - 15
        start_x = int((sw - 2 * x0) / 2)
        for i, w in enumerate(wins):
            r = w.rect()
            if r and ((abs(r[2] - bw) > 4 or abs(r[3] - bh) > 4) or layout):
                todo.append(w)
                x = (start_x + i % 2 * x0) if i < 4 else int((sw - bw) / 2)
                y = (sh - bh - 36 if sh - bh * 2 < 0 else bh) * int(i / 2) if i < 4 else int((sh - bh) / 2)
                pos.append((x, y))
        if not todo:
            self.main_view.toast(f"所有窗口已是基准尺寸 {bw}×{bh}，无需还原")
            self.refresh()
            return
        ok = 0
        for i, w in enumerate(todo):
            w.activate()
            if w.resize_to(bw, bh, pos[i] if layout else None):
                ok += 1
        data._game_connected = None  # 尺寸变了，强制下次 tick 刷新药丸
        if ok < len(todo):
            self.main_view.toast(f"已还原 {ok}/{len(todo)} 个号到 {bw}×{bh}；部分窗口可能锁了分辨率档位")
        else:
            self.main_view.toast(f"已把 {ok} 个号还原到基准尺寸 {bw}×{bh}")
        self.refresh()

    def _kick_enum_windows(self):
        """后台枚举窗口，完成后回主线程把列表填进 holder。用 token 丢弃过期结果（连续切页/刷新时）。"""
        cfg = data.cfg
        title = cfg.get("window_title", "梦幻西游")
        offset = cfg.get("window_offset", [0, 0])
        token = object()
        self._enum_token = token

        def work():
            try:
                wins = win_mod.locate_all(title, offset)
                d = [(w, w.rect()) for w in wins]  # rect() 趁后台一并取好
            except Exception:
                d = []
            try:
                self.main_view.after(0, lambda: self._fill_win_rows(d, token))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        self._kick_enum_windows()

    def _fill_win_rows(self, data, token):
        """在主线程把枚举结果渲染进 holder。过期结果/控件已销毁则丢弃。"""
        if token is not getattr(self, "_enum_token", None):
            return
        try:
            if not self.rows_holder.winfo_exists():
                return
            for w in self.rows_holder.winfo_children():
                w.destroy()
        except Exception:
            return
        if not data:
            ctk.CTkLabel(self.rows_holder, text="没检测到游戏窗口，请先打开游戏再点「刷新」。",
                         font=T.fonts["body"], text_color=T.TEXT_DIM).pack(anchor="w", padx=16, pady=(2, 14))
            return
        for i, (w, r) in enumerate(data):
            row = ctk.CTkFrame(self.rows_holder, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM)
            row.pack(fill="x", padx=12, pady=4)
            row.grid_columnconfigure(0, weight=1)
            meta = f"号{i + 1}    {r[2]}×{r[3]}    @({r[0]},{r[1]})" if r else f"号{i + 1}    （窗口已失效）"
            is_base = bool(self.base_size and r and int(self.base_size[0]) == r[2] and int(self.base_size[1]) == r[3])
            ctk.CTkLabel(row, text=meta + ("   ✓ 当前基准" if is_base else ""),
                         font=T.fonts["body"],
                         text_color=T.SUCCESS if is_base else T.TEXT).grid(
                row=0, column=0, sticky="w", padx=12, pady=8)
            ctk.CTkButton(row, text="设为基准", font=T.fonts["small"], width=84, height=30,
                          corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.BORDER, text_color=T.TEXT,
                          border_width=1, border_color=T.BORDER,
                          command=lambda w=w: self._set_base_from(w)).grid(row=0, column=1, padx=10)
        ctk.CTkFrame(self.rows_holder, fg_color="transparent", height=6).pack()

    def _set_base_from(self, w):
        r = w.rect()
        if not r:
            self.main_view.toast("该窗口已失效，请点「刷新」")
            return
        cfg = data.cfg
        cfg.setdefault("targets", {})["base_size"] = [r[2], r[3]]
        cfg_mod.save_config(cfg)
        self.main_view.toast(f"已设基准尺寸 {r[2]}×{r[3]}（点「还原尺寸」时其它号会归一化到这个大小）")
        self.refresh()
