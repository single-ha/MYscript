class Data:
    cfg = None # 配置数据
    _win_count = 0 # 选中的窗口数量
    _game_connected = None   # 缓存连接状态，只在变化时刷新药丸


data = Data()
