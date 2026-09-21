# -*- coding: utf-8 -*-
"""
配置读写。config.json 用 tasks.<任务名>.* 命名空间存放各任务自己的配置，
顶层只放跨任务共享项（窗口、输入后端、拟人化参数）。这样以后加新任务不会互相干扰。
"""

import os
import sys
import json
import copy
from pathlib import Path

# 数据根目录：config.json / templates / captures 都存这里。
#   - 源码运行：= 项目根（本文件向上三级 mhxy/core/config.py -> 项目根）。
#   - 打包成 exe（PyInstaller，sys.frozen=True）：= exe 所在目录。
#     绝不能用 __file__——onefile 模式下它在临时解压目录 %TEMP%\_MEIxxxx，
#     退出即清空，标定的配置和模板会全部丢失。改用 sys.executable 的所在目录，
#     于是配置/模板/截图都生成在 exe 同级，持久且集中在一个文件夹。
if getattr(sys, "frozen", False):
    DATA_ROOT = Path(sys.executable).resolve().parent
else:
    DATA_ROOT = Path(__file__).resolve().parents[2]

PROJECT_ROOT = DATA_ROOT          # 兼容别名：vision.py / gui/app.py 仍按此拼相对路径
CONFIG_PATH = DATA_ROOT / "config.json"
TEMPLATES_DIR = DATA_ROOT / "templates"
CAPTURES_DIR = DATA_ROOT / "captures"

# 跨任务【共用】的区域键（见 tasks.shared.regions）：在任意任务页标定一次全任务通用。
SHARED_REGION_KEYS = ("activity_list", "bag_list")
SHARED_REGION_LABELS = {"activity_list": "活动列表区域", "bag_list": "背包列表区域"}

# 拓印临摹资产（存 tasks.tuoying，在「工具」页「拓印」里标定，所有副本共用）：
# 点「进入」偶发的「拓印」临摹弹窗（队长窗）的标题/上传按钮模板。可选：标了刷副本遇弹窗自动描摹，
# 不标遇弹窗转手动。读方 = dungeon_base._load_flags / 拓印页，只读 tasks.tuoying。
TUOYING_TPL_KEYS = ("tuoying_title", "tuoying_upload")

# 主界面判断资产（tasks.shared.templates，在「通用」页「标定（公共区域）」里标定）：商城图标 / 活动图标。
# 判断「当前界面是否是主界面」= 在窗口画面里能否找到商城图标（ui/ui_state.is_main_screen）。可选：不标则
# 无法做主界面判定（调用方自行兜底）。活动图标供后续界面判定复用，同处标定。
MAIN_ICON_TPL_KEYS = ("shop_icon", "activity_icon")

# 战斗界面标志（存 tasks.shared.templates.battle_flag，在「通用」页「标定（公共区域）」里标定）：
# 各任务进战斗后的画面元素相同。运镖/宝图用它在战斗期暂停「运镖结束/静止」判定（必标），秘境仅日志诊断（可选）。
# task_config 会把它叠加进各任务的 templates（见 SHARED_TPL_KEYS），任务用 tc["templates"]["battle_flag"] 直接读。
BATTLE_FLAG_TPL_KEY = "battle_flag"
# 需要把共享模板叠加进任务 templates 的键（目前只有战斗标识；shop/activity 由 ui_state 直接读 shared，不叠加，
# 避免无谓污染各任务模板配置）。calibrate_dialog._save 写任务命名空间时会剥掉这些键防止回写冗余。
SHARED_TPL_KEYS = (BATTLE_FLAG_TPL_KEY,)

# 各任务「就绪判定」还要查的共享区域键（这些键已从任务自身 CALIBRATION 移走、只在
# 通用页「标定（公共区域）」里标定一次）。key 与任务名一致；dungeon 指共享 tasks.dungeon。
TASK_SHARED_REQ = {
    "escort": ("activity_list",),
    "secret_realm": ("activity_list",),
    "sanjie": ("activity_list",),
    "zhuagui": ("activity_list",),
    "treasure_map": ("activity_list", "bag_list"),
    "appreciation": ("activity_list",),
    "dungeon": ("activity_list",),
}

# 各任务「就绪判定」还要查的共享模板键（tasks.shared.templates，同 TASK_SHARED_REQ 的语义，只是模板）。
# 战斗标识已从各任务自身 CALIBRATION 移走、移到「通用」页「标定（公共区域）」，运镖/宝图是真实使用方（必标）。
TASK_SHARED_TPL_REQ = {
    "escort": (BATTLE_FLAG_TPL_KEY,),
    "treasure_map": (BATTLE_FLAG_TPL_KEY,),
}

# 所有只存 tasks.shared.regions 的区域键（calibrate_dialog 写入路由 / _save 剥离用）：
# 只有「活动列表区/背包列表区」这俩全任务共用；拓印绘制区已迁到 tasks.tuoying（「工具」页「拓印」），
# 不再经 shared 叠加进任务 regions——dungeon_base.run 单独读它并入 regions。
EXCLUSIVE_SHARED_REGIONS = frozenset(SHARED_REGION_KEYS)


