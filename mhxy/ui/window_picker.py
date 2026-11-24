# -*- coding: utf-8 -*-
"""
选择窗口对话框（基础特性）。

让用户从「桌面上检测到的同名游戏窗口」里选择脚本要操作的目标：
  · 勾选几个窗口就操作几个窗口（选中 1 个 → 单号行为；选中 ≥ 2 个 → 多开号行为）

每个窗口渲成一张卡片，带【实时缩略图】帮你认出哪个号（号按屏幕位置左→右编号，
和 window.locate_all 的排序一致）。选择结果写入 cfg["targets"]：
  multi_indices / multi（按选中数量派生：len>1 时 True）/ single_index

选号纯按【屏幕位置序号】，不记 HWND：窗口移动/重排后按当前摆放的 号1/号2/号3… 认号，
运行顺序与界面显示完全一致。
"""
import time

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import window as win_mod

try:
    from PIL import Image
except Exception:  # PIL 缺失时降级为无缩略图
    Image = None


class WindowPickerDialog(ctk.CTkToplevel):
    THUMB_H = 84   # 缩略图高度（像素），宽按窗口宽高比缩放

    def __init__(self, app, on_done=None, captain_ns=None):
        """captain_ns: 不为 None 时，每张窗口卡都常驻一个「队长」单选（始终显示，user 2026-11-24）——
        直接在带缩略图的卡片上指定哪个号当队长（解决下拉框「号123」看不出是哪个窗口的问题）；被选为
        队长的那张卡再出现「选择队长ID」按钮。确定时把队长在所选窗口里的序号写入
        cfg.tasks.<captain_ns>.captain_index（如组队走 captain_ns="teaming"）。"""
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self.captain_ns = captain_ns

        self.cfg = cfg_mod.load_config()
        self.targets = dict(self.cfg.get("targets", {}))
        self.title_substr = self.cfg.get("window_title", "梦幻西游")
        self.offset = self.cfg.get("window_offset", [0, 0])

        self._wins = []          # [(GameWindow, rect, ctk_thumb_or_None)]
        self._thumbs = []        # 防 GC
        self._multi_vars = {}    # index -> BooleanVar
        self._all_var = ctk.BooleanVar(value=False)   # 「全选」toggle 的状态
        self._all_switch = None   # 「全选」toggle 控件（构建后赋值）
        self._captain_var = ctk.IntVar(value=0)   # 队长的【绝对窗口序号】(0起)；仅 captain_ns 时用
        self._captain_inited = False
        # 队长ID 按钮（仅 captain_ns）：每卡一个，只在「被选为队长」的那张卡显示（与勾选数量无关）。
        # 已标定队长ID 时按钮显示 tm_leader_id.png 缩略图，否则显示「选择队长ID」文字，点开队长ID 库。
        self._cap_radios = {}          # 绝对窗口序号 -> 「队长」单选控件
        self._captain_buttons = {}     # 绝对窗口序号 -> 队长ID 按钮
        self._leader_thumbs = []       # 队长ID 缩略图防 GC
        self._leader_poll_id = None    # 轮询队长ID 库是否关闭的 after 句柄

        self.title("选择窗口")
        self.geometry("560x600")
        self.minsize(480, 420)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self._build()
        self._enumerate()
        self.after(120, self._center)

    # ------------------------------------------------------------------
    def _center(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        # 头部
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        ctk.CTkLabel(top, text="选择窗口", font=self.fonts["title"], text_color=T.TEXT).pack(anchor="w")
        base_hint = ("勾选要操作的窗口（可多选），选中几个就操作几个。号按屏幕从左到右编号。"
                     "检测区可在「标定」里留空＝整窗，无需框大区域。")
        if self.captain_ns:
            base_hint += ("  每张卡都有「队长」单选，选中要当队长的号后，该卡会出现「选择队长ID」按钮："
                          "已标定则显示队长ID缩略图，点击可打开队长ID库管理。")
        sub = ctk.CTkLabel(top, text=base_hint,
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.pack(fill="x", pady=(4, 0))
        T.bind_wraplength(sub)

        # 顶部栏：全选 toggle + 刷新
        topbar = ctk.CTkFrame(self, fg_color="transparent")
        topbar.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 6))
        self._all_switch = ctk.CTkCheckBox(topbar, text="全选", font=self.fonts["body"], text_color=T.TEXT,
                                           fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                                           onvalue=True, offvalue=False,
                                           variable=self._all_var, command=self._on_all_toggle)
        self._all_switch.pack(side="left")
        ctk.CTkButton(topbar, text="刷新", font=self.fonts["body"], width=72, height=30,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._enumerate).pack(side="right")

        # 窗口卡片列表
        self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="nsew", padx=16, pady=(2, 4))
        self.body.grid_columnconfigure(0, weight=1)
        T.tune_scroll_speed(self.body)

        # 底部
        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=20, pady=(4, 16))
        bottom.grid_columnconfigure(0, weight=1)
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        self.status_lbl.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(bottom, text="取消", font=self.fonts["body"], width=84, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self._cancel).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(bottom, text="确定", font=self.fonts["btn"], width=110, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                      command=self._confirm).grid(row=0, column=2)

    def _set_alpha(self, a):
        for w in (self.app, self):
            try:
                w.attributes("-alpha", a)
            except Exception:
                pass

    def _enumerate(self):
        """枚举同名窗口并各截一张缩略图（截图时临时把本助手透明化，避免拍进自己）。"""
        wins = win_mod.locate_all(self.title_substr, self.offset)
        self._wins = []
        self._thumbs = []
        if not wins:
            self._render()
            return

        # 透明化批量截缩略图（alpha=0 不被 mss 拍到，但不整窗重建，省闪烁）
        self._set_alpha(0.0)
        try:
            self.update_idletasks()
            time.sleep(0.12)
            for w in wins:
                rect = w.rect()
                thumb = self._make_thumb(rect)
                self._wins.append((w, rect, thumb))
        finally:
            self._set_alpha(1.0)
            self.lift()
            self.focus_force()
        self._render()

    def _make_thumb(self, rect):
        if rect is None or Image is None:
            return None
        try:
            bgr = win_mod.grab(rect)
            if bgr is None:
                return None
            rgb = bgr[:, :, ::-1]          # BGR -> RGB
            img = Image.fromarray(rgb)
            w, h = img.size
            scale = self.THUMB_H / max(1, h)
            size = (max(1, int(w * scale)), self.THUMB_H)
            cimg = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._thumbs.append(cimg)
            return cimg
        except Exception:
            return None

    def _render(self):
        for c in self.body.winfo_children():
            c.destroy()
        self._multi_vars = {}
        # 队长控件随卡片整体重建，旧引用一并清空（点「刷新」重枚举也走这里）
        self._cap_radios = {}
        self._captain_buttons = {}
        self._leader_thumbs.clear()

        if not self._wins:
            empty = ctk.CTkLabel(self.body, text=f"没检测到标题含「{self.title_substr}」的窗口。\n"
                                         "请先打开游戏（可多开），再点上方「刷新」。",
                                 font=self.fonts["body"], text_color=T.TEXT_DIM, justify="left")
            empty.grid(row=0, column=0, sticky="ew", padx=12, pady=24)
            T.bind_wraplength(empty)
            self._all_var.set(False)
            if self._all_switch is not None:
                self._all_switch.configure(state="disabled")
            self.status_lbl.configure(text="未检测到窗口")
            return

        # 初始选择集：优先 multi_indices；否则若历史是单开且 single_index 合法，只勾选它；否则全选
        sel_multi = set(self.targets.get("multi_indices") or [])
        targets_multi_flag = bool(self.targets.get("multi", False))
        single_idx = int(self.targets.get("single_index", 0) or 0)
        init_all = False
        if sel_multi:
            init_set = set(sel_multi)
        elif (not targets_multi_flag) and 0 <= single_idx < len(self._wins):
            init_set = {single_idx}
        else:
            init_all = True
            init_set = set(range(len(self._wins)))

        # 队长初值：把存的 captain_index（所选窗口里的序号）映射回【绝对窗口序号】
        if self.captain_ns and not self._captain_inited:
            team_tc = cfg_mod.task_config(self.cfg, self.captain_ns)
            ci = int(team_tc.get("captain_index", 0) or 0)
            sel_cand = sorted(init_set) if init_set else list(range(len(self._wins)))
            cap_abs = sel_cand[ci] if 0 <= ci < len(sel_cand) else (sel_cand[0] if sel_cand else 0)
            self._captain_var.set(cap_abs)
            self._captain_inited = True

        for i, (w, rect, thumb) in enumerate(self._wins):
            card = ctk.CTkFrame(self.body, fg_color=T.SURFACE, corner_radius=T.RADIUS_SM,
                                border_width=1, border_color=T.BORDER)
            card.grid(row=i, column=0, sticky="ew", pady=5, padx=4)
            card.grid_columnconfigure(2, weight=1)

            # 复选框（始终）
            var = self._multi_vars.get(i)
            if var is None:
                if init_all:
                    default_on = True
                else:
                    default_on = (i in init_set)
                var = ctk.BooleanVar(value=default_on)
                self._multi_vars[i] = var
            ctk.CTkCheckBox(card, text="", width=24, variable=var,
                            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                            command=self._sync_all_toggle).grid(
                                row=0, column=0, padx=(12, 6), pady=10)

            # 缩略图
            if thumb is not None:
                ctk.CTkLabel(card, text="", image=thumb).grid(row=0, column=1, padx=(2, 10), pady=8)
            else:
                ctk.CTkLabel(card, text="", font=self.fonts["title"]).grid(
                    row=0, column=1, padx=(2, 10), pady=8)

            # 信息
            info = ctk.CTkFrame(card, fg_color="transparent")
            info.grid(row=0, column=2, sticky="ew")
            ctk.CTkLabel(info, text=f"号{i + 1}", font=self.fonts["body_b"],
                         text_color=T.TEXT).pack(anchor="w")
            if rect:
                meta = f"{rect[2]}×{rect[3]}  @ ({rect[0]},{rect[1]})"
            else:
                meta = "（窗口已失效）"
            ctk.CTkLabel(info, text=meta, font=self.fonts["small"],
                         text_color=T.TEXT_DIM).pack(anchor="w")
            ttl = (w.title or "").strip()
            if ttl:
                ttl_lbl = ctk.CTkLabel(info, text=ttl, font=self.fonts["small"], text_color=T.TEXT_DIM,
                                       justify="left")
                ttl_lbl.pack(fill="x")
                T.bind_wraplength(ttl_lbl)

            # 队长区（仅 captain_ns）：「队长」单选【始终显示】（每张卡都有，user 2026-11-24）；
            # 「选择队长ID」按钮只在【被选为队长】的那张卡上显示。两者都 pack 在 cap_box 里，
            # 故按钮显隐用 pack_forget/pack（不能对 pack 管理的控件用 grid_remove）。
            if self.captain_ns:
                cap_box = ctk.CTkFrame(card, fg_color="transparent")
                cap_box.grid(row=0, column=3, padx=(6, 12))
                radio = ctk.CTkRadioButton(cap_box, text="队长", width=24, variable=self._captain_var,
                                           value=i, font=self.fonts["small"], fg_color=T.ACCENT,
                                           hover_color=T.ACCENT_HOVER,
                                           command=self._update_captain_visibility)
                radio.pack(anchor="e")
                self._cap_radios[i] = radio
                leader_btn = ctk.CTkButton(
                    cap_box, text="选择队长ID", font=self.fonts["small"], height=30, width=108,
                    corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                    text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                    command=self._open_leader_gallery)
                # 初始不 pack；由 _update_captain_visibility 决定只在队长卡上 pack 出来
                self._captain_buttons[i] = leader_btn

        # 走到这必有窗口 → 启用「全选」toggle，并按当前勾选状态同步它
        if self._all_switch is not None:
            self._all_switch.configure(state="normal")
        self._sync_all_toggle()
        # 队长区显隐/缩略图按当前队长选择刷新：单选常显，队长ID 按钮只在队长卡上出现
        if self.captain_ns:
            self._update_captain_visibility()

        # 尺寸一致性提示（多开共用一套标定要求同尺寸）
        sizes = {(r[2], r[3]) for _, r, _ in self._wins if r}
        warn = "" if len(sizes) <= 1 else "  ⚠ 窗口尺寸不一致，多开共用标定可能点错，建议统一分辨率"
        self.status_lbl.configure(text=f"检测到 {len(self._wins)} 个窗口" + warn,
                                  text_color=T.WARN if warn else T.TEXT_DIM)

    # ------------------------------------------------------------------
    def _on_all_toggle(self):
        """用户点「全选」toggle：勾选→选中全部窗口；取消→只选中窗口1（号1）。"""
        if self._all_var.get():
            for v in self._multi_vars.values():
                v.set(True)
        else:
            for i in sorted(self._multi_vars):
                self._multi_vars[i].set(i == 0)
        # 勾选数反推 toggle 状态（如只有 1 个窗口时「只选号1」=全选，保持一致性）
        self._sync_all_toggle()

    def _sync_all_toggle(self):
        """按各窗口复选框的实际状态同步「全选」toggle：全部勾选→选中，否则未勾选。"""
        vals = [v.get() for v in self._multi_vars.values()]
        self._all_var.set(bool(vals) and all(vals))
        if self.captain_ns:
            self._update_captain_visibility()

    # ------------------------------------------------------------------
    # 队长区（仅 captain_ns）：单选常显 + 队长ID 按钮只在队长卡显隐/缩略图
    # ------------------------------------------------------------------
    def _selected_idxs(self):
        """当前勾选窗口的绝对序号（升序）。"""
        return sorted(i for i, v in self._multi_vars.items() if v.get())

    def _update_captain_visibility(self):
        """按当前队长选择刷新队长区（user 2026-11-24 改口径）：
        · 「队长」单选【始终显示】在每张卡上（不再按勾选数量多开/单开隐藏），随时可改选队长；
        · 「选择队长ID」按钮只出现在【被选为队长】的那张卡上（与勾选数量无关）。
        顺带把越界/未勾选的队长选择纠正到第一个所选号，并刷新队长ID按钮的缩略图。"""
        if not self.captain_ns:
            return
        idxs = self._selected_idxs()
        cap_abs = self._captain_var.get()
        # 队长必须落在所选号里；不在（取消勾选/越界）则回退到第一个所选号
        if idxs and cap_abs not in idxs:
            cap_abs = idxs[0]
            self._captain_var.set(cap_abs)
        # 单选在 _render 里已 pack 且始终显示，这里不再做显隐。
        # 队长ID 按钮：只在队长卡上 pack（pack_forget 其余），保持在单选下方。
        for i, btn in self._captain_buttons.items():
            try:
                if i == cap_abs:
                    if not btn.winfo_ismapped():
                        btn.pack(anchor="e", pady=(6, 0))
                else:
                    btn.pack_forget()
            except Exception:
                pass
        self._refresh_leader_button()

    def _refresh_leader_button(self):
        """给当前队长卡上的「选择队长ID」按钮套缩略图（已标定）或文字（未标定）。
        队长ID 是全局共享的一张激活图 tm_leader_id.png，所有窗口共用，故只需刷新显示中的那个按钮。"""
        from .common import load_thumb   # 延迟导入，避免循环依赖
        cap_abs = self._captain_var.get()
        btn = self._captain_buttons.get(cap_abs)
        if btn is None:
            return
        self._leader_thumbs.clear()
        img = load_thumb("templates/tm_leader_id.png", self._leader_thumbs, max_h=26)
        try:
            if img is not None:
                # 有图：只显示缩略图（compound 默认 text 在图旁，这里清空文字让按钮就是一张图）
                btn.configure(image=img, text="", width=58, height=34)
            else:
                btn.configure(image=None, text="选择队长ID", width=108, height=30)
        except Exception:
            pass

    def _open_leader_gallery(self):
        """打开队长ID 库弹窗（复用 LeaderIdGallery 的去重单例）。关闭后轮询刷新本窗按钮缩略图——
        库的 on_done 回调固定广播 App.refresh_leader_thumb，触达不到本弹窗，故用 after 轮询其存在性。"""
        from .leader_gallery import LeaderIdGallery
        try:
            LeaderIdGallery.open(self.app)
        except Exception as e:
            try:
                self.app.toast(f"打开队长ID库失败：{e}")
            except Exception:
                pass
            return
        self._poll_leader_gallery()

    def _poll_leader_gallery(self):
        """轮询队长ID库是否已关闭：关闭（单例被置空/窗口失效）后刷新一次缩略图并停止轮询。"""
        gal = getattr(self.app, "_leader_gallery", None)
        alive = False
        if gal is not None:
            try:
                alive = bool(gal.winfo_exists())
            except Exception:
                alive = False
        if alive:
            self._leader_poll_id = self.after(300, self._poll_leader_gallery)
        else:
            self._leader_poll_id = None
            # 库可能改了激活图，reload 配置后再刷新按钮
            try:
                self.cfg = cfg_mod.load_config()
            except Exception:
                pass
            self._refresh_leader_button()

    # ------------------------------------------------------------------
    def _confirm(self):
        captain_index = None
        idxs = sorted(i for i, v in self._multi_vars.items() if v.get())
        if not idxs:                       # 没勾任何号 → 视为「全部」
            idxs = list(range(len(self._wins)))
        # 存所选绝对序号（运行时不再被 max_windows 截断）。选号纯按屏幕位置序号，不记 HWND：
        # 移动/重排窗口后按当前摆放位置认号，与界面显示的 号1/号2… 始终对应。
        self.targets["multi_indices"] = idxs
        self.targets.pop("selected_hwnds", None)
        # 派生：按选中数量决定是否多开
        self.targets["multi"] = len(idxs) > 1
        if len(idxs) == 1:
            self.targets["single_index"] = idxs[0]
        else:
            # 保留旧的 single_index，不强行改写
            if "single_index" not in self.targets:
                self.targets["single_index"] = int(self.targets.get("single_index", 0) or 0)
        if self.captain_ns:
            cap_abs = self._captain_var.get()
            if cap_abs not in idxs:        # 队长没在所选里 → 落到第一个所选号
                cap_abs = idxs[0] if idxs else 0
            captain_index = idxs.index(cap_abs) if cap_abs in idxs else 0
        # 读盘再写，避免覆盖其它地方刚改的配置
        cfg = cfg_mod.load_config()
        cfg["targets"] = {**cfg.get("targets", {}), **self.targets}
        if captain_index is not None:
            team_tc = cfg_mod.task_config(cfg, self.captain_ns)
            team_tc["captain_index"] = captain_index
            cfg_mod.set_task_config(cfg, self.captain_ns, team_tc)
        cfg_mod.save_config(cfg)
        self.app.cfg = cfg
        self._close()

    def _cancel(self):
        self._close()

    def _close(self):
        if self._leader_poll_id is not None:
            try:
                self.after_cancel(self._leader_poll_id)
            except Exception:
                pass
            self._leader_poll_id = None
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass
        self.destroy()
