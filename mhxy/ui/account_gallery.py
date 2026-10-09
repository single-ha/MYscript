# -*- coding: utf-8 -*-
"""
账号库弹窗（登录游戏用）：标定账号卡片/头像图 + 挑最多 5 个进登录队列。一个窗口干两件事。

为什么是弹窗：登录区（日常页顶部）只留一个「账号库」小按钮点开本窗口，标定与挑选的复杂度
全收在这里，主界面不拥挤。数据逻辑全在 core/account_history.py（纯函数），本文件只是薄壳+画面。
用法与队长ID 库（ui/leader_gallery.py）一致：框选期间本窗隐身（保证截图里只有游戏画面），
所有操作改写同一份 app.cfg + 同一批物理文件，变更后回调 on_done 广播刷新登录区。

规矩（与 core/account_history 同源，别在这里另写一套）：
  · 库最多 10 个（MAX_SLOTS），标到第 11 个明确拒绝（不偷偷挤掉旧的）。
  · 登录队列最多 5 个（MAX_PICK），按**点选的先后**决定登录先后；满了再点会提示先移掉一个。
  · 框选时账号在客户端「选择账号」列表里对着那张卡片/头像框（要独特、别框到会变的部分）。
"""

import customtkinter as ctk
from tkinter import messagebox

from . import theme as T
from .common import OrderThumbs, Tooltip
from ..core import account_history as ah
from ..core import config as cfg_mod