def _mk_dungeon_shared():
    """刷副本的【共享】默认配置块。所有副本进副本前/后流程一致、标定一套共用，只按普通/侠士区分。
    整体存入 tasks.dungeon（同块还含勾选 selected 与运行时 enter_target）。
    模板键含义：
      entry_common / entry_xiashi  活动列表里的普通/侠士副本卡片
      join / select                参加按钮 / 选择副本对话框按钮
      skip / clock / enter         跳过剧情 / 小闹钟(寻路) / 进入战斗按钮
      enter_dungeon                选择副本对话框里的「进入」按钮（普通/侠士两标签区共用）
      xiashi_tab                   侠士进副本前先点的「侠士区」标签页
      confirm                      侠士进副本后各号弹的「确认」按钮
      settlement                   结算界面（副本结束信号，识别到即收尾）
    loop 各键同旧 _mk_dungeon；extra：enter_retry_*/confirm_sec 为侠士「进入+确认」重试参数、
    tuoying_* / enter_check_sec 为拓印临摹处理参数。
    副本内主循环为统一轮询（dungeon_base._run_rounds，user 2026-09-21 拍板）：每帧按优先级检测
    结算界面 → 战斗标识 → 跳过剧情 → 进入战斗 → 小闹钟，见下各键注释。"""
    return {
        "loop": {
            "match_threshold": 0.85,     # 标志模板匹配阈值
            "card_match_threshold": 0.9,   # 活动列表「找副本卡片」的专属阈值：卡片模板若框到多卡公共UI，镜像卡也常拿 0.9x
                                           # 分。真卡(标定原帧)≈0.99 稳过、镜像被挡；专属模板靠标定自检把关（见
                                           # calibrate_dialog._verify_card_tpl 独特性格检），此处阈值只作运行时兜底
            "npc_dialog_sec": 60,        # 点「参加」后等角色寻路到 NPC、弹出「选择副本」对话框的超时
            "poll_sec": 1.0,           # 副本内统一轮询的间隔：空闲/等战斗打完全的空转节奏（默认 1s；调小更灵敏更费CPU，调大更省但不及时发现按钮）
            "step_timeout_sec": 30,      # 副本内统一轮询的「寻路等待」超时：点跳过剧情/小闹钟后在等「进入战斗」出现的空闲上限
            "enter_clock_retries": 3,    # 寻路等待超时后：小闹钟还在→点它重新寻路再等，最多重试这么多次；小闹钟没了→判副本可能已结束进收尾
            "entry_skip_sec": 60,        # 副本内统一轮询的开局阶段空闲超时（传送动画结束、首个动作/结算出现的上限）
            "battle_timeout_sec": 600,   # 单场战斗上限：点「进入战斗」（或识别到战斗已开始）后在等本场打完全的空闲上限
            "max_rounds": 24,            # 副本内点「进入战斗」发起的最大场数（防无限循环，强制收尾）
            "settle_grace": 0.05,        # 结算界面匹配阈值放宽量（结算画面一闪而过，比统一阈值放宽些提高召回）
            "post_skip_sec": 3.0,        # 已废弃（统一轮询每帧都查结算界面，不再需要点跳过后的专属驻留）——保留兼容旧配置读取
            "still_end_sec": 20,         # 兜底判结束：画面连续静止这么久(秒)就判副本已结束（结算界面自动关闭后回到静止场景，不必干等超时）；0=关
            "still_diff": 6.0,           # 平均帧差低于此值视为静止（战斗中画面一直在动，不会误判）
            "enter_retry_max": 3,        # 侠士「进入+确认」重试上限
            "enter_retry_pause": 1.2,    # 重试前回队长重点进入前的停顿
            "confirm_sec": 40,           # 侠士各号点确认的总超时
            "tuoying_detect_sec": 8.0,   # 点「进入」后轮询是否弹出「拓印」临摹界面的时长
            "tuoying_passes": 2,         # 拓印临摹：单遍内沿图案骨架描几轮（每轮各笔画重新随机，提升覆盖）；整个拓印可能需要多遍(=弹窗会再次弹出)，见 tuoying_max_rounds
            "tuoying_lateral": 3.0,      # 拓印临摹：随描轨迹垂直于笔画走向的横向偏移上限(像素)，笔迹加宽更易达标
            "tuoying_sample_step": 5.0,  # 拓印临摹：骨架采样间距(像素)，越小越贴笔画(也更慢)
            "tuoying_stripe_spacing": 8.0,  # 已废弃（拓印改骨架描摹）——保留兼容旧配置读取
            "tuoying_upload_sec": 6.0,   # 点完「上传」后等拓印界面关闭的超时；超时=自动描未被认可→转手动
            "tuoying_max_rounds": 3,       # 拓印临摹最大轮次：一次「上传」后界面可能再次弹出（要拓印两次图案才过），
                                           #   每轮=描+上传+确认界面不再重现；超过轮数仍弹→转手动不复描
            "tuoying_gone_confirm_sec": 1.2,  # 判定拓印界面「真消失」的确认窗口：消失后再持续观察这么久都不重现，
                                          #   才算通过（防止上传后界面一闪即认定成功、实则第二遍还等着描）
            "enter_check_sec": 10.0,     # 普通副本点「进入」后验证已进本的时长（出现结算/跳过剧情等即算进）；超时重试点「进入」
            "scroll_step": -3,           # 活动列表每次滚轮格数(负=向下翻)
            "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
            "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
            "scroll_max_tries": 8,       # 活动列表最多翻几屏找副本卡片
            "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔(带抖动)
            "scroll_reset_top": True,    # 翻找前先把列表滚到顶
            "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)
            "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
            "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内
            "join_same_row_px": 55,      # 认出副本卡片后，「参加」最多允许离开卡片本行多少像素；越带=本行
                                         #   按钮当帧没匹配上，拒绝点隔壁行(曾 d=65 点到运镖卡的参加进错活动)
        },
        "regions": {
            "scene": None,           # 主识别区(整窗或大半屏)
            "activity_list": None,   # 活动列表区域(滚轮在此找本副本卡片)
        },
        "templates": {
            "entry_common": None,    # 活动列表里的普通副本卡片
            "entry_xiashi": None,    # 活动列表里的侠士副本卡片
            "join": None,            # 卡片右侧的「参加」按钮
            "select": None,          # 对话框「选择副本」按钮
            "skip": None,            # 「跳过剧情」按钮（每轮先点它，也用来判上一场打完）
            "clock": None,           # 任务栏「小闹钟」按钮（点它寻路到当前目标）
            "enter": None,           # 副本内「进入战斗」按钮（寻路到位后点它发起本场）
            "enter_dungeon": None,   # 选择副本对话框里的「进入」按钮（普通/侠士共用）
            "xiashi_tab": None,      # 侠士进副本前先点的「侠士区」标签页（仅侠士用）
            "confirm": None,         # 侠士进副本后各号弹的「确认」按钮
            "settlement": None,      # 结算界面（副本结束信号；每轮打完轮询它，识别到即收尾）
        },
        "selected": ["dt_70_common", "dt_60_common1", "dt_60_common2",
                     "dt_70_xiashi", "dt_60_xiashi"],
        "enter_target": {"cat": "common", "pos": 0},
    }


