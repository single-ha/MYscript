import customtkinter as ctk


class Splash(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master=master)
        self.pb = None
        self.status = None
        self.overrideredirect(True)
        try:
            self.attributes("-topmost", True)
        except Exception:
            pass
        self.main()

    def main(self):
        W, H = 380, 132
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"{W}x{H}+{(sw - W) // 2}+{(sh - H) // 2}")
        BG, FG, DIM, ACC, TROUGH, EDGE = "#13161c", "#e7eaf0", "#8a93a3", "#4f8cff", "#20252f", "#2a313d"
        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True)
        ctk.CTkLabel(outer, text="梦幻 · 时空 助手", text_color=FG,
                     font=("Microsoft YaHei UI", 20, "bold")).pack(pady=(26, 4))
        self.status = ctk.CTkLabel(outer, text="正在加载图像识别库，请稍候…", text_color=DIM,
                                   font=("Microsoft YaHei UI", 15))
        self.status.pack()
        self.pb = ctk.CTkProgressBar(outer, mode="indeterminate", width=300)
        self.pb.pack(pady=18)
        self.pb.start()
        self.update()  # 立刻画出来，别等到 mainloop

    def refresh(self, content):
        self.status.configure(text=content)

    def on_close(self):
        self.destroy()
