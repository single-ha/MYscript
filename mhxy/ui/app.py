# -*- coding: utf-8 -*-
"""
主界面。深色简约风，左侧导航 + 中间分页 + 右侧常驻全局日志。
每个任务对应一个页面：以后加新任务，写个 *Page 并在 App.PAGE_CLASSES 注册即可。
页面实现已拆到 gui/pages/ 子包（每页一个文件）；跨页小组件与急停常量在 gui/common.py。
"""

import datetime
import threading

import customtkinter as ctk

from . import theme as T
from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core.runner import TaskRunner
from ..core.input import get_cursor
from .common import (DEFAULT_STOP_HOTKEY, DEFAULT_FAILSAFE, FAILSAFE_CORNERS,
                     _in_failsafe_corner, _parse_stop_hotkey, _vk_down)
from .pages import (DailyPage, SinglePage, MultiPage, ToolsPage,
                    GeneralPage, SettingsPage, AboutPage)


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------
class App(ctk.CTk):
    NAV = [("daily", "🐉  日常一条龙"),
           ("general", "🧰  通用"),
           ("single", "👤  单人任务"),   # 内嵌 宝图/运镖/秘境降妖
           ("multi", "👥  多人任务"),    # 内嵌 刷副本枢纽
           ("tools", "🧰  工具"),        # 内嵌 秒装备（后续工具汇总于此）
            ("settings", "⚙  设置"), ("about", "ⓘ  关于")]
    # 可运行任务页（有 runner/pump），App 的定时器/热键/关闭钩子按此遍历。
    # general 也在内：它的「一键组队」会跑后台任务，需要 pump 抽日志、关闭时停 runner。
    # single/multi/tools 是分类页，App 下标只是顶层项；其内嵌任务页靠分类页的
    # pump() 下钻转发（见 pages/category.py）。
    RUNNABLE_KEYS = ("general", "daily", "single", "multi", "tools")

    def __init__(self):
        super().__init__()
        self.cfg = cfg_mod.load_config()
        # 全局窗口识别按进程名过滤（避免把终端/编辑器等同名标题窗口当游戏号）；GUI 各窗口操作据此生效。
        win_mod.set_game_process(self.cfg.get("window_process", "MyGame_x64r.exe"))
        mode = self.cfg.get("appearance", "dark")
        ctk.set_appearance_mode(mode if mode in ("dark", "light") else "dark")
        self.title("梦幻 · 时空 助手")
        # 主界面默认尺寸启动（1310x700），不再设最小尺寸限制，可拖到任意大小
        win_w, win_h = 1310, 700
        pos_x = max(0, (self.winfo_screenwidth() - win_w) // 2)
        pos_y = max(0, (self.winfo_screenheight() - win_h) // 2)
        self.geometry(f"{win_w}x{win_h}+{pos_x}+{pos_y}")
        self.configure(fg_color=T.BG)

        self.fonts = T.build_fonts()
        self.game_win = win_mod.GameWindow(self.cfg.get("window_title", "梦幻西游"))
        self._tick_count = 0
        self._game_connected = None   # 缓存连接状态，只在变化时刷新药丸
        self._locating = False        # 防止多个后台定位线程叠加

        self.grid_columnconfigure(1, weight=1)   # 中间内容区随窗口拉伸
        self.grid_columnconfigure(2, weight=0)   # 右侧全局日志列固定宽
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_log_panel()   # 先建日志面板：各页 _log_line 都往这写，必须先于建页
        self._build_pages()
        self._show("daily")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self._tick)
        self._hotkey_down = False
        self.after(60, self._poll_hotkey)
        # 界面显示后趁空闲把其余页面逐个预建好，首次切过去即秒开（每个间隔开，单帧不卡）。
        self.after(800, self._prebuild_idle)

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=210)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(99, weight=1)

        ctk.CTkLabel(bar, text="梦幻 · 时空", font=self.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=22, pady=(24, 0))
        ctk.CTkLabel(bar, text="辅助助手", font=self.fonts["small"], text_color=T.TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=22, pady=(0, 22))

        self.nav_buttons = {}
        for i, (key, label) in enumerate(self.NAV):
            b = ctk.CTkButton(bar, text=label, font=self.fonts["nav"], anchor="w",
                              height=42, corner_radius=T.RADIUS_SM,
                              fg_color="transparent", hover_color=T.SURFACE,
                              text_color=T.TEXT_DIM, command=lambda k=key: self._show(k))
            b.grid(row=2 + i, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[key] = b

        # 明暗切换按钮（置于风险提示之上，随侧栏底部对齐）
        self.btn_appearance = ctk.CTkButton(
            bar, text="", font=self.fonts["nav"], anchor="w", height=42,
            corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.SURFACE,
            text_color=T.TEXT_DIM, command=self._toggle_appearance)
        self.btn_appearance.grid(row=100, column=0, sticky="ew", padx=12, pady=(8, 4))
        self._render_appearance_btn()

        # 连接状态（原各任务页 header 药丸，现统一收敛到这里）：浅底 + 彩字。
        # 未检测到=白/深灰底(SURFACE)+黄字(WARN)，已连接=绿底(PILL_OK_BG)+绿字(SUCCESS)；描边同字色。
        # 做成带描边的按钮样式：点在连接状态上即打开「选择窗口」对话框（换号/选窗口一步到位）。
        self.btn_game_status = ctk.CTkButton(
            bar, text="○ 未检测到目标窗口", font=self.fonts["small"], height=34,
            corner_radius=T.RADIUS_SM, fg_color=T.SURFACE, hover_color=T.BORDER,
            border_width=1, border_color=T.WARN,
            text_color=T.WARN, command=self.open_window_picker)
        self.btn_game_status.grid(row=101, column=0, sticky="ew", padx=12, pady=(4, 2))

        ctk.CTkLabel(bar, text="⚠ 脚本有封号风险\n请用小号测试", font=self.fonts["small"],
                     text_color=T.WARN, justify="left").grid(row=102, column=0, sticky="sw",
                                                             padx=22, pady=18)

    # ---------------- 全局日志面板（常驻右侧，所有功能共用一处）----------------
    def _build_log_panel(self):
        """右侧常驻日志列：各页面/任务的日志统一汇到这里，按来源（秒装备/组队/整理背包…）打标签。
        以前每个页面各有一个日志框，功能一多就散乱；现在收敛成这一处，谁产生的日志靠行首来源标签区分。"""
        panel = ctk.CTkFrame(self, fg_color=T.SIDEBAR, corner_radius=0, width=340)
        panel.grid(row=0, column=2, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="运行日志", font=self.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="清空", font=self.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.clear_log).grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(panel, font=self.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self.log_line("界面就绪。各功能的日志都会汇总到这里。", "info")

    def log_line(self, msg, level="info", source=None):
        """统一日志出口（所有页面/任务都调它）。source 非空时在行首加暗色来源标签，如「秒装备 ›」。
        超过约 2000 行就裁掉最旧的，避免长时间运行把内存吃满。
        debug 级别日志只在全局「调试日志」开关（config.debug_log，设置页可勾）打开时才显示。"""
        if level == "debug" and not getattr(self, "cfg", {}).get("debug_log", False):
            return
        log = getattr(self, "log", None)
        if log is None:
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        log.configure(state="normal")
        try:
            tb = log._textbox
            tb.insert("end", f"[{ts}] ")
            if source:
                tb.insert("end", f"{source} › ", "src")
            tb.insert("end", f"{msg}\n", level)
            # 行数封顶：删掉最旧的若干行（int(index) 是行号，含末尾空行）
            try:
                nlines = int(tb.index("end-1c").split(".")[0])
                if nlines > 2000:
                    tb.delete("1.0", f"{nlines - 1800}.0")
            except Exception:
                pass
        except Exception:
            prefix = f"{source} › " if source else ""
            log.insert("end", f"[{ts}] {prefix}{msg}\n")
        log.see("end")
        log.configure(state="disabled")

    def clear_log(self):
        log = getattr(self, "log", None)
        if log is None:
            return
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")

    # 各顶层面（对应 NAV 每一项）对应的类。懒加载：启动只建默认页，其余等第一次切到才建——
    # 一次性建全部页面会瞬间绘制几百个 CTk 画布控件，正是启动「一块块慢慢刷出来」的根因。
    # 任务页（宝图/运镖/秘境降妖/刷副本/秒装备）已分别嵌进 single/multi/tools 三个分类页，
    # 由分类页内部懒建（见 pages/category.py），这里只登记顶层导航页。
    PAGE_CLASSES = {
        "daily": DailyPage,
        "general": GeneralPage,
        "single": SinglePage,
        "multi": MultiPage,
        "tools": ToolsPage,
        "settings": SettingsPage,
        "about": AboutPage,
    }

    def _build_pages(self):
        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        self.pages = {}   # 懒加载：key -> 页面实例，按需创建

    def _ensure_page(self, key):
        """返回页面实例，不存在则即时创建（懒加载）。返回 (page, just_created)。"""
        p = self.pages.get(key)
        if p is not None:
            return p, False
        p = self.PAGE_CLASSES[key](self.container, self)
        p.grid(row=0, column=0, sticky="nsew")
        self.pages[key] = p
        return p, True

    def refresh_leader_thumb(self):
        """广播刷新所有含行内队长ID缩略图/状态的页（刷副本页、通用页等），无论从哪个改了「队长ID 库」都同步。"""
        for p in self._iter_pages():
            for meth in ("_refresh_leader_btn", "_refresh_leader_status"):
                fn = getattr(p, meth, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass

    def _iter_pages(self):
        """产出所有顶层页 + 各分类页已建成的内嵌子页。急停/广播/查运行都要扫到子页里的 runner。"""
        for page in self.pages.values():
            yield page
            for sub in getattr(page, "sub_pages", {}).values():
                yield sub

    def build_all_pages(self, on_step=None):
        """一次性把所有页面都建好（建完再亮窗口，杜绝「出现后才逐页卡」）。
        每建好一页回调一次 on_step——用它驱动遮罩上的进度条转动，让构建期也有动画。"""
        for key in self.PAGE_CLASSES:
            if key not in self.pages:
                self._ensure_page(key)
                if callable(on_step):
                    try:
                        on_step()
                    except Exception:
                        pass
        cur = getattr(self, "_current_key", None)
        if cur in self.pages:
            self.pages[cur].tkraise()

    def _safe_update(self):
        try:
            self.update()
        except Exception:
            pass

    def reveal_with_overlay(self):
        """在本窗口上盖一层全屏「正在准备界面…」遮罩，遮罩后把其余页面全部建好，再撤遮罩。
        全程只用本窗口这一个 Tk 根（不再开第二个根），既避免双根崩溃，又盖住建页面的卡顿。

        遮罩是 App 的直接子组件、最后创建，叠在 container（含所有页面）与侧栏之上；各页面建在
        container 里，层级在遮罩之下，故新建页面不会盖穿遮罩。建页期间用 update() 让进度条转动。"""
        import tkinter as tk
        from tkinter import ttk

        bg, fg, dim, acc, trough = (T.resolve(T.BG), T.resolve(T.TEXT), T.resolve(T.TEXT_DIM),
                                    T.resolve(T.ACCENT), T.resolve(T.SURFACE_2))
        ov = tk.Frame(self, bg=bg)
        ov.place(x=0, y=0, relwidth=1, relheight=1)
        tk.Label(ov, text="梦幻 · 时空 助手", bg=bg, fg=fg,
                 font=("Microsoft YaHei UI", 16, "bold")).place(relx=0.5, rely=0.43, anchor="center")
        tk.Label(ov, text="正在准备界面…", bg=bg, fg=dim,
                 font=("Microsoft YaHei UI", 11)).place(relx=0.5, rely=0.51, anchor="center")
        try:
            style = ttk.Style(self)
            style.theme_use("default")
            style.configure("Ovl.Horizontal.TProgressbar", troughcolor=trough,
                            background=acc, bordercolor=bg, lightcolor=acc, darkcolor=acc)
            pb = ttk.Progressbar(ov, mode="indeterminate", length=240,
                                 style="Ovl.Horizontal.TProgressbar")
            pb.place(relx=0.5, rely=0.59, anchor="center")
            pb.start(12)
        except Exception:
            pass
        self._safe_update()                 # 先把遮罩画出来（盖住未完成的界面）
        self.build_all_pages(on_step=self._safe_update)
        try:
            ov.destroy()                    # 撤遮罩，露出已就绪的界面
        except Exception:
            pass
        self._safe_update()

    def _prebuild_idle(self):
        """启动后趁空闲逐个把尚未创建的页面建好；每次只建一个并重排一次调度，避免单帧卡顿。"""
        for key in self.PAGE_CLASSES:
            if key not in self.pages:
                self._ensure_page(key)
                # 新建的页默认叠在最上、会盖住当前可见页，立刻把当前页重新置顶。
                cur = getattr(self, "_current_key", None)
                if cur in self.pages:
                    self.pages[cur].tkraise()
                self.after(120, self._prebuild_idle)
                return
        # 全部建完，停止调度。

    def _show(self, key):
        self._current_key = key   # 记当前可见页，全局热键只控它
        page, _just_created = self._ensure_page(key)
        page.tkraise()
        # 总是 refresh：部分页（如通用页）刻意把内容填充放在 refresh 里、__init__ 不填，靠这里驱动。
        # 重复 refresh 的代价已被各页的「内容签名守卫」摊薄（数据没变就不重画列表）。
        if hasattr(page, "refresh"):
            page.refresh()
        for k, b in self.nav_buttons.items():
            if k == key:
                b.configure(fg_color=T.SURFACE, text_color=T.TEXT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_DIM)

    def _render_appearance_btn(self):
        """按当前外观刷新切换按钮文案：夜间显示「🌙 夜间」、白天显示「☀ 白天」。"""
        if ctk.get_appearance_mode() == "Light":
            self.btn_appearance.configure(text="☀  白天模式")
        else:
            self.btn_appearance.configure(text="🌙  夜间模式")

    def _toggle_appearance(self):
        """在夜间/白天之间切换，写回配置，并补刷不随外观自动变的部分（日志级别色）。"""
        new = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        ctk.set_appearance_mode(new)
        self._render_appearance_btn()
        # 写回配置（读盘再改，避免覆盖别处刚写入的配置）
        cfg = cfg_mod.load_config()
        cfg["appearance"] = new
        cfg_mod.save_config(cfg)
        self.cfg = cfg
        # 日志框走底层 tk tag_config，不随 set_appearance_mode 自动变，需手动重刷这一个全局面板
        log = getattr(self, "log", None)
        if log is not None:
            try:
                T.apply_log_tags(log._textbox)
            except Exception:
                pass

    def toast(self, msg):
        """简单的右下角浮层提示。"""
        lbl = ctk.CTkLabel(self, text=msg, font=self.fonts["body"], fg_color=T.ACCENT,
                           text_color=T.ON_ACCENT, corner_radius=T.RADIUS_SM, padx=16, pady=8)
        lbl.place(relx=0.99, rely=0.97, anchor="se")
        self.after(1600, lbl.destroy)

    def _tick(self):
        # 抽日志：所有可运行任务页
        for k in self.RUNNABLE_KEYS:
            p = self.pages.get(k)
            if p:
                p.pump()
        # 每约 1.2s 检测一次游戏窗口（放后台线程，避免阻塞 UI 造成滑动卡顿）
        self._tick_count += 1
        if self._tick_count % 8 == 0:
            self._kick_locate()
        self.after(150, self._tick)

    def _kick_locate(self):
        """在后台线程枚举窗口找游戏；getAllWindows 较慢，绝不能在主线程跑。"""
        if self._locating:
            return
        self._locating = True
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        targets = self.cfg.get("targets", {})

        def work():
            try:
                all_wins = win_mod.locate_all(title, offset)
                found, summary = self._compute_target_state(all_wins, targets)
            except Exception:
                found, summary = False, ""
            # 回主线程更新（after 由 Tk 在主线程执行，线程安全）
            try:
                self.after(0, lambda: self._apply_game_state(found, summary))
            except Exception:
                pass

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _compute_target_state(all_wins, targets):
        """据「已枚举的窗口 + targets 选择」算出药丸要显示的 (是否连上, 摘要串)。
        纯函数，跑在后台线程，不碰 Tk。"""
        if not all_wins:
            return False, ""
        if targets.get("multi"):
            idxs = targets.get("multi_indices") or list(range(len(all_wins)))
            sel = [i for i in idxs if 0 <= i < len(all_wins)]
            n = len(sel) if sel else len(all_wins)
            return True, f"{n} 号 · 多开"
        i = targets.get("single_index", 0)
        if not (isinstance(i, int) and 0 <= i < len(all_wins)):
            i = 0
        return True, f"号{i + 1} · 单开"

    def _apply_game_state(self, found, summary=""):
        self._locating = False
        state = (found, summary)
        if state == self._game_connected:
            return  # 状态没变就不动控件，省掉无谓重绘
        self._game_connected = state
        self._render_game_status(found, summary)

    def _render_game_status(self, found, summary=""):
        """刷新侧边栏底部的连接状态按钮：浅底 + 彩字，描边同字色。
        未检测到=底 SURFACE + 黄字 WARN；已连接=底 PILL_OK_BG + 绿字 SUCCESS。点它会打开「选择窗口」对话框。"""
        if found:
            self.btn_game_status.configure(text="● " + (summary or "目标窗口已连接"),
                                           text_color=T.SUCCESS, fg_color=T.PILL_OK_BG,
                                           hover_color=T.BORDER, border_color=T.SUCCESS)
        else:
            self.btn_game_status.configure(text="○ 未检测到目标窗口",
                                           text_color=T.WARN, fg_color=T.SURFACE,
                                           hover_color=T.BORDER, border_color=T.WARN)

    def open_window_picker(self, after=None, captain_ns=None):
        """打开「选择窗口」对话框（各任务页共用）。关闭后刷新配置并强制刷新药丸。
        captain_ns: 传入则多开模式下可在卡片上直接指定队长，写入 tasks.<captain_ns>.captain_index。"""
        from .window_picker import WindowPickerDialog

        def _done():
            self.cfg = cfg_mod.load_config()
            self._game_connected = None   # 选择可能变了，强制下次 tick 刷新药丸
            if callable(after):
                try:
                    after()
                except Exception:
                    pass

        try:
            WindowPickerDialog(self, on_done=_done, captain_ns=captain_ns)
        except Exception:
            pass

    def restore_window_size(self, after=None):
        """把选中的号窗口还原到标定时记录的基准尺寸（被手操拉大后一键复位）。"""
        targets = self.cfg.get("targets", {})
        base = targets.get("base_size")
        if not base or len(base) < 2:
            self.toast("请先标定一次，标定时会自动记录基准尺寸")
            return
        title = self.cfg.get("window_title", "梦幻西游")
        offset = self.cfg.get("window_offset", [0, 0])
        ok, total, actual = win_mod.restore_targets_size(title, offset, targets, base)
        if total == 0:
            self.toast(f"没找到/没选中目标窗口（标题含「{title}」），请先「选择窗口」")
            return
        w, h = int(base[0]), int(base[1])
        if ok == total:
            self.toast(f"已还原 {ok}/{total} 个号到 {w}×{h}")
        else:
            # 有号没还原成功——多半是游戏锁了分辨率档位，resize 被忽略
            self.toast(f"还原 {ok}/{total} 个号；部分窗口可能不支持自由缩放")
        self._game_connected = None   # 尺寸变了，强制下次 tick 刷新药丸
        if callable(after):
            try:
                after()
            except Exception:
                pass

    # ---- 全局急停轮询：鼠标甩到屏幕左上角 或 急停组合键 → 停止一切任务 ----
    def _poll_hotkey(self):
        # ① 物理急停：鼠标甩到所选屏幕角（默认右上角，设置里可改/可关）。注意——默认 sendinput 后端走
        #    SendInput 底层注入，【不经过 pyautogui，没有内置 FAILSAFE】，故必须在这里用真实光标位置自己兜
        #    （覆盖所有后端）。仅在有任务跑时生效，避免空触发；任务停了 _any_running 转 False 不再重复触发。
        corner = self.cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        if corner != "off":
            try:
                cx, cy = get_cursor()
            except Exception:
                cx, cy = None, None
            if _in_failsafe_corner(cx, cy, corner) and self._any_running():
                self._emergency_stop(f"鼠标甩到{FAILSAFE_CORNERS.get(corner, '角落')}")
        # ② 急停组合键（默认 Ctrl+Alt+F12）：所有修饰键+主键同时按下的瞬间触发一次。
        mods, key_vk = _parse_stop_hotkey(self.cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        if key_vk is not None:
            down = all(_vk_down(m) for m in mods) and _vk_down(key_vk)
            if down and not self._hotkey_down:
                self._emergency_stop()
            self._hotkey_down = down
        else:
            self._hotkey_down = False
        self.after(60, self._poll_hotkey)

    def _emergency_stop(self, reason=None):
        """急停：停掉所有页面正在跑的后台任务，弹提示。reason 标明来源（甩角/热键）。"""
        tag = reason or f"[{self.cfg.get('hotkey_stop', DEFAULT_STOP_HOTKEY)}]"
        n = self.stop_all_tasks()
        if n:
            self.toast(f"{tag} 急停：已停止 {n} 个运行中的任务")
        else:
            self.toast(f"{tag} 急停（当前没有正在跑的任务）")

    def _any_running(self):
        """是否有任意后台任务在跑（供甩角失控急停判断，避免空触发）。含分类页内嵌子页。"""
        for page in self._iter_pages():
            for v in vars(page).values():
                if isinstance(v, TaskRunner) and v.is_running():
                    return True
        return False

    def stop_all_tasks(self):
        """停掉所有页面上任意正在运行的 TaskRunner（runner / runner_ob 等都覆盖到；含分类页内嵌子页）。
        返回停了几个。顺带取消各页「未启动的待办」（如日常一条龙的定时等待——急停也要让它停得住）。"""
        n = 0
        for page in self._iter_pages():
            if hasattr(page, "stop_pending") and callable(page.stop_pending):
                try:
                    page.stop_pending()
                except Exception:
                    pass
            for v in list(vars(page).values()):
                if isinstance(v, TaskRunner) and v.is_running():
                    try:
                        v.stop()
                        n += 1
                    except Exception:
                        pass
        return n

    def _on_close(self):
        self.stop_all_tasks()
        self.destroy()


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