DEFAULT_CONFIG = {
    # ---- 跨任务共享 ----
    "window_title": "梦幻西游",          # 游戏窗口标题关键字（模糊匹配）
    "window_process": "MyGame_x64r.exe", # 只认这个进程的窗口：标题会和终端/编辑器等撞，进程名才稳
                                         #   （游戏窗口类名是随机串无法白名单）。空串=退回纯标题匹配。
                                         #   客户端 exe 改名了就改这里。见 core/window.set_game_process。
    "input_backend": "sendinput",        # sendinput(底层+拟人化, 推荐) / pyautogui / pydirectinput
    "window_offset": [0, 0],             # 整体点击偏移修正 [dx, dy]
    "hotkey_stop": "ctrl+alt+F12",       # 全局【急停】组合键：游戏前台也能按，按一下立刻停止一切正在跑的任务
                                         #   （鼠标被脚本拉着失控时随时叫停）。设置里可改修饰键(Ctrl/Alt/Shift)+主键。
    "failsafe_corner": "top_right",      # 失控急停：任务运行时把鼠标甩到屏幕哪个角即停（独立于鼠标后端，始终生效）。
                                         #   top_right/top_left/bottom_right/bottom_left/off(关闭)。设置里可改。
    "appearance": "dark",                # 界面外观：dark(夜间) / light(白天)，侧栏可切换
    "debug_log": False,                  # 调试日志开关：开启后 level="debug" 的日志才进全局面板（设置页可勾）

    # ---- 目标窗口选择（基础特性，跨任务共享）----
    #   所有任务都基于它确定「操作哪个号」：单开=选 1 个窗口，多开=选多个号轮流操作。
    #   窗口身份用「屏幕位置序号」(左→右，见 window.locate_all 排序)——三个号标题相同、HWND 重启会变，
    #   按摆放位置认号最稳。检测区(listing/scene)留空即「整窗检测」，无需框大区域。
    "targets": {
        "multi": False,            # False=单开(操作1个号) / True=多开(轮流操作多个号)
        "single_index": 0,         # 单开：选中窗口的序号(左→右,从0起)；越界自动回退0
        "multi_indices": [],       # 多开：选中的序号列表（勾选名单是权威）；空=自动跟踪全部(受 max_windows 上限)
        "max_windows": 5,          # 仅「自动全部(multi_indices 为空)」时最多同时操作几个号(0=不限)；手动勾选不受限
        "switch_delay_sec": 0.15,  # 号与号之间切换的停顿(秒,带抖动)，别太机械
        "base_size": [907, 707]    # 基准窗口尺寸[w,h]；「还原尺寸/调整窗口」把号拉回/排成它。默认 907×707，可点「设为基准」改
    },

    # 「调整窗口」排布：最左一列距屏幕/工作区左边缘的空白(像素)。想贴边留白改成 0，想更靠右调大。
    "arrange_left_margin": 100,

    "humanize": {
        "speed": 2.0,             # 整体速度倍率：>1 更快(按比例缩短鼠标移动/按键的拟人化延迟)，<1 更慢更稳【标准抢货档】
        "snipe_speed": 5.0,       # 命中后「下单那一下」的额外速度倍率：只在抢的瞬间生效，越大越快越抢得到(也越不像人)【标准抢货档】
        "click_radius": 4,        # 落点随机半径(像素)
        "px_per_step": 12,        # 鼠标移动每步像素，越大步数越少→越快(但越不平滑)
        "interval_jitter": 0.4,   # 各种间隔的随机抖动比例
        "idle_chance": 0.0,       # 每轮“走神”停顿概率(抢货想快就调到 0)【标准抢货档：关闭走神】
        "idle_min_sec": 1.5,
        "idle_max_sec": 5.0
    },

    # ---- 游戏快捷键：脚本按「动作名」调用的语义映射（ctx.send_hotkey("open_bag")，键名列表）----
    #   ✅ 2026-06-22 已按用户提供的《时空》游戏内【快捷键预览截图】逐格核对/订正（图为权威来源）。
    #     ⚠ 之前那份文字情报有误：F8/O 实际是【未绑定】，活动其实是 Alt+C、聊天其实是 Alt+X。
    #     某项留空 [] 表示该入口没快捷键（个别任务会据此降级处理）。
    #     注：同一物理键在「功能页/战斗页」可能是不同功能，下面取的是【功能页(非战斗)】含义——
    #     脚本导航基本都在非战斗态发起。完整实测全表见顶层 game_hotkeys。
    "hotkeys": {
        "close_panel": ["esc"],          # 关闭面板/复位（Esc 游戏内未绑定，但通用关面板/退栈）
        "open_bag": ["alt", "e"],        # 包裹
        "open_task": ["alt", "y"],       # 任务（订正：旧种子 alt+q 错——时空 alt+q 是召唤灵/默认随机法术）
        "open_activity": ["alt", "c"],   # 活动（订正：实测 Alt+C；F8 其实未绑定，旧情报误把 F8 当活动）
        "open_character": ["alt", "w"],  # 人物
        "open_skill": ["alt", "s"],      # 技能（仅功能页；战斗页 alt+s 是默认法术）
        "open_summon": ["alt", "q"],     # 召唤灵（功能页）
        "open_team": ["alt", "t"],       # 队伍（功能页；战斗页 alt+t 是保护）
        "open_friend": ["alt", "f"],     # 好友
        "open_rank": ["alt", "r"],       # 排行（功能页；战斗页 alt+r 是召唤）
        "open_map": ["alt", "m"],        # 大地图
        "open_minimap": ["tab"],         # 小地图（功能页 Tab）
        "open_system": ["alt", "j"],     # 系统
        "open_mount": ["alt", "k"],      # 坐骑
        "open_guide": ["alt", "h"],      # 指引
        "open_shop": ["alt", "a"],       # 商城（功能页；战斗页 alt+a 是攻击）
        "open_welfare": ["alt", "d"],    # 福利（功能页；战斗页 alt+d 是防御）
        "open_strengthen": ["alt", "v"], # 强化
        "open_guild": ["alt", "b"],      # 帮派
        "open_home": ["alt", "n"],       # 家园
        "open_helper": ["alt", "z"],     # 助战（功能页；战斗页 alt+z 是特技）
        "open_chat": ["alt", "x"],       # 聊天（订正：实测 Alt+X；O 其实未绑定）
        "hide_ui": ["alt", "p"],         # 隐藏界面（功能页）
        "afk": ["alt", "g"],             # 挂机（功能页；战斗页 alt+g 是捕捉）
        "boss_key": ["alt", "l"],        # 老板键
        "pin_window": ["alt", "u"]       # 置顶客户端
    },

    # ---- 《时空》游戏内快捷键【实测全表】（用户 2026-06-22 截图核对，作为今后操控游戏的权威依据）----
    #   只读参考表：脚本实际调用走上面的语义 hotkeys，新增任务/战斗逻辑时来这查键位。
    #   只收录「已绑定」项（无绑定不列，故 F8/O/P/M(战斗) 等未绑定键不出现）。
    #   同一物理键在两页可能是不同功能，故分开记录；用 物理键 -> 功能 表示。
    "game_hotkeys": {
        "field": {                       # 功能页（非战斗）
            "f7": "横竖屏切换", "tab": "小地图",
            "alt+q": "召唤灵", "alt+w": "人物", "alt+e": "包裹", "alt+r": "排行",
            "alt+t": "队伍", "alt+y": "任务", "alt+u": "置顶客户端", "alt+p": "隐藏界面",
            "alt+a": "商城", "alt+s": "技能", "alt+d": "福利", "alt+f": "好友",
            "alt+g": "挂机", "alt+h": "指引", "alt+j": "系统", "alt+k": "坐骑", "alt+l": "老板键",
            "alt+z": "助战", "alt+x": "聊天", "alt+c": "活动", "alt+v": "强化",
            "alt+b": "帮派", "alt+n": "家园", "alt+m": "大地图"
        },
        "battle": {                      # 战斗页
            "f7": "横竖屏切换",
            "alt+q": "默认随机法术", "alt+w": "法术", "alt+e": "道具", "alt+r": "召唤",
            "alt+t": "保护", "alt+y": "任务", "alt+u": "置顶客户端",
            "alt+a": "攻击", "alt+s": "默认法术", "alt+d": "防御", "alt+f": "好友",
            "alt+g": "捕捉", "alt+h": "指引", "alt+j": "系统", "alt+k": "坐骑", "alt+l": "老板键",
            "alt+z": "特技", "alt+x": "聊天", "alt+c": "法宝", "alt+v": "强化",
            "alt+b": "帮派", "alt+n": "家园"
        }
    },

    # ---- 各任务独立配置 ----
    "tasks": {
        "sniper": {
            "loop": {
                "refresh_interval_sec": 0.2,    # 两轮「进货架查看」之间的间隔（带抖动），别太机械【标准抢货档】
                "shelf_load_wait_sec": 1.2,     # 等货架加载的「最长」等待（自适应：画面静止即提前结束，这是上限/超时）
                "shelf_load_min_sec": 0.15,     # 等货架加载的「最短」等待（再快也至少等这么久，给画面起步时间）【标准抢货档】
                "match_threshold": 0.85,
                "after_buy_cooldown_sec": 2.0
            },
            "regions": {                 # 由标定向导写入，相对游戏窗口左上角 [x,y,w,h]
                "listing": None,         # 货架/列表识别区域
                "category_button": None, # 左侧侧边栏的商品类别（如「奇珍异宝」）
                "product_entry": None,   # 右侧信息框里要进的那个商品条目
                "buy_button": None,
                "confirm_button": None
            },
            "watchlist": []              # [{name, template, max_price}]
        },

        # ---- 刷副本·宝图（一次性两阶段状态机；游戏自带自动战斗全托管，脚本只导航+监控+关键点击）----
        "treasure_map": {
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限；主终止是「背包挖空」
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.6,    # 每次「截图→判状态」的节拍（带抖动）
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "still_diff": 8.0,           # 收集/挖宝判「人物静止」的整屏帧差阈值：低于此算静止。
                                             #   太小→待机动画/周围走动/特效让永远判不到静止→误超时；
                                             #   运行日志会实时打印真实帧差，照着设到静止<阈值<走动即可。
                "collect_idle_sec": 4.0,     # 收集阶段：人物连续静止这么久且非战斗非对话→判定收集完成
                "activity_timeout_sec": 30,  # 开活动→找到宝图入口的超时
                "dialog_timeout_sec": 30,    # 等 NPC 对话框出现的超时
                "dig_timeout_sec": 120,      # 单张挖宝（含战斗）超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "scroll_max_tries": 8,       # 滑动找目标最多翻几屏，超了仍没找到→兜底
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）。
                                             #   滚轮查找会在【同一个号】上一气呵成跑完（找到/翻完才轮转下个号），
                                             #   故这里要自等画面静止，别太小（否则截到滚动动画中途、漏识别）
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "join_confirm_tries": 3,     # 认出「宝图任务」后连确认几次「参加」按钮；多次都找不到
                                             #   才判定「已有宝图」(跳过领取直接挖)，防滚动/加载瞬间误判
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None,   # 活动列表区域（滚轮在此找宝图任务条目）
                "bag_list": None         # 背包列表区域（滚轮在此找藏宝图）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "flag_treasure_entry": None, # 活动列表里「宝图任务」条目
                "flag_join": None,           # 「宝图任务」那一行右侧的「参加」按钮（按行匹配点它）
                "flag_tingting": None,       # 对话框「听听无妨」选项
                "flag_next_map": None,       # 挖完弹出的「下一张使用」按钮
                "treasure_item": None,       # 背包里藏宝图道具图标（双击用图靠它定位）
                "flag_bag_arrange": None     # 背包「整理」按钮（可选：回开背包确认前点一下让道具归位）
            }
        },

        # ---- 运镖（一次性循环押镖状态机；游戏自带自动寻路+自动战斗，脚本只导航+监控+关键点击）----
        "escort": {
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限；主终止是「对话框不再弹出」
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "max_escorts": 3,            # 押镖次数：做满即停（与「对话框不再弹出」互为保险）
                "done_idle_sec": 6.0,        # 已是最后一趟、「运镖中」标志消失且无新对话框，持续这么久→判定全部结束
                "no_dialog_giveup_sec": 90,   # 运镖中但标志消失、下一趟对话框迟迟不弹的最大耐心（s），超了按本号结束处理；0=不启用（干等单趟超时）
                "dialog_timeout_sec": 60,    # 点「参加」后等首个「押送普通镖银」对话框的超时
                "confirm_timeout_sec": 10,   # 点「押送普通镖银」后等「确认」按钮的超时（超时容错继续）
                "escort_timeout_sec": 600,   # 单趟运镖（含自动战斗）超时，超了按本批结束处理
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
                "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
                "scroll_max_tries": 8,       # 滑动找「运镖」最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）；
                                             #   滚轮查找在同一个号上一气呵成跑完，故需自等画面静止
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None    # 活动列表区域（滚轮在此找运镖条目）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "escort_entry": None,    # 活动列表里「运镖」条目
                "escort_join": None,     # 「运镖」那一行右侧的「参加」按钮（按行匹配点它）
                "escort_silver": None,   # 对话框「押送普通镖银」按钮
                "escort_confirm": None,  # 点押送后再弹出的「确认」按钮
                "escort_ongoing": None   # 运镖途中常驻的「运镖中」标志（在=还在运镖、不停）
            }
        },

        # ---- 秘境降妖（一次性状态机；游戏自带自动战斗，脚本只导航+监控+关键点击）----
        "secret_realm": {
            "loop": {
                "time_limit_min": 45,        # 时间上限（分钟）安全网，0=不限；每轮主终止是 失败/离开 或 时长判超时。
                                             # ⚠ 必须 > battle_timeout_sec/60（单轮上限），否则多开时最后扛住的号永远等不到自己那轮自然结束就被总上限砍掉
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "max_runs": 1,               # 每个号连跑几轮秘境（每轮=开活动→挑战→直到 失败/超时离开）
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "dialog_timeout_sec": 30,    # 点「参加」后等「秘境降妖」对话框出现的超时
                "dungeon_select_wait_sec": 6,  # 等「选择副本-进入」出现的短超时；没出现=本次无需选副本，跳过
                "step_timeout_sec": 20,      # 确定/继续挑战/任务栏寻路/离开 等每步按钮出现的超时（容错继续）
                "nav_double_gap_sec": 0.3,   # 点任务栏秘境条目后隔多久补点一次（首击常被游戏当聚焦吞掉，和抓鬼同款）
                "battle_timeout_sec": 1800,  # 单轮秘境「超时判定」时长：挂够这么久仍没结束就视为超时、点离开（按真实关卡时限调）
                "dungeon_enter_box": [0.0, 0.5, 0.55, 1.0],  # 「进入」按钮限定的左下角比例框 [x0,y0,x1,y1]（0~1）
                                             #   同款「进入」靠位置区分：只在 scene 这个左下角比例框里找
                "still_min_sec": 0.3,        # 帧差判静止：最短先等
                "still_wait_sec": 2.0,       # 帧差判静止：单次最长等/超时
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
                "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
                "scroll_max_tries": 8,       # 滑动找卡片最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）；
                                             #   滚轮查找在同一个号上一气呵成跑完，故需自等画面静止
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None    # 活动列表区域（滚轮在此找秘境降妖卡片）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "sr_entry": None,            # 活动列表里要点「参加」的那张卡片
                "sr_join": None,             # 那张卡片右侧的「参加」按钮（按行匹配点它）
                "sr_select": None,           # 对话框里「秘境降妖」选项
                "sr_dungeon_enter": None,    # 「选择副本」界面左下角的「进入」按钮（可选）
                "sr_confirm": None,          # 「确定」按钮
                "sr_continue": None,         # 「继续挑战」按钮
                "sr_nav": None,               # 右侧任务栏的秘境任务条目（点它自动寻路到NPC才开始战斗；没有「挑战」按钮）
                "sr_enter_battle": None,     # 难度关卡的「进入战斗」按钮（监控期一出现就点）
                "sr_leave": None,            # 「离开」按钮（失败/超时/结束后点它退出秘境）
                "sr_fail": None              # 「失败」标志（可选，判定该退出）
            }
        },

        # ---- 三界奇缘（答题型：开活动→参加→答题循环，识别到「完成」字样即停）----
        "sanjie": {
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "answer_idle_sec": 30,       # 答题循环里长时间找不到选项按钮就先进「结束确认」缓冲（见下），缓冲后仍无才按结束处理
                "answer_idle_verify_sec": 10,  # 「结束确认」缓冲秒数：空闲超时后不急着收尾，这段时间里继续找选项/完成标志，
                                                #   选项重现就续答（题间过渡动画/多开切前台会短暂不见选项，防止误判结束）
                "answer_pos": None,          # 不标定选项模板时的固定点位 [fx, fy]（答题选项在 scene 内的相对坐标 0~1）；有模板时可留空
                "answer_grid_rows": 4,       # 盲点兜底：在「答题选项区域」里竖向网格候选数（模板认不出选项时按网格盲点+画面反馈作答）
                "answer_grid_cols": 2,       # 盲点兜底：横向网格候选数（同题选项通常竖向排列，横向 2 列可覆盖两列布局）
                "answer_click_diff": 10.0,   # 盲点兜底：点一下后该区域像素平均差>此值=画面变了=答中进下一题；偏大更保守(防动画误判)
                "answer_click_settle_sec": 0.8,  # 盲点兜底：点一下后等画面落定再对比的间隔（带抖动）
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
                "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
                "scroll_max_tries": 8,       # 滑动找卡片最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "answer_area": None,     # 答题选项区域（模板认不出选项时的盲点兜底区；圈住选项所在整片，不必框准单个按钮）
                "activity_list": None    # 活动列表区域（滚轮在此找三界奇缘卡片）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "qq_entry": None,            # 活动列表里要点「参加」的那张卡片
                "qq_join": None,             # 那张卡片右侧的「参加」按钮（按行匹配点它）
                "qq_option": None,           # 答题界面里任意一个选项按钮（脚本点它作答本道题）
                "qq_done": None,             # 答题「完成」标志（今日已答完/次数用完等字样），识别到即停
                "qq_close": None             # 答题结束后的「关闭」按钮（可选）
            }
        },

        # ---- 趣味鉴赏（点爱心玩法：开活动→参加→匹配并点心形图案，点满 N 次或超时即停）----
        "appreciation": {
            "loop": {
                "time_limit_min": 30,        # 时间上限（分钟）安全网，0=不限
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "target_clicks": 5,          # 点击心形图案的目标次数，点满即停
                "heart_timeout_sec": 120,    # 鉴赏环节超时（秒）：这么久没点满也收尾该号
                "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
                "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
                "scroll_wait_sec": 0.8,      # 当前屏没匹配到心形图案后，等这么久才开始滚动（防刚点完画面未落定就滚）
                "tick_interval_sec": 0.5,    # 多开轮转节拍：所有号各推进一步后的间隔（带抖动）
                "scroll_step": -3,           # 图文列表区每次滚轮格数（负=向下翻）
                "scroll_max_tries": 10,      # 滚动找心形图案：连续滚这么多屏还没找到就反向滚回重找
                "scroll_settle_sec": 0.35,   # 找活动卡片时每滚一屏后等画面落定再重找的间隔（带抖动）
                "scroll_reset_top": True,    # 翻找卡片前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏）
                "appr_region": None,     # 鉴赏界面里心形图案所在的「图文列表区域」（心形在此匹配、滚动也在此）
                "activity_list": None    # 活动列表区域（滚轮在此找趣味鉴赏卡片）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "appr_entry": None,          # 活动列表里要点「参加」的那张「趣味鉴赏」卡片
                "appr_join": None,           # 那张卡片右侧的「参加」按钮（按行匹配点它）
                "appr_heart": None           # 鉴赏界面里要点击的「心形图案」（没它就在图文列表区域滚动找）
            }
        },

        # ---- 抓鬼（多人·组队可跳过，队长跑 N 轮抓鬼循环）----
        #   组队设置（已组队/跑完解散/队长）统一在共享 tasks.teaming；这里只放抓鬼自己的模板/区域/流程超时。
        "zhuagui": {
            "loop": {
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "max_rounds": 2,             # 抓鬼轮数 = 领任务次数，跑满即停（默认 2）
                "npc_dialog_sec": 60,        # 参加后等「领取抓鬼任务」对话框出现的超时
                "step_timeout_sec": 30,      # 领下一轮 等按钮出现的超时
                "nav_double_gap_sec": 0.3,   # 点任务条目需连点两次：两次点击的间隔（首击会被当聚焦吞掉）
                "battle_timeout_sec": 2400,  # 一场战斗超时：挂够这么久没打完视为异常（默认 40 分钟）
                "scroll_step": -3,           # 每次滚轮格数（负=向下翻）
                "nudge_max": 4,             # 卡片被列表区域边界裁成半张时，最多朝补齐方向微滚几格仍找不着才告警
                "nudge_step": 3,             # 微滚的格数（朝让被裁那半滚进画面里的方向）
                "scroll_max_tries": 8,       # 滑动找卡片最多翻几屏
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "activity_columns": 2,       # 活动列表每排几张卡片：找「参加」只在条目所属那一列内，
                                             #   避免两张卡片一排时扫到右邻卡片、点错右边的「参加」
                "max_stuck_recover": 3       # 连续卡死多少次就主动停
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "scene": None,           # 主识别区（整窗或大半屏，所有 flag 都在这里找）
                "activity_list": None    # 活动列表区域（滚轮在此找抓鬼卡片）
            },
            "templates": {               # 状态标志模板路径（标定向导裁图写入，tm_ 前缀）
                "gg_entry": None,            # 活动列表里「抓鬼」那张卡片
                "gg_join": None,             # 那张卡片右侧的「参加」按钮（按行匹配点它）
                "gg_claim": None,            # 寻路到任务 NPC 后「领取抓鬼任务」按钮（每轮点它领当前轮）
                "gg_nav": None,              # 任务条目标签（领取后点它触发自动寻路到鬼的位置）
                "gg_cancel": None,           # 点「领取抓鬼任务」后可能弹出的提醒弹窗里的取消按钮（可选；没标=弹窗时不处理）
                "gg_next": None             # 「是否继续」弹窗里的「继续」按钮（战斗打完弹出，点它寻路回 NPC 领下一轮）
            }
        },

        # ---- 公共区域（全局共享）：活动列表区 / 背包列表区 ----
        #   运镖/宝图/秘境降妖/三界奇缘/抓鬼/刷副本都要在「活动」界面那片卡片列表里翻找入口，
        #   宝图挖宝/整理背包都要在「背包」那片物品列表里翻找道具——两片区域画面相同、跨任务共用，
        #   故统一存 tasks.shared.regions：随便在哪个任务页标一次，所有任务自动通用（见 task_config 叠加）。
        "shared": {
            "regions": {
                "activity_list": None,   # 「活动」界面里那一片卡片列表（滚轮翻找副本/运镖/宝图等入口）
                "bag_list": None,        # 「背包」打开后那一片物品列表（滚轮翻找藏宝图/待整理物品）
            },
        },

        # ---- 组队（全局共享资产；不是可运行任务，只存「组队」用到的标定+参数）----
        #   组队是跨窗口握手：队长建队→队员申请→队长接受→双方关窗。多个任务（刷副本/师门/帮派…）都会复用。
        #   故标定的模板/区域放这个共享命名空间 tasks.teaming，与具体任务解耦。
        #   「一键组队」（通用页）的角色参数也放这里：captain_index=谁当队长。
        #   所有多人任务共享的组队设置也放这里（skip_team=是否已组队跳过组队、auto_disband=跑完是否解散），
        #   各副本/抓鬼任务只读这份共享配置，不再各存一份（在 GUI 的「组队设置」组件里统一改）。
        "teaming": {
            "captain_index": 0,          # 队长是所选多开窗口里的第几号（0 起），其余自动当队员
            "skip_team": False,          # 已组队=跳过自动组队，直接由队长跑（无需组队标定）
            "auto_disband": False,       # 跑完后是否自动解散队伍（所有号退队）
            "loop": {
                "match_threshold": 0.85,     # 标志模板匹配阈值
                "tick_interval_sec": 0.5,    # 多号轮转节拍：所有号各推进一步后的间隔（带抖动）
                "form_timeout_sec": 180,     # 组队整体超时（安全网）：到点仍没成形就降级结束
                "create_timeout_sec": 15,    # 队长等「创建队伍/申请」按钮出现的超时
                "accept_timeout_sec": 90,    # 队长等申请、点够人数的超时（每接受到一人会刷新计时）
                "find_leader_timeout_sec": 60,  # 队员在好友列表找队长ID的总超时
                "apply_timeout_sec": 15,     # 队员等「申请入队」按钮出现的超时
                "scroll_step": -3,           # 好友列表每次滚轮格数（负=向下翻）
                "scroll_max_tries": 10,      # 好友列表最多翻几屏找队长
                "scroll_settle_sec": 0.35,   # 每滚一屏后等画面落定再重找的间隔（带抖动）
                "scroll_reset_top": True,    # 翻找前先把列表滚到顶，保证向下扫一遍能覆盖整段(不漏上半截)
                "scroll_end_diff": 2.0,      # 滚一屏后该区域帧差<此值=列表滚不动了(到顶/到底)，据此判「整段翻完」；偏小更保守(动画/高亮时退回 max_tries)
                "scroll_reset_max": 20,      # 「滚到顶」最多上滚几屏的防死循环上限
                "arrow_band_w": 0,           # 找箭头的横向范围：从名字右边缘往右多宽；0=扫到该区域右缘(整行右半)
                "arrow_offset_x": 28,        # 箭头没匹配到时的兜底：点「名字右边缘 + 这么多像素」处
                "arrow_card_ratio": 2.0,     # 好友【卡片】高 ≈ 队长ID模板高的几倍（撑开找箭头的竖直带、覆盖整张卡片）
                "arrow_leader_pos": 0.4,     # 队长ID 在卡片内的纵向位置(从顶算占比，0.4=中上)；兜底点落到卡片纵向中心而非队长ID 的 Y
                "disband_timeout_sec": 60,   # 解散队伍整体超时（安全网）：到点仍没让所有号退完就结束
                "disband_quit_wait_sec": 1.0,# 开队伍面板后等「退出队伍」出现的短等待：超这么久没找到就认为本就不在队、直接关面板
                "max_stuck_recover": 3       # 连续卡死多少次就放弃该号
            },
            "regions": {                 # 相对游戏窗口 [x,y,w,h]，标定向导写入
                "team_panel": None,      # 队伍面板区（创建队伍/申请标签页/同意/退出队伍 都在这片找）
                "friend_list": None      # 好友列表区（滚轮在此翻找队长ID）
            },
            "templates": {               # 组队全局模板（标定向导裁图写入，tm_ 前缀）
                "team_create": None,         # 「创建队伍」按钮
                "team_apply": None,          # 「申请」标签页（点它切到队员入队申请列表）
                "team_accept": None,         # 「同意」按钮（切到申请页后，队员申请那行右侧，见即点）
                "team_apply_join": None,     # 队员点队长右侧箭头后弹出的「申请入队」按钮
                "team_arrow": None,          # 好友列表里队长ID右侧的箭头按钮（在命中右侧小范围内找，可选）
                "team_quit": None,           # 解散用：「退出队伍」按钮
                "leader_id": None            # 队长ID（队员据此在好友列表定位队长）。由 leader_history 维护：
                                             #   历史非空→恒指 templates/tm_leader_id.png，历史空→None
            },
            # 队长ID「当前+最近3历史」库（见 core/leader_history.py）。激活图永远是 tm_leader_id.png，
            #   切换=复制覆盖该文件；历史存 4 个固定槽 tm_leader_id_slot0..3.png，环形淘汰最旧。
            "leader_id_history": [],     # [{slot, label}, ...]，最新在前，最多4项（当前1+历史3）
            "leader_id_active": 0        # 指向 history 的下标（仅 UI 高亮；约定 history[active] 字节==激活图）
        },

        # ---- 整理背包（全局共享能力；像组队一样可单独一键运行，也可被任意流程穿插调用）----
        #   翻包裹找到用户标定的物品图，按各自动作逐个 使用/丢弃/出售。核心在 core.inventory.InventoryOrganizer。
        #   标定的区域/按钮模板/物品清单都放共享命名空间 tasks.organize_bag，与具体任务解耦。
        "organize_bag": {
            "auto_organize": False,              # true=「自动整理背包」：任何走多开轮转的任务流程(运镖/宝图/秘境/副本)
                                                 #   每轮检测一次背包满图标(bag_full_icon)，命中即自动整理一遍。
            "loop": {
                "match_threshold": 0.85,
                "auto_check_interval_sec": 20,   # 自动整理：同一个号两次「检测背包满」之间的最小间隔(秒)，
                                                 #   避免每轮都截图匹配、也避免战斗/过场频繁打断
                "open_item_click": "right",      # 打开物品操作菜单的方式：right(右键,默认)/left/double
                                                 #   注：商会/摆摊出售固定左键点物品弹详情，不看此项
                "step_wait_sec": 0.4,            # 出售/处理多步序列里两步之间的默认等待（如点「商会出售」后等出售窗弹出）
                "action_settle_sec": 0.4,        # 点物品后等操作菜单/面板出现
                "confirm_timeout_sec": 6,        # 等丢弃/出售确认弹窗的超时
                "passes": 1,                     # 整理几遍（丢/卖后列表会变，可多遍兜底）
                "scroll_step": -3,
                "scroll_max_tries": 30,          # 背包可能很长：处理动作会改画面、削弱「帧差判到底」，故给足翻屏上限兜底
                "scroll_settle_sec": 0.35,
                "scroll_reset_top": True,
                "scroll_end_diff": 2.0,
                "scroll_reset_max": 20
            },
            "regions": {"bag_list": None},       # 背包列表区（留空=整窗检测）
            "templates": {
                # 「使用」=直接左键双击物品，无需任何按钮模板（见 core/inventory._ACTION_SPECS["use"]）。
                "discard_button": None,          # 「丢弃」按钮
                "more_button": None,             # 详情面板「更多」按钮（商会/摆摊出售前若有就点，展开更多选项；可不标）
                "shop_sell_button": None,        # 「商会出售」按钮
                "sell_full_button": None,        # 商会出售弹窗里把数量设满的「满」按钮
                "sell_confirm_button": None,     # 商会出售弹窗里最终确认的「出售」按钮
                "stall_sell_button": None,       # 「摆摊出售」按钮
                "stall_shelf_button": None,      # 摆摊出售的「本服上架」按钮
                "confirm_button": None,          # 丢弃的「确定」确认按钮
                "bag_full_icon": None            # 背包满时常驻屏幕上的「满」图标（自动整理靠它判背包满）
            },
            "items": []                          # [{name, template, action}]，action ∈ use/discard/shop_sell/stall_sell（兼容旧 sell）
                                                 # 物品图存 templates/ob_<name>.png
        },

        # ---- 拓印（全局共享的临摹校验能力；不是可单独玩的玩法，是刷副本「拓印」弹窗的自动描摹）----
        #   点「进入」偶发的「拓印」临摹弹窗（队长窗）需按住鼠标沿随机图案描一遍再点「上传」。
        #   识别标题/上传按钮 + 标绘制区，存这份共享命名空间 tasks.tuoying，在「工具」页「拓印」里标定一次，
        #   所有副本共用（dungeon_base / 拓印页只读这份，旧 tasks.shared、tasks.dungeon 残留值一律不沿用）。
        #   「拓印」页可单独「演练」描一遍。
        "tuoying": {
            "regions": {
                "tuoying_area": None,    # 「拓印」临摹界面的图案绘制区（留空=检测到拓印时无法自动描、转手动）
            },
            "templates": {
                "tuoying_title": None,   # 「拓印」临摹界面的标题/标志（点「进入」后被它拦截时识别用）
                "tuoying_upload": None,  # 拓印临摹完要点的「上传」按钮
            },
        },

        # ---- 帮派签到（单人任务页一键操作，也可进日常一条龙个人组）----
        #   对每个所选窗口：打开帮派界面 → 点「福利」页签 → 点「签到」按钮。scene 留空=整窗检测。
        "guild_checkin": {
            "loop": {
                "match_threshold": 0.85,
                "step_timeout_sec": 30,           # 等「福利」页签/「签到」按钮出现的单步超时
            },
            "regions": {"scene": None},
            "templates": {
                "guild_welfare_tab": None,        # 「福利」页签
                "guild_checkin_btn": None,        # 「签到」按钮
            },
        },

        # ---- 领取每日活跃度奖励（单人任务页一键操作，也可进日常一条龙个人组）----
        #   对每个所选窗口：打开活动界面 → 依次点 20/40/60/80/100 五档「领取」按钮。scene 留空=整窗检测。
        "activity_reward": {
            "loop": {
                "match_threshold": 0.85,
                "step_timeout_sec": 30,           # 等每档「领取」按钮出现的单步超时
            },
            "regions": {"scene": None},
            "templates": {
                "act_reward20": None,             # 20 活跃度「领取」按钮
                "act_reward40": None,             # 40 活跃度「领取」按钮
                "act_reward60": None,             # 60 活跃度「领取」按钮
                "act_reward80": None,             # 80 活跃度「领取」按钮
                "act_reward100": None,            # 100 活跃度「领取」按钮
            },
        },

        # ---- 刷副本（副本中枢）----
        #   所有副本进副本前/后流程一致、标定【共用一套】（只按普通/侠士区分），整体存这份共享块，
        #   由 dungeon_base 读取；各副本本身不再有各自模板/区域/超时。
        #   "selected" = 勾选的副本任务名列表（is_dungeon=True 的任务），按顺序一个个刷。
        #   "enter_target" = 运行时字段：启动某副本前，刷副本页写入 {cat, pos}（该副本在其标签区的展示序号），
        #     任务据此在全屏类别「进入」多命中点里取第 pos 个点。
        "dungeon": _mk_dungeon_shared(),

        # ---- 日常一条龙（只做串联：把下面 steps 里勾选的任务按顺序依次跑完）----
        #   完全沿用各子任务自身的流程/标定/演练实战/多开单开设置，本块只存「跑哪些、按什么顺序」。
        #   steps 是【有序】列表，每项 {task, enabled}；界面分「个人/多人」两区、可勾选 + 区内调序；
        #   两区分组固定：多人组（集体屏障：刷副本/抓鬼）在前、个人组（每窗口独立链：宝图/运镖/秘境/
        #   三界奇缘/帮派签到/活跃度奖励）在后，不提供两区互换。group_order 仅供兼容旧配置，引擎不再读它。
        #   单人任务组在本趟流程中 → 多人步跑完转入单人步前【强制解散】队伍（界面开关随之强制打开）；
        #   只有多人步时按 tasks.teaming.auto_disband（开关）决定是否收尾解散。
        #   秒装备不在候选内（无限抢货、不会自己跑完，会卡死整条龙）。
        "daily": {
            "steps": [
                {"task": "treasure_map", "enabled": True},
                {"task": "escort", "enabled": True},
                {"task": "secret_realm", "enabled": True},
                {"task": "sanjie", "enabled": True},
                {"task": "guild_checkin", "enabled": True},
                {"task": "activity_reward", "enabled": True},
                {"task": "dungeon", "enabled": True},
                {"task": "zhuagui", "enabled": True}
            ],
            "group_order": ["multi", "single"],
            "loop": {
                "time_limit_min": 0,          # 整条龙的时间上限(分钟)安全网，0=不限；正常按各子任务自身条件跑完
                "shutdown_after": False,      # 跑完关机：整条龙全部跑完（且有实跑任务）后延迟关机
                "shutdown_delay_sec": 60,     # 关机延迟秒数（留缓冲，可 shutdown -a 取消）
                "schedule": "",               # 定时延迟执行（"HH:MM"，留空=关闭）：点「开始一条龙」后先看它——设了才等到该时刻再真正跑；已过则立即跑
            }
        }
    }
}


