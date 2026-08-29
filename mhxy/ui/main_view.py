import customtkinter as ctk
import datetime
import threading

from .general_page import GeneralPage
from . import theme as T
from . import util
from .data import data

from ..core import config as cfg_mod
from ..core import window as win_mod
from ..core.input import get_cursor
from ..core.runner import TaskRunner

DEFAULT_FAILSAFE = "top_right"
# 失控急停：任务运行时把鼠标甩到屏幕某个角(撞到角落)即停。内部值 <-> 中文显示，off=关闭。
FAILSAFE_CORNERS = {
    "top_right": "右上角", "top_left": "左上角",
    "bottom_right": "右下角", "bottom_left": "左下角", "off": "关闭",
}
DEFAULT_STOP_HOTKEY = "ctrl+alt+F12"


class MainView:
    NAV = [("general", "🧰  通用 / 工具"),
           ("daily", "🐉  日常一条龙"),
           ("sniper", "🗡  秒装备"),
           ("treasure_map", "🗺  宝图"),
           ("escort", "🚚  运镖"),
           ("secret_realm", "👹  秘境降妖"),
           ("dungeon", "🏰  刷副本"),
           ("ghost", "👻  抓鬼"),
           ("settings", "⚙  设置"), ("about", "ⓘ  关于")]
    # 左侧标签,key,实现文本,绘制类
    TAB = {
        "general": ("general", "🧰  通用 / 工具", GeneralPage),
        "person": ("person", "🍭 单人任务", None),
        "team": ("team", "🙌 多人任务", None),
        "settings": ("settings", "⚙  设置", None),
        "about": ("about", "ⓘ  关于", None)
    }

    def __init__(self, master):
        super().__init__()
        self.master = master
        self.master.configure(fg_color=T.BG)
        win_mod.set_game_process(data.cfg.get("window_process", "MyGame_x64r.exe"))
        self.game_win = win_mod.GameWindow(data.cfg.get("window_title", "梦幻西游"))

        self._tick_count = 0
        self._game_connected = None  # 缓存连接状态，只在变化时刷新药丸
        self._locating = False  # 防止多个后台定位线程叠加 
        self._current_key = None  # 记当前可见页，全局热键只控它

        self._build_sidebar()
        self._build_log_panel()  # 先建日志面板：各页 _log_line 都往这写，必须先于建页
        self._build_pages()
        self._hotkey_down = False
        self.after(60, self._poll_hotkey)
        self.after(150, self._tick)
        self._show("general")

    def _build_sidebar(self):
        bar = ctk.CTkFrame(self.master, fg_color=T.SIDEBAR, corner_radius=0, width=210)
        bar.grid(row=0, column=0, sticky="nsew")
        bar.grid_propagate(False)
        bar.grid_rowconfigure(99, weight=1)
        ctk.CTkLabel(bar, text="梦幻 · 时空", font=T.fonts["title"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w", padx=22, pady=(24, 0))
        ctk.CTkLabel(bar, text="辅助助手", font=T.fonts["small"], text_color=T.TEXT_DIM).grid(
            row=1, column=0, sticky="w", padx=22, pady=(0, 22))
        self.nav_buttons = {}
        for i, (key, label, cla) in enumerate(self.TAB.values()):
            b = ctk.CTkButton(bar, text=label, font=T.fonts["nav"], anchor="w",
                              height=42, corner_radius=T.RADIUS_SM,
                              fg_color="transparent", hover_color=T.SURFACE,
                              text_color=T.TEXT_DIM, command=lambda k=key: self._show(k))
            b.grid(row=2 + i, column=0, sticky="ew", padx=12, pady=3)
            self.nav_buttons[key] = b
        # 明暗切换按钮（置于风险提示之上，随侧栏底部对齐）
        self.btn_appearance = ctk.CTkButton(
            bar, text="", font=T.fonts["nav"], anchor="w", height=42,
            corner_radius=T.RADIUS_SM, fg_color="transparent", hover_color=T.SURFACE,
            text_color=T.TEXT_DIM, command=self._toggle_appearance)
        self.btn_appearance.grid(row=100, column=0, sticky="ew", padx=12, pady=(8, 4))
        self._render_appearance_btn()
        ctk.CTkLabel(bar, text="⚠ 脚本有封号风险\n请用小号测试", font=T.fonts["small"],
                     text_color=T.WARN, justify="left").grid(row=101, column=0, sticky="sw",
                                                             padx=22, pady=18)

    def _ensure_page(self, key):
        """返回页面实例，不存在则即时创建（懒加载）。返回 (page, just_created)。"""
        p = self.pages.get(key)
        if p is not None:
            return p, False
        if self.TAB[key][2] is None:
            return p, False
        p = self.TAB[key][2](self.container, self)
        p.grid(row=1, column=0, sticky="nsew")
        self.pages[key] = p
        # 新建的可运行页要补一次游戏连接状态（药丸初值为空，否则要等下一轮 tick 才更新）
        if self._game_connected and hasattr(p, "update_game_pill"):
            found, summary = self._game_connected
            p.update_game_pill(found, summary)
        return p, True

    def _toggle_appearance(self):
        """在夜间/白天之间切换，写回配置，并补刷不随外观自动变的部分（日志级别色）。"""
        new = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        ctk.set_appearance_mode(new)
        self._render_appearance_btn()
        # 写回配置（读盘再改，避免覆盖别处刚写入的配置）
        data.cfg["appearance"] = new
        cfg_mod.save_config(data.cfg)
        # 日志框走底层 tk tag_config，不随 set_appearance_mode 自动变，需手动重刷这一个全局面板
        log = getattr(self, "log", None)
        if log is not None:
            try:
                T.apply_log_tags(log._textbox)
            except Exception:
                pass

    def _render_appearance_btn(self):
        """按当前外观刷新切换按钮文案：夜间显示「🌙 夜间」、白天显示「☀ 白天」。"""
        if ctk.get_appearance_mode() == "Light":
            self.btn_appearance.configure(text="☀  白天模式")
        else:
            self.btn_appearance.configure(text="🌙  夜间模式")

    def _build_pages(self):
        self.container = ctk.CTkFrame(self.master, fg_color="transparent")
        self.container.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.container.grid_rowconfigure(1, weight=1)
        self.container.grid_columnconfigure(0, weight=1)
        self.build_header()
        self.pages = {}  # 懒加载：key -> 页面实例，按需创建

    def _show(self, key):
        self._current_key = key  # 记当前可见页，全局热键只控它
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
        self.render_title()

    def refresh(self):
        p = self.pages.get(self._current_key)
        if p is None:
            return
        if hasattr(p, "refresh"):
            p.refresh()

    def build_header(self):
        bar = ctk.CTkFrame(self.container, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(2, 14))
        bar.grid_columnconfigure(0, weight=1)
        self.title_lab = ctk.CTkLabel(bar, text="", font=T.fonts["title"], text_color=T.TEXT)
        self.title_lab.grid(row=0, column=0, sticky="w")
        self.sub = ctk.CTkLabel(bar, text="",
                                font=T.fonts["small"], text_color=T.TEXT_DIM, justify="left", anchor="w")
        self.sub.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.render_title()

        right = ctk.CTkFrame(bar, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.pill_game = util.pill(right)
        self.pill_game.pack(side="left", padx=(0, 8))
        self.update_game_pill()
        self.switch_mode = ctk.CTkSwitch(right, text="", font=T.fonts["body"], text_color=T.DANGER,
                                         progress_color=T.DANGER, command=self._toggle_mode)
        self.switch_mode.pack(side="left")
        self.render_mode_pill()

    def render_title(self):
        title = "标题"
        sub_title = "副标题"
        if self._current_key:
            title, sub_title = self.pages[self._current_key].get_title()
        self.title_lab.configure(text=title)
        self.sub.configure(text=sub_title)
        T.bind_wraplength(self.sub)

    def _toggle_mode(self):
        live = bool(self.switch_mode.get())
        live = not live
        data.cfg["dry_run"] = live
        cfg_mod.save_config(data.cfg)
        self.render_mode_pill()
        if not live:
            self.log_line("⚠ 已切到实战：会真开活动、真参加、真押镖，请用小号！", "warn")
        else:
            self.log_line("已切回演练（只识别自检+打日志，安全）。", "info")

    def render_mode_pill(self):
        live = data.cfg.get("dry_run", True)
        self.switch_mode.configure(text="演练模式" if live else "实战模式", text_color=T.SUCCESS if live else T.DANGER)

    def update_game_pill(self):
        if self.pill_game is None:
            return
        if self._game_connected and self._game_connected[0]:
            self.pill_game.configure(text="● " + (self._game_connected[1] or "目标窗口已连接"),
                                     fg_color=T.PILL_OK_BG, text_color=T.SUCCESS)
        else:
            self.pill_game.configure(text="○ 未检测到目标窗口", fg_color=T.SURFACE_2, text_color=T.TEXT_DIM)

    def _apply_game_state(self, found, summary=""):
        state = (found, summary)
        if state == self._game_connected:
            return  # 状态没变就不动控件，省掉无谓重绘
        self._game_connected = state
        self.update_game_pill()

    def _build_log_panel(self):
        """右侧常驻日志列：各页面/任务的日志统一汇到这里，按来源（秒装备/组队/整理背包…）打标签。
        以前每个页面各有一个日志框，功能一多就散乱；现在收敛成这一处，谁产生的日志靠行首来源标签区分。"""
        panel = ctk.CTkFrame(self.master, fg_color=T.SIDEBAR, corner_radius=0, width=340)
        panel.grid(row=0, column=2, sticky="nsew")
        panel.grid_propagate(False)
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(16, 8))
        head.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(head, text="运行日志", font=T.fonts["h2"], text_color=T.TEXT).grid(
            row=0, column=0, sticky="w")
        ctk.CTkButton(head, text="清空", font=T.fonts["small"], height=26, width=56,
                      corner_radius=T.RADIUS_SM, fg_color=T.BTN, hover_color=T.BTN_HOVER, text_color=T.TEXT,
                      border_width=1, border_color=T.BORDER,
                      command=self.clear_log).grid(row=0, column=1, sticky="e")

        self.log = ctk.CTkTextbox(panel, font=T.fonts["mono"], fg_color=T.SURFACE_2,
                                  text_color=T.TEXT, corner_radius=T.RADIUS_SM, wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 14))
        T.apply_log_tags(self.log._textbox)
        self.log.configure(state="disabled")
        self.log_line("界面就绪。各功能的日志都会汇总到这里。", "info")

    def log_line(self, msg, level="info", source=None):
        """统一日志出口（所有页面/任务都调它）。source 非空时在行首加暗色来源标签，如「秒装备 ›」。
        超过约 2000 行就裁掉最旧的，避免长时间运行把内存吃满。"""
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

    def after(self, ms, func=None, *args):
        self.master.after(ms, func, *args)

    def _tick(self):
        # 抽日志：所有可运行任务页
        # for k in self.RUNNABLE_KEYS:
        #     p = self.pages.get(k)
        #     if p:
        #         p.pump()
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
        title = data.cfg.get("window_title", "梦幻西游")
        offset = data.cfg.get("window_offset", [0, 0])
        targets = data.cfg.get("targets", {})

        def work():
            try:
                all_wins = win_mod.locate_all(title, offset)
                found, summary = self._compute_target_state(all_wins, targets)
            except Exception:
                found, summary = False, ""
            # 回主线程更新（after 由 Tk 在主线程执行，线程安全）
            try:
                self.after(0, self._apply_game_state, found, summary)
            except Exception:
                pass
            self._locating = False

        threading.Thread(target=work, daemon=True).start()

    # ---- 全局急停轮询：鼠标甩到屏幕左上角 或 急停组合键 → 停止一切任务 ----
    def _poll_hotkey(self):
        # ① 物理急停：鼠标甩到所选屏幕角（默认右上角，设置里可改/可关）。注意——默认 sendinput 后端走
        #    SendInput 底层注入，【不经过 pyautogui，没有内置 FAILSAFE】，故必须在这里用真实光标位置自己兜
        #    （覆盖所有后端）。仅在有任务跑时生效，避免空触发；任务停了 _any_running 转 False 不再重复触发。
        corner = data.cfg.get("failsafe_corner", DEFAULT_FAILSAFE)
        if corner != "off":
            try:
                cx, cy = get_cursor()
            except Exception:
                cx, cy = None, None
            if util.in_failsafe_corner(cx, cy, corner) and self._any_running():
                self._emergency_stop(f"鼠标甩到{FAILSAFE_CORNERS.get(corner, '角落')}")
        # ② 急停组合键（默认 Ctrl+Alt+F12）：所有修饰键+主键同时按下的瞬间触发一次。
        mods, key_vk = util.parse_stop_hotkey(data.cfg.get("hotkey_stop", DEFAULT_STOP_HOTKEY))
        if key_vk is not None:
            down = all(util.vk_down(m) for m in mods) and util.vk_down(key_vk)
            if down and not self._hotkey_down:
                self._emergency_stop()
            self._hotkey_down = down
        else:
            self._hotkey_down = False
        self.after(60, self._poll_hotkey)

    def _any_running(self):
        """是否有任意页面的后台任务在跑（供甩角失控急停判断，避免空触发）。"""
        for page in self.pages.values():
            for v in vars(page).values():
                if isinstance(v, TaskRunner) and v.is_running():
                    return True
        return False

    def _emergency_stop(self, reason=None):
        """急停：停掉所有页面正在跑的后台任务，弹提示。reason 标明来源（甩角/热键）。"""
        tag = reason or f"[{data.cfg.get('hotkey_stop', DEFAULT_STOP_HOTKEY)}]"
        n = self.stop_all_tasks()
        if n:
            self.toast(f"{tag} 急停：已停止 {n} 个运行中的任务")
        else:
            self.toast(f"{tag} 急停（当前没有正在跑的任务）")

    def stop_all_tasks(self):
        """遍历所有页面，停掉其上任意正在运行的 TaskRunner（runner / runner_ob 等都覆盖到）。返回停了几个。"""
        n = 0
        for page in self.pages.values():
            for v in list(vars(page).values()):
                if isinstance(v, TaskRunner) and v.is_running():
                    try:
                        v.stop()
                        n += 1
                    except Exception:
                        pass
        return n

    def toast(self, msg):
        """简单的右下角浮层提示。"""
        lbl = ctk.CTkLabel(self.master, text=msg, font=T.fonts["body"], fg_color=T.ACCENT,
                           text_color=T.ON_ACCENT, corner_radius=T.RADIUS_SM, padx=16, pady=8)
        lbl.place(relx=0.99, rely=0.97, anchor="se")
        self.after(16000, lbl.destroy)

    def calib_singleton(self, attr, only, fail_msg, exclude=None):
        self.master.calib_singleton(attr, only, fail_msg, exclude)

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

    def on_close(self):
        pass