class AccountGallery(ctk.CTkToplevel):
    @classmethod
    def open(cls, app, on_done=None):
        """打开（去重：已开则前置，避免两个库窗同时写 config 打架）。"""
        existing = getattr(app, "_account_gallery", None)
        if existing is not None:
            try:
                if existing.winfo_exists():
                    existing.lift()
                    existing.focus_force()
                    return existing
            except Exception:
                pass
        g = cls(app, on_done=on_done)
        app._account_gallery = g
        return g

    def __init__(self, app, on_done=None):
        super().__init__(app)
        self.app = app
        self.fonts = app.fonts
        self.on_done = on_done
        self._thumbs = []                 # 防缩略图被 GC（每次 _render 先清空再重建）
        self._name_vars = {}              # slot -> 名字输入框的 StringVar（改名失焦即存）
        self._suspend = False             # 改名重建卡片时别把「失焦即存」打成递归

        # 以磁盘为准 reload：账号库刚在别的窗里改过，别被旧内存对象覆盖回去
        self.app.cfg = cfg_mod.load_config()

        self.title("账号库")
        self.geometry("620x600")
        self.minsize(560, 520)
        self.configure(fg_color=T.BG)
        self.transient(app)

        self._build()
        self._render()
        self.after(120, self._center)
        self.protocol("WM_DELETE_WINDOW", self._close)

    # ------------------------------------------------------------------
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(top, text="账号库", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        sub = ctk.CTkLabel(top, text="每号两张图：账号卡片 + 角色卡片（登录流程：切换账号→点账号卡→进入游戏→"
                                     "更换角色→选「已有角色」里的角色卡）。框选存进库里（最多 %d 个）；"
                                     "点「加入登录」挑最多 %d 个去登录，按点选先后依次登录。"
                                     % (ah.MAX_SLOTS, ah.MAX_PICK),
                           font=self.fonts["small"], text_color=T.TEXT_DIM, justify="left")
        sub.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        T.bind_wraplength(sub)

        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 4))
        bar.grid_columnconfigure(0, weight=1)
        self.btn_add = ctk.CTkButton(bar, text="＋ 标定新账号", font=self.fonts["btn"], height=38,
                                     corner_radius=T.RADIUS_SM, fg_color=T.ACCENT,
                                     hover_color=T.ACCENT_HOVER, text_color=T.ON_ACCENT,
                                     command=self._add)
        self.btn_add.pack(side="left")
        ctk.CTkButton(bar, text="清空登录队列", font=self.fonts["body"], height=38, width=132,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER,
                      text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=self._clear).pack(side="right")

        self.grid_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.grid_frame.grid(row=2, column=0, sticky="nsew", padx=12, pady=(4, 4))
        for c in range(3):
            self.grid_frame.grid_columnconfigure(c, weight=1, uniform="col")
        T.tune_scroll_speed(self.grid_frame)

        bottom = ctk.CTkFrame(self, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", padx=18, pady=(4, 14))
        bottom.grid_columnconfigure(0, weight=1)
        # 登录顺序用**缩略图**排（图比名字更认得出是哪张卡），账号1的图 → 账号2的图 …
        self.order = OrderThumbs(bottom, self.fonts, empty_text="还没挑要登录的号", thumb_h=40)
        self.order.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.status_lbl = ctk.CTkLabel(bottom, text="", font=self.fonts["small"], text_color=T.TEXT_DIM)
        # sticky="ew" 是铁律：bind_wraplength 靠 label 自身槽宽算 wraplength，sticky="w" 会让标签
        # 只占「换行后自己要的宽度」= 越换越窄，最终每个字一行（竖排）。同 CLAUDE.md 约束 8。
        self.status_lbl.grid(row=1, column=0, sticky="ew")
        T.bind_wraplength(self.status_lbl)
        ctk.CTkButton(bottom, text="完成", font=self.fonts["btn"], width=100, height=36,
                      corner_radius=T.RADIUS_SM, fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                      text_color=T.ON_ACCENT, command=self._close).grid(row=1, column=1, sticky="e")

    # ------------------------------------------------------------------
    def _render(self):
        from .common import load_thumb   # 延迟导入避免循环依赖
        for w in self.grid_frame.winfo_children():
            w.destroy()
        self._thumbs.clear()
        self._name_vars.clear()

        self.app.cfg = cfg_mod.load_config()
        lib = ah.get_library(self.app.cfg)
        queue = ah.get_selection(self.app.cfg)
        by_slot = {it["slot"]: it for it in lib}

        if ah.is_full(self.app.cfg):
            self.btn_add.configure(state="disabled", text="账号库已满（%d 个）" % ah.MAX_SLOTS)
        else:
            self.btn_add.configure(state="normal", text="＋ 标定新账号")

        # 已标定的账号排在前面，其余留空槽（空槽提示"标一个"）
        order = [it["slot"] for it in lib] + [s for s in range(ah.MAX_SLOTS) if s not in by_slot]
        for i, slot in enumerate(order):
            r, col = divmod(i, 3)
            if slot in by_slot:
                self._card(r, col, by_slot[slot], queue.index(slot) + 1 if slot in queue else 0,
                           load_thumb)
            else:
                self._empty_slot(r, col)
        # 底部缩略图条：登录顺序（点选先后）用图排，文字状态只报数量
        self.order.set_items([(queue.index(s) + 1, by_slot[s]["name"], ah.slot_rel(s))
                              for s in queue if s in by_slot])
        char_done = sum(1 for s in queue if ah.char_slot_exists(s))
        status = "账号库 %d/%d 个　已选 %d/%d 个（下图即登录顺序）" % (
            len(lib), ah.MAX_SLOTS, len(queue), ah.MAX_PICK)
        if queue and char_done < len(queue):
            status += "　⚠ 角色卡缺 %d 个" % (len(queue) - char_done)
        self._toast(status, (T.WARN if queue and char_done < len(queue)
                             else T.ACCENT if queue else T.TEXT_DIM))

    def _card(self, r, col, item, pos, load_thumb):
        slot = item["slot"]
        card = ctk.CTkFrame(self.grid_frame, fg_color=T.SURFACE_2, corner_radius=T.RADIUS_SM,
                            border_width=2, border_color=(T.SUCCESS if pos else T.BORDER))
        card.grid(row=r, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_columnconfigure(0, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        head.grid_columnconfigure(0, weight=1)
        if pos:
            ctk.CTkLabel(head, text="● 第 %d 个登录" % pos, font=self.fonts["small"],
                         text_color=T.SUCCESS).grid(row=0, column=0, sticky="w")
        else:
            ctk.CTkLabel(head, text="未加入登录", font=self.fonts["small"],
                         text_color=T.TEXT_DIM).grid(row=0, column=0, sticky="w")

        thumb = load_thumb(ah.slot_rel(slot), self._thumbs, max_h=54)
        if thumb is not None:
            ctk.CTkLabel(card, text="", image=thumb).grid(row=1, column=0, padx=10, pady=8)
        else:
            miss = ctk.CTkLabel(card, text="（图片丢失，请重标）", font=self.fonts["small"],
                                text_color=T.WARN, justify="left")
            miss.grid(row=1, column=0, sticky="ew", padx=10, pady=14)
            T.bind_wraplength(miss)

        var = ctk.StringVar(value=item["name"])
        self._name_vars[slot] = var
        ent = ctk.CTkEntry(card, textvariable=var, height=28, font=self.fonts["small"],
                           fg_color=T.SURFACE, border_color=T.BORDER, justify="left")
        ent.grid(row=2, column=0, sticky="ew", padx=10)
        ent.bind("<FocusOut>", lambda e, s=slot: self._rename(s))
        ent.bind("<Return>", lambda e, s=slot: self._rename(s))
        Tooltip(ent, "账号名（只用于日志显示；登录时按卡片图识别，与名字无关）", self.fonts)

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=3, column=0, sticky="ew", padx=8, pady=(6, 2))
        btns.grid_columnconfigure(0, weight=1)
        btns.grid_columnconfigure(1, weight=1)
        ctk.CTkButton(btns, text=("移出登录" if pos else "加入登录"), font=self.fonts["small"], height=28,
                      corner_radius=T.RADIUS_SM,
                      fg_color=T.SUCCESS if pos else T.BTN,
                      hover_color=T.SUCCESS_HOVER if pos else T.BTN_HOVER,
                      text_color=T.TEXT if pos else T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=lambda s=slot: self._toggle(s)).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        ctk.CTkButton(btns, text="重标", font=self.fonts["small"], height=28,
                      corner_radius=T.RADIUS_SM, fg_color="transparent",
                      hover_color=T.BTN_HOVER, text_color=T.TEXT, border_width=1, border_color=T.BORDER,
                      command=lambda s=slot: self._recal(s)).grid(row=0, column=1, sticky="ew", padx=(4, 0))

        # 角色卡片（登录流程「选择角色」一步用；与账号卡片各标各的）
        ctk.CTkFrame(card, fg_color=T.BORDER, height=1).grid(row=4, column=0, sticky="ew",
                                                              padx=10, pady=(2, 2))
        charf = ctk.CTkFrame(card, fg_color="transparent")
        charf.grid(row=5, column=0, sticky="ew", padx=10, pady=(2, 4))
        charf.grid_columnconfigure(0, weight=1)
        ch = load_thumb(ah.char_slot_rel(slot), self._thumbs, max_h=34)
        cbtn = ctk.CTkButton(charf, corner_radius=T.RADIUS_SM,
                             fg_color=T.BTN, hover_color=T.BTN_HOVER,
                             text_color=T.TEXT,
                             border_width=1, border_color=T.BORDER, width=96, height=38,
                             command=lambda s=slot: self._char(s))
        if ch is not None:
            cbtn.configure(text="", image=ch, width=ch.cget("size")[0] + 14,
                           height=ch.cget("size")[1] + 6)
            Tooltip(cbtn, "该号已标角色卡；点击可重标", self.fonts)
        else:
            # 未标时用实底强调色，确保从外观上就是「可点击的按钮」而不是一段提示文字
            cbtn.configure(text="角色卡未标", fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER,
                           text_color=T.ON_ACCENT)
            Tooltip(cbtn, "还没标角色卡：点击框选该号在「更换角色→已有角色」里的角色卡"
                          "（登录流程最后一步用）", self.fonts)
        cbtn.grid(row=0, column=0, sticky="w")

        del_row = ctk.CTkFrame(card, fg_color="transparent")
        del_row.grid(row=6, column=0, sticky="ew", padx=8, pady=(0, 8))
        ctk.CTkButton(del_row, text="删除该账号", font=self.fonts["small"], height=24,
                      corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.DANGER,
                      text_color=T.TEXT_DIM, border_width=1, border_color=T.BORDER,
                      command=lambda s=slot: self._delete(s)).pack(fill="x")

    def _empty_slot(self, r, col):
        ph = ctk.CTkFrame(self.grid_frame, fg_color="transparent", corner_radius=T.RADIUS_SM,
                          border_width=1, border_color=T.BORDER)
        ph.grid(row=r, column=col, sticky="nsew", padx=6, pady=6)
        ctk.CTkLabel(ph, text="（空槽）", font=self.fonts["small"], text_color=T.TEXT_DIM).pack(
            expand=True, padx=10, pady=34)

    # ------------------------------------------------------------------
    # 标定（框选期间整窗隐身，只留游戏画面）
    # ------------------------------------------------------------------
    def _pick(self, prompt, stage_rel):
        """框选一张图：写进 stage_rel 暂存路径（账号卡片 ah.PICK_REL / 角色卡片 ah.CHAR_PICK_REL，
        不写 config 的 templates——账号/角色图由 core/account_history 自管物理槽）。

        框选期间本窗与主窗口一起设**透明度 0**（与标定对话框同款机制，alpha_windows=(app, self)
        全量隐身），保证冻结截图里只剩游戏画面；不 withdraw——CTk 弹窗 withdraw 在某些环境下
        不彻底、窗口仍会被收进截图里（已踩）。

        require_window=False：账号/角色图只是**像素模板**、不存相对坐标，所以不该要求「先找到游戏
        窗口」——否则窗口没在前台/标题对不上时只会界面闪一下、框选层不弹（已踩）。"""
        from .calibrate_dialog import calibrate_template_direct
        self._pick_error = None
        ok = False
        try:
            ok = calibrate_template_direct(self.app, ah.TASK, "login_pick",
                                           "卡片/角色图", toast=self._toast,
                                           out_rel=stage_rel, prompt=prompt,
                                           require_window=False,
                                           alpha_windows=(self.app, self))
        except Exception as e:
            # 绝不静默：回调里抛异常在 GUI 里没人看得见，用户只会觉得「点了没反应」
            ok = False
            self._pick_error = f"框选失败：{e}"
        if not ok:
            self._toast(getattr(self, "_pick_error", None) or "已取消（框选层里按 Esc 或松开即可）",
                        T.WARN)
        return ok

    def _add(self):
        if ah.is_full(self.app.cfg):
            self._toast("账号库已满（最多 %d 个），先删一个再标定。" % ah.MAX_SLOTS, T.WARN)
            return
        if not self._pick("请框选要加入账号库的**账号卡片**（「选择账号」列表里那张卡/头像，"
                          "要独特，别框会变的部分）", ah.PICK_REL):
            return
        self.app.cfg = cfg_mod.load_config()      # calibrate 自己写过盘，必须 reload 再收编
        ok, msg = ah.add_account(self.app.cfg)
        self._after_change(msg, T.SUCCESS if ok else T.WARN)

    def _recal(self, slot):
        name = ah.get_names(self.app.cfg).get(slot, "")
        if not self._pick("请框选「%s」的新**账号卡片**（保持与库里同一个账号）" % name, ah.PICK_REL):
            return
        self.app.cfg = cfg_mod.load_config()
        ok, msg = ah.recalibrate(self.app.cfg, slot)
        self._after_change(msg, T.SUCCESS if ok else T.WARN)

    def _char(self, slot):
        """标/重标某个账号的**角色卡片**（登录流程最后一步「选择角色」要用）。"""
        name = ah.get_names(self.app.cfg).get(slot, "该账号")
        if not self._pick("请框选「%s」的**角色卡片**：点「进入游戏」后点「更换角色」、"
                          "点「已有角色」，列表里那张角色卡" % name, ah.CHAR_PICK_REL):
            return
        self.app.cfg = cfg_mod.load_config()
        ok, msg = ah.save_char(self.app.cfg, slot)
        self._after_change(msg, T.SUCCESS if ok else T.WARN)

    # ---- 选择 / 改名 / 删除 ----
    def _toggle(self, slot):
        ok, msg, _queue = ah.toggle_selected(self.app.cfg, slot)
        self._after_change(msg, T.SUCCESS if ok else T.WARN)

    def _rename(self, slot):
        if self._suspend:
            return
        var = self._name_vars.get(slot)
        if var is None:
            return
        if ah.get_names(self.app.cfg).get(slot) == var.get().strip():
            return
        ah.rename(self.app.cfg, slot, var.get())
        self._suspend = True
        try:
            self.app.cfg = cfg_mod.load_config()
        finally:
            self._suspend = False
        self._after_change("已改名。", T.TEXT_DIM)

    def _delete(self, slot):
        name = ah.get_names(self.app.cfg).get(slot, "该账号")
        try:
            if not messagebox.askyesno("删除账号", f"删除账号「{name}」？\n"
                                                "它会同时从登录队列里移出（图也删掉，不可恢复）。",
                                       parent=self):
                return
        except Exception:
            pass
        ok, msg = ah.delete_account(self.app.cfg, slot)
        self._after_change(msg, T.SUCCESS if ok else T.WARN)

    def _clear(self):
        ah.clear_selection(self.app.cfg)
        self._after_change("已清空登录队列（账号库里的图都还在）。", T.SUCCESS)

    def _after_change(self, msg, color):
        self._render()
        self._toast(msg, color)
        if callable(self.on_done):
            try:
                self.on_done()
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _toast(self, msg, color=T.TEXT_DIM):
        try:
            self.status_lbl.configure(text=msg, text_color=color)
        except Exception:
            pass

    def _center(self):
        try:
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _close(self):
        try:
            if getattr(self.app, "_account_gallery", None) is self:
                self.app._account_gallery = None
        except Exception:
            pass
        self.destroy()