def _deep_merge(base, new):
    """把 new 合并进 base 的深拷贝并返回；用于补全旧配置缺失字段。"""
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config():
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
    except (json.JSONDecodeError, OSError):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _deep_merge(DEFAULT_CONFIG, user_cfg)


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def task_config(cfg, task_name):
    """取某任务的配置块，缺失则用默认补。"""
    default = DEFAULT_CONFIG["tasks"].get(task_name, {})
    tc = _deep_merge(default, cfg.get("tasks", {}).get(task_name, {}))
    # 把共享公共区域（tasks.shared.regions）叠加进本任务 regions：已标定的覆盖、空的不动。
    # 「活动列表区/背包列表区」跨任务画面相同，统一在此叠加 → 在任意任务页标一次，所有任务
    # （运行时读取 + 各页就绪判定 + 标定状态）一律自动通用，无需逐任务标定。
    if task_name != "shared":
        shared_r = (cfg.get("tasks", {}) or {}).get("shared", {}) or {}
        shared_r = shared_r.get("regions") or {}
        # 只对「本身定义了 regions」的任务叠加——daily 等无 regions 的块不注入，
        # 否则页面的 _save 会把叠加产物回写进配置、造成冗余（运行期也无意义）。
        if shared_r and isinstance(tc.get("regions"), dict):
            reg = dict(tc["regions"])
            changed = False
            for k, v in shared_r.items():
                if v:
                    reg[k] = v
                    changed = True
            if changed:
                tc["regions"] = reg
        # 共享模板同样叠加（目前只有战斗标识 battle_flag）：任务用 tc["templates"]["battle_flag"] 直接读。
        # 只注入 SHARED_TPL_KEYS 里的键、且任务本身有 templates 才注入，避免污染无模板任务。
        shared_block = (cfg.get("tasks", {}) or {}).get("shared", {}) or {}
        shared_t = shared_block.get("templates") or {}
        if shared_t and isinstance(tc.get("templates"), dict):
            ttp = dict(tc["templates"])
            changed = False
            for k in SHARED_TPL_KEYS:
                if shared_t.get(k):
                    ttp[k] = shared_t[k]
                    changed = True
            if changed:
                tc["templates"] = ttp
    return tc


def set_task_config(cfg, task_name, task_cfg):
    cfg.setdefault("tasks", {})[task_name] = task_cfg
    return cfg
