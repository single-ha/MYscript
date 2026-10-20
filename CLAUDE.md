# CLAUDE.md —— 项目交接说明（给新会话的 Claude 看）

> 用中文交流（用户全局偏好）。代码/命令/文件名保持英文。

> **本文件维护原则：小而精（务必遵守）。** 这是每次会话都会被加载进上下文的文件，越短越好。
> 只写**长期有效**的东西：是什么、用户拍板的约束、架构地图、怎么跑、踩过且会复发的坑。
> **不写 changelog**——「改了哪几个文件、第几步怎么修、已验证/未验证、依赖装好没、打了 exe 没、
> 任务清单」都属一次性记录，沉到 `git log`（commit 正文）和 memory，不要堆进本文件。
> 实现细节凡代码 docstring / memory 已写清的，这里只留「一句话 + 指针」，别原样复述。
> 每次更新前先问「这条能从代码/git 派生吗、三个月后还成立吗」；能派生或会过期就别加。
> 发现本文件又开始膨胀（尤其冒出「当前状态」这种流水账容器），主动精简回来。

## 这是什么
《梦幻西游：时空》（网易手游的官方 Windows PC 客户端）的辅助脚本，带图形界面。
原理：**截屏 + OpenCV 图像识别 + 拟人化模拟点击**，不读内存、不注入进程。
具体有哪些功能看 GUI 各页 / `base.all_tasks()`（代码注册表为准，别在这里列清单）。

⚠️ 脚本违反游戏用户协议、有封号风险，已多次向用户说明；用户知情，要求用小号测。
本工具仅供学习交流。

## 用户拍板的关键约束（务必遵守）
1. **命中即抢，不上 OCR**：模板匹配认不了“任意低于 X 价”，所以不做价格过滤。模板只框装备本身。
   - **刷新机制（用户拍板）**：游戏**没有固定刷新按钮**。看货架上新唯一办法是「点左侧类别→点右侧商品→进货架」，
     且货架进去后画面静止、不会自动上新，必须**退出重进**。故“刷新”=每轮重走这条两步路径（类别+商品，固定坐标点击）。
2. **必须拟人化**：贝塞尔曲线移动、加减速、落点随机偏移、按下/抬起与各种间隔随机抖动、偶尔走神。
3. **操作原理越底层越好**（怕封号）：鼠标走 Windows `SendInput`(user32) 注入（见 `core/input.py`）。
   已诚实告知：真正不可检测要硬件级(KMBox/Arduino)/驱动级，本方案不保证 100% 安全。
4. **安全靠 preflight 自检 + 多重急停**：任务无演练/实战模式、启动即实跑；跑前 preflight 校验标定/窗口，未就绪就拒跑（见「停止」条目）。
5. 用户**基本不读代码**，只在被明确告知“需要你亲手改的地方”才动手；要尽量傻瓜化（一键 + GUI）。
6. 用户要求**模块化**、可持续扩展，并要一个**现代、简约、精致、信息密度适中**的 GUI。
7. **活动列表卡片布局（运镖/宝图等「开活动→参加」类任务共用）**：活动入口是**卡片**、**默认两张一排**，
   每张右侧有「参加」。按行找「参加」时**只能在条目所属那张卡片的列内找**，不能横向扫到列表右缘——
   否则会圈进右邻卡片的「参加」、点到隔壁（已踩坑修复）。排数变了改配置 `tasks.<name>.loop.activity_columns`
   即可，不必改代码；实现见 `escort.py` 的 `_find_join_on_row` docstring。
8. **GUI 文字换行铁律（已踩坑两轮）**：所有「可能超一行」的说明性文字（副标题/开关说明/卡内提示/标定状态）
   一律套 `theme.bind_wraplength(label)` + 让标签 `sticky="ew"`/`fill="x"` 占满父容器，
   **禁止写死 `wraplength=数字`**（窗口比它窄就溢出截断）。三个非显而易见的坑见 `theme.bind_wraplength`
   docstring 与 memory `ctklabel-wraplength-gotcha`——**改它前务必看懂，否则极易改回截断/振荡**。
9. **多开通用约束（贯穿全部任务）**：多开各号窗口须**同尺寸**（共用标定点位）；一只鼠标，多开节奏天然慢于单开；
   操作某号前先 `window.activate()` 切前台（`_force_foreground` 绕过焦点抢占并校验，失败则跳过该号、下轮重试，
   绝不在后台号瞎点）。**识别抓图统一走 `GameWindow.grab_screen`（用户拍板 2026-09-26 遮挡感知方案A）**：
   mss 抓的是屏幕真实像素，窗口被盖时画面是别的窗口的会认错——已在前台直抓零开销；不在前台先 `WindowFromPoint`
   抽查 grab 矩形 5 点（中心+四角，GA_ROOT 比对兼容子窗口），**画面完整可见的后台号直接抓、不抢焦点**（多开
   不重叠排布下的常态，切台数归零），**真被遮才 `activate()`**，激活失败返回 None（宁缺勿错，不用被遮画面去
   匹配）；config 顶层 `grab_auto_activate=false` 可关。通用页「调整窗口」排布为**用户拍板**：调基准尺寸 + **2列×2行**网格（第1排贴屏幕/工作区顶、
   最后1排贴任务栏），**最多 5 个窗口，第 5 个居中放屏幕正中**；实现用 `window.work_area()`（SPI_GETWORKAREA）。
10. **GUI 每 tick 镜像控件去抖铁律（已踩坑）**：被 `App._tick`(150ms) 反复调用的 `pump()`/镜像函数
   **只能在该控件状态真变了时才 `configure`**——customtkinter 的 `CTkButton.configure` 只要带 `fg_color`/`hover_color`
   就**无条件整只重绘**（见 ctk_button.py），无脑每 tick 调会让**可见页**那一行高频繁闪（未显示的页不重绘故不显现；
   起始页日常卡 `sync_run` 曾如此）。写法：先算 (文字, 风格) 签名，与上次相同直接 return（同
   `sniper._update_wait_button` / `App._refresh_run_status` 的既有写法）。
11. **识别命中→点击前必须拟人停顿（user 拍板 2026-10-20）**：除秒装备(sniper)外，凡「模板识别命中后点击」
    一律先停 0.5~1.5s 再点；组队握手/队员准备这类连贯动作用短停顿 0.3~0.6s。时长**写死在 core/input.py**
    的 CLICK_DELAY/TEAM_CLICK_DELAY（不进 config、不上设置页）。统一入口 base.Task.click_after_hit(ctx,x,y[,mouse=][,delay=])
    （core 层各自 _click_after_hit），**新任务别直接 ctx.mouse.click**。排除：sniper、弹窗守卫点×、抓鬼双击第 2 下、
    非识别驱动的盲点（区域中心右键关面板 / ui_state 交替右键）。

## 架构（三层，包名 mhxy/）
> 文件树是「地图」，只给一句话功能；实现细节看各文件 docstring 与 memory，别往这里抄。
```
启动.bat            双击入口 -> python start.py
start.py            装依赖 + 启动 GUI
config.json         配置（标定后生成；用 tasks.<任务名> 命名空间）  config.example.json 示例
templates/          装备/模板图   captures/  命中截图
mhxy/
  core/   通用基础设施（与玩法无关）
    config.py   配置读写（DEFAULT_CONFIG / load/save / task_config / set_task_config）
    window.py   GameWindow（locate/rect/activate/坐标换算）+ grab() 截图 + set_dpi_aware()
    vision.py   load_template / save_image / match() / frame_diff()（兼容中文路径）
    scan.py     通用「滚动查找」scroll_search()（翻列表/翻包裹统一底层）；详见 docstring + memory scroll-search-scan
    input.py    Mouse 类：SendInput 底层 + human_move/click/sleep/maybe_idle
    context.py  TaskContext：打包 window/mouse/cfg/log/stop_event 给任务
    runner.py   TaskRunner：后台线程跑 Task + 线程安全日志队列
    rotation.py 多开轮转推进器：连续推进到等待点才让出；详见 docstring + memory rotation-engine
    teaming.py  TeamFormation：跨窗口组队握手编排 + run_disband() 解散队伍（每号同一套退队流程）
    inventory.py InventoryOrganizer：整理背包（翻包裹逐物使用/丢弃/出售）的可复用编排，只依赖 ctx；详见 memory organize-bag-task。
                      收尾关背包优先点共享「面板关闭按钮」(panel_close 的「×」)、未标定/找不到才降级
                      ui_state.back_to_main_screen（与宝图 treasure_map 同款；找按钮走 ui_state.find_panel_close，
                      共享层只找不点、点击和可打断等待留任务侧）。
                      自动整理入口 ctx.maybe_auto_organize()（user 2026-10-08 拍板：**只在日常串跑的任务交接处触发**，
                      daily._organize_between 在换下一个任务前调用；单个任务执行中绝不触发，曾每 tick/切前台检测被打断）——rotation 的
                      between_steps 钩子已不再挂这功能
    account_history.py 账号库（登录游戏用）：10 个固定物理槽 + 有序登录队列（最多 5 个），纯逻辑零 GUI；详见文件 docstring
    tuoying_flow.py TuoyingFlow mixin：拓印临摹弹窗的共享编排（探测「拓印」界面→沿图案描→点「上传」→
                    轮询「未完成态/拓印标题」判定是否续描或完成，直到界面消失）；刷副本(dungeon_base)与宝图(treasure_map)共用，
                    资产/参数只读 tasks.tuoying；详见文件 docstring
  tasks/  可插拔任务
    base.py     Task 基类 + 注册表（register/get_task/all_tasks）+ _make_rotation()（包多开轮转）+ dungeon_tasks()
    sniper.py   SniperTask（抢关注；内部名仍叫 sniper，tasks.sniper 不改名）：preflight() 自检 + run() 主循环；刷新=每轮重进货架 _enter_shelf()
    escort.py        EscortTask（运镖）：开活动→参加→押送普通镖银→循环押满次数
    treasure_map.py  TreasureMapTask（宝图）：开活动→自动判是否已有宝图(找不到「参加」或翻完全列表没认出卡片=已有,
                      跳过领取直接挖)→收图/挖宝 领奖 两阶段状态机。点「听听无妨」后检测「拓印」弹窗（TuoyingFlow），
                      命中则自动临摹、完成该号**整条任务从头重跑**（回开活动）；没弹继续原流程
    appreciation.py AppreciationTask（趣味鉴赏）：开活动→参加→匹配并点击心形图案，点满 target_clicks 或鉴赏
                      超时即停；当前屏没匹配到就在「图文列表区域」滚动再找（滚动只在该区内，反向滚回防漏）
    secret_realm.py  SecretRealmTask（秘境降妖）：开活动→参加→挑战→盯「进入战斗」续战，可连跑 max_runs 轮
    login.py         LoginTask（登录游戏）：按账号库里**有序**挑中的号（tasks.login.accounts，≤5）逐号 subprocess 起客户端，
                      新流程（user 2026-10-09 按实测改版）：切换账号→滚轮翻账号卡并点→进入游戏→更换角色→「已有角色」标签
                      →点该号角色卡→等主界面；每个号各起一次客户端（user 2026-09-29 拍板），窗口靠 hwnd 差集认新弹的那个
                      (core/window.wait_new_game_window，不碰已开好的号)。账号卡片+**角色卡片**两张图都归 core/account_history
                      的物理槽（登录标定只管 4 个流程模板+账号/角色两个列表区域，账号/角色图不进标定向导）；「更换角色」点完 1 秒
                      （loop.switch_role_tab_wait_sec）没见「已有角色」标签就再点一次（最多 loop.switch_role_retry_max 次，见 _role_tab_step）。
                      ⚠ 它**不在 daily 链里**（无 ENSURE_MAIN_ON_START / POPUP_GUARD_OFF 两处关开关，见文件 docstring），运行入口只有日常页登录区
    weekly_base.py   WeeklyBaseTask 周常基类（门派闯关/海底世界/迷魂塔共用）：多人先自动组队（tasks.teaming，
                      只驱动队长窗口），两阶段循环「领任务(开活动→找卡片→参加→寻路→点自配 confirm)→做任务
                      (轮询 战斗标识/进战/小闹钟，三样全无满 clean_need_sec=判完成)→再开新一轮」直到 中止/时间上限；
                      中止(立即停)=翻完无卡/无参加/NPC超时/进不了场景；三任务薄子类只用 @register + name/title。
                      ★ 门派闯关多一步「领取任务」：点完 NPC「参加活动」后还要点它才真正领到任务（user 2026-09-28），
                      由类属性 `NEEDS_CLAIM_TASK` 开关（只有 sect_gate 置 True）＋**必标**模板 `claim_task` 驱动；
                      缺标 preflight 直接拒跑（preflight 的必标列表取自 spec，不写死——否则 spec 加行会漏检）；
                      但某轮等不到该按钮仍只提示不阻断。另两个周常用 `without_templates()` 从标定 spec 里
                      剥掉该行（必标会被迫去标用不上的图），向导里不会出现该项。⚠ 加模板键必须同时登记进
                      `weekly_base._FLAG_KEYS`——`_load_flags` 只按该列表建字典，漏登记会静默失效。
    dungeon.py       DungeonTask（组队/一键组队）：把所选多开窗口组成一队即停（通用页「一键组队」跑它，
                      角色参数存共享 tasks.teaming）。name 仍叫 "dungeon" 仅为兼容；刷副本页跑的是选中副本而非它。
    disband.py       DisbandTask（解散队伍/一键解散）：让所选各号都退出当前队伍（每号同一套流程，不分队长队员），
                      复用 teaming.run_disband；通用页「一键解散」跑它，副本勾「跑完解散队伍」也调它
    dungeon_base.py  DungeonBaseTask 通用副本基类：子类只写 name/title/cat(侠士 xiashi / 普通 common)；
                     进副本「进入」按钮普通/侠士共用，侠士先进副本前先点「侠士区」标签页(cat 区分)，再用
                      vision.match_multi 多命中点按(行,列)取同标签区第 N 个「进入」；侠士进副本后轮询各队员点「确认」，
                      **队长不弹该按钮、绝不等他**（详见文件头④）；

                     副本内每轮=点跳过剧情→点小闹钟寻路→点进入战斗→等战斗，循环到副本结束
    dt_70_xiashi.py / dt_60_xiashi.py / dt_70_common.py / dt_60_common1.py / dt_60_common2.py
                      五副本薄子类（侠士×2 / 普通×3，is_dungeon=True）；已删 taohaiqu.py（蹈海去被这 5 个取代）
    organize_bag.py     OrganizeBagTask（整理背包）：工具页(OrganizeBagPage)可单独跑的共享能力封装，逐号 activate→core/inventory 整理；详见 memory organize-bag-task
    tuoying.py          TuoyingTask（拓印）：刷副本偶发「拓印」临摹弹窗的自动描摹能力封装，工具页「拓印」可单独跑一遍完整临摹
                        （标定存 tasks.tuoying；描→自动点「上传」→确认界面不再重现，与 dungeon_base 判据一致）；
                        完整自动流程（探测→描→上传→确认）参照 core/tuoying_flow.py 的 TuoyingFlow（本文件是『手动把临摹界面调到前台再跑』的单窗版）
  tools/
    calibrate.py 旧的命令行标定（已不被 GUI 调用，仅留作 CLI 备用）
  ui/
    theme.py            配色/字体/圆角令牌（改这里整体换肤）+ bind_wraplength 换行助手（见约束 8）
    app.py              主窗口：侧边导航（起始页置顶/默认）+ 通用页/任务配置页(ConfigPage)/工具页/SettingsPage/AboutPage
    roi_overlay.py      全屏框选组件（纯 tk，冻结截图上拖框，返回屏幕绝对 ROI）
    calibrate_dialog.py GUI 内标定对话框（区域 + 模板缩略图画廊 + 加装备），按任务 CALIBRATION spec 驱动
    leader_gallery.py   队长ID 库画廊（见下「队长ID 库」约束）
    account_gallery.py  账号库弹窗（登录游戏用）：每号框**两张图**（账号卡片 + 角色卡片）入库 + 挑最多 5 个进登录队列，
                      仿队长ID 库；数据/槽文件见 core/account_history.py
    inventory_items_dialog.py 整理背包「物品清单」管理弹窗（缩略图+名字+动作下拉+框选添加，写 tasks.organize_bag.items）
    daily_card.py       起始页「日常任务卡」：把「日常」页的运行控制（开始/停止 · 调整窗口/刷新 · 各设置开关）
                        +「已勾选任务」只读清单搬到起始页；整卡可折叠；设置各写自己的命名空间、与「日常」页同源，
                        运行由 DailyPage 单例 runner 承载（本卡只是入口 + 状态镜像，见文件 docstring）
    pages/organize_bag.py  OrganizeBagPage：整理背包独立页（工具分类页「整理背包」tab）——一键整理/标定/管理物品；
                          原在通用页卡片，迁到工具页；共享命名空间 tasks.organize_bag。「自动整理背包」开关
                          已迁到「日常」页控制区（影响日常运行中自动清背包）
    pages/config_page.py  ConfigPage：任务配置页（导航「🎛 任务配置」，2026-09-23 起取代原 single/multi 两页）——
                          单人/多人全部任务的 参数+标定 收拢成一个可滚动界面，无任何运行入口；登录1卡(最上)
                          + 单人8卡(按 config.SINGLE_TASK_ORDER) + 多人2卡(刷副本/抓鬼；组队开关已不在本页，
                          见下「已组队」条)。
                          各任务页文件（treasure_map/secret_realm/appreciation/sanjie/escort/guild_checkin/
                          activity_reward/guaguale/dungeon/zhuagui/login）均改为无 run 的配置卡类 *Config，参数「改动即保存」
                          （common.param_entry 失焦/回车写回 config）；副本勾选唯一入口仍留在「日常」页
                          （DungeonPicker，共享 tasks.dungeon.selected），本页刷副本卡只做共用标定+就绪，不含勾选。
    pages/login.py        LoginConfig：登录游戏的配置卡（客户端路径/启动参数/等待参数 + 标定入口 + 账号库按钮，共享 tasks.login）；
                          就绪度自绘（账号库里标几个用几个，账号本身不在这标）。日常页登录区**不设标定入口**，
                          只留「账号库…/开始登录」+「⚙ 设置」按钮跳转来这里（daily._goto_login_config）
    pages/start.py       StartPage：起始页（导航「🏠 起始」，启动默认页）——登录游戏卡 + 日常任务卡（daily_card.DailyRunCard）
    pages/tuoying.py       TuoyingPage：拓印独立页（工具分类页「拓印」tab）——标定/就绪状态/一键跑完整临摹；
                          共享命名空间 tasks.tuoying（原在通用页公共标定，迁到这里）
    pages/tools.py      工具分类页（Tab: 商城(抢关注) / 整理背包 / 拓印）
    ui_state.py         界面状态判定（与玩法无关）：is_main_screen 主界面判定 + is_present 标志判定（战斗标识等）；
                          back_to_main_screen 逐层 ESC 回主界面 + find_popup_close（弹窗守卫「×」）/
                          find_panel_close（共享面板关闭按钮「×」，宝图/整理背包收尾共用，只找不点）
```

### 任务模块约定（加新功能照此做）
- 任务在**后台线程**跑，通过 `ctx.log(msg, level)` 输出（level: info/hit/warn/error），
  循环里**勤查 `ctx.should_stop()`**，绝不直接碰 GUI。
- config 用 `tasks.<name>.*` 存各任务配置（regions/watchlist/loop 等）。
- **多开轮转用 `core/rotation.py`**（非阻塞状态机：每号一份 record，处理方法只推进一小步，能往下做就 `_goto`
  改 state、在等待就不改 state；推进器据此「连续推进到等待点才让出」省切前台）。逐号任务接入走
  `base.Task._make_rotation()`，只传 `step_fn(rec)`；有跨窗口握手的自己构造 `RotationConfig`（见 `teaming.py`）。
  **铁律：监控态未触发转移时绝不 `_goto`**，否则在一个号上空转盯屏、饿死别号。详见 memory `rotation-engine`。
- 加新任务示例：新建 `mhxy/tasks/xxx.py` 写 `@register class XxxTask(Task)`；在 `tasks/__init__.py` import；
  在 `ui/app.py` 仿 `SniperPage` 加页面 + 在 `App.NAV` 加项。
- **日志统一到「全局日志面板」（约束，别再各页造日志框）**：日志框只此一处——常驻主窗口右侧（`App._build_log_panel`），
  统一出口 `App.log_line(msg, level, source)`。新页面**不要**自建日志框：设个类属性 `LOG_SOURCE = "短名"`，
  页内 `_log_line` 照范式写成一行转发 `self.app.log_line(msg, level, getattr(self,"LOG_SOURCE",None))`，
  全局面板会按 source 打来源标签（如「抢关注 ›」）。一页里有多种来源（如通用页的组队/解散）就在
  `pump`/各消息处显式把第三个参 source 传成对应短名覆盖。
  **日志来源可随任务动态变**：`TaskRunner._log` 会带上 `task.current_log_source`（无则 None）入队，
  GUI 侧统一走 `runner.drain_logs()` 取 `(level,msg,source)`、source 为空才回退本页 `LOG_SOURCE`。
  「日常」链即靠它把每步日志的来源标签显示成当前子任务（`DailyTask.current_log_source` 每步改写，
  `DailyPage._SINGLE_SOURCE` 把任务名映射成短名如「宝图/刷副本」，非任务日志回退「日常」）。
- **副本中枢（「刷副本」页 = DungeonPage）**：副本统一收进该页用**勾选框多选**、点「开始」**按展示顺序一个个顺序刷**
  （一个跑完自动接下一个；某副本 preflight/异常失败**跳过继续下一个**，最后汇总；「停止」即停整个队列）。
  **加新副本只需写个 `is_dungeon = True` 的 Task**、在 `tasks/__init__.py` import——
  `base.dungeon_tasks()` 自动把它列进勾选清单，GUI 不用改。约定：勾选结果存 `tasks.dungeon.selected`（字符串列表）。
  ⚠ **勾选框只决定「刷不刷」，不决定顺序**（user 拍板 2026-09-27）：刷的顺序**一律按展示顺序**
  （侠士本在前、类内等级低→高，然后普通本）。唯一顺序基准 = `base.dungeon_display_names()`
  （另有 `dungeon_cat_order()`/`dungeon_display_layout()`），**三处都用它**：GUI 勾选区排列
  （`ui/dungeon_picker.py`）、运行时排序（`daily._selected_dungeons` 按它过滤勾选结果）、
  进副本点第几个「进入」（`base.enter_target_for`）——三处不一致就会刷错顺序/进错本，
  故别在任何一处另算一套。副作用：config 里 `selected` 的**存放顺序无关紧要**（历史遗留/手改的乱序不影响跑的顺序）。
  ⚠ **一个副本都没勾 = 「刷副本」整步不跑**（user 拍板 2026-10-19，等同整行关掉：不设集体屏障、
  不触发收尾/强制解散、不打日志；只剩它时会直接报「没有勾选任何任务，已停止」）。判据
  `daily._selected_dungeons()` 空即剔除，**UI 三处必须同源**：`daily_card.enabled_names`、
  日常页 `_refresh_exec_order`/`_renumber`（经 `_runs_now`）、`dungeon_picker._load_selection`
  （不再「空则默认勾第一个」）——不同源就会「界面显示这步、引擎却跳过」。行就绪态因此多一态
  「未勾选副本」（`DailyPage._ready_meta`），行级单跑空勾选直接拦下不启动；点副本名单跑
  （`_start_single_dungeon`）自带 `selected=[该本]`，只查 `_dungeon_calib_ok`（不含勾选那层）。
  **所有副本共用一套标定**（只按普通/侠士区分）：模板/区域/loop 全存共享 `tasks.dungeon` 命名空间，一次标定覆盖全部副本。
  **组队设置（队长 captain_index / 已组队 skip_team / 跑完解散
  auto_disband）统一存共享 `tasks.teaming`**，各多人任务（蹈海去/抓鬼等）的 preflight/run 都读这份共享配置；
  **「跑完解散」开关在「日常」页设置区（`DailyPage.var_disband`）；「已组队」开关（`skip_team`）2026-09-23
  起在「日常」页设置区也新增一份（`DailyPage.var_skip_team`）**，与「跑完解散」并排、写同一份共享配置；
  队长/队长ID 在「通用」页「选择窗口/队长」里选。⚠ **拍板脉络**：2026-09-23「已组队」先迁出「任务配置」页
  （该页顶部组队卡已删），同日落定「不涉及周常/工具页」——周常页的 `common.TeamSettingsCard`（含已组队开关）
  原样保留作第二入口，任务子页（刷副本/抓鬼）仍不放组队控件。
  - **「已组队」开关（skip_team）**：勾上=已自行组好队，副本跳过组队握手、直接由队长开刷；preflight 随之放宽
    （不要求多开≥2、不查组队资产，只需队长那个号能定位）。入口在「日常」页设置区（`DailyPage.var_skip_team`，
    与「跑完解散」并排）＋ 周常页 `TeamSettingsCard`；「任务配置」页已移除。各多人任务（副本/抓鬼/周常）的
    UI 就绪判定都读同一份。
  - **副本通用实现 + 多命中点「进入」定位（`tasks/dungeon_base.py`）**：副本逻辑统一抽成 `DungeonBaseTask`
    子类只写 `name/title/cat(侠士 xiashi / 普通 common)`；PREF 已废弃。
    「进入」按钮**普通/侠士共用**一张（`templates.enter_dungeon`）；`xiashi_tab` 为侠士区标签页——
    侠士副本进副本前先点它切到侠士区再找「进入」（cat 区分，普通不用切）。
    进副本列表里**同标签区几个「进入」长得一样**，无法靠图像区分 → 用 `vision.match_multi` 全屏检出该标签区
    「进入」模板的**所有命中点**，按(行,列)排好，取「当前副本在该标签区展示顺序里的第几个」点
    （以 GUI 该区展示顺序当基准，**不能用勾选队列算序号**——只勾一个时队列序=0，但物理位置未必是列表第一个，曾进错副本）。
    「当前副本是第几个」= 该副本在其标签区展示顺序的序号（`tasks.base.enter_target_for`，与 GUI 勾选区同基准；
    日常引擎 `_run_collective_dungeons` 开跑前写入内存 `tasks.dungeon.enter_target={cat,pos}`，不落盘）。
    侠士副本在点完「进入」后多一段入本步骤，**⚠ 2026-10-11 重构后流程**（`_xiashi_enter_popups`）：
    **队长点「进入」→ 留下来在【队长窗口】等「确认弹窗」或「拓印弹窗」二选一**（互斥，弹一不弹二）——
    · **确认弹窗** → 轮询各队员窗口、用共用「确认」按钮模板把队员确认点掉，继续进本。
      **队长绝不当等待对象**（user 2026-09-28：曾把队长放进等待队列 → 永远等不齐 → 必中止；
      只剩队长一个号时直接算完成、连 `confirm` 模板都不用标）。⚠ **队长窗口的确认弹窗里没有「确认」
      按钮**（user 2026-10-11 拍板）：识别「确认已弹出」用**弹窗本身的标志** `confirm_popup`（可选标定，
      标弹窗标题/边框等任何独特区域，不是按钮）——它只作信号、不点任何人；不标则没这信号、靠队员
      确认轮询兜底（照样能进本）。**2026-10-11 文案统一改「准备」**：按钮/弹窗模板键改
      `ready`/`ready_popup`、图标明「侠士本准备按钮 / 侠士本准备弹窗」（旧 `confirm`/`confirm_popup`
      键有一次性迁移自动搬路径，不用重标）。
    · **拓印弹窗** → `_auto_trace` 临摹两遍+上传（描→上传→再描→再传）→ **队长重新点「进入」**
      （第一次进入被拓印拦截，描完回到列表再点一次；重进封顶 `tuoying_reenter_max` 次）→ 再等弹窗。
    · **都没弹** → 队员窗口可能弹了确认（队长窗不一定弹）→ 确认轮询兜底。
    **队员确认超时 → 返回队长窗口重新点「进入」**（enter+confirm 外套 `enter_retry_max` 次重试）。
**「拓印」临摹弹窗（user 2026-09-08 反馈、2026-09-09 升级）**：点「进入」后**队长窗口偶发**弹「拓印」临摹界面（队员不弹、
    无放弃按钮；与侠士「确认弹窗」互斥、弹其一不弹另一，见上），需按住鼠标沿随机图案描一遍再点「上传」。处理：识别 `tuoying_title` → `core/scribble.py` **沿图案骨架描摹**
    （先截绘制区画面、形态学分离前景线条→Zhang-Suen 细化取骨架→确定性贪心拆成尽量少的连续笔画→每笔按住沿骨架走+横向
    锯齿加宽笔迹；只描图案本身，**不盖满整区**——拓印判「完成度>60%」，描到图案外会拉低完成度）→ 点 `tuoying_upload` → 等界面消失；
    **资产全可选、不标不拖累就绪度**（人不标=遇弹窗转手动，描完脚本自动续跑；3 种触发见
    `core/tuoying_flow.py` 的 `TuoyingFlow`——刷副本(dungeon_base)/宝图(treasure_map)共用）。识别不出图案/截图失败 → 转手动兜底（等界面消失自动续跑），绝不盲目乱描。
    ⚠ **上传后轮询判定续描/完成（user 2026-10-19 拍板）**：一次「上传」后**图案相同的界面可能再次弹出**，需再描一遍。
    `TuoyingFlow._auto_trace` 改为「描→上传→轮询『未完成态/拓印标题』判定」：检测到「未完成态」→新图案生成，继续描下一遍；
    「未完成态」缺席且「拓印标题」缺席（持续 `tuoying_gone_confirm_sec` 短确认，防单帧误判）→拓印完成；
    判据依赖标定「拓印标题」，期间标题仍在会一直等待（未标标题时不误判完成，转手动兜底）。不再按固定「最大轮数」限制。
    `scribble.trace_pattern` 是通用能力：任何「沿图案描一遍」类校验都能复用。
    ⚠ **标定位置（user 2026-09-08 拍板、2026-09-18 迁工具页、同日拍板只读新值）**：`tuoying_title`/`tuoying_upload`/`tuoying_area` 在**「工具」页「拓印」**里标，
    存 `tasks.tuoying`（模板键集 `TUOYING_TPL_KEYS`），一次标定覆盖全部副本；读取 = `dungeon_base._load_flags` +
    `_tuoying_area()`（绘制区单独并入 regions），**只读这份新值——旧 `tasks.shared` / `tasks.dungeon` 残留一律不沿用**。
    已从「通用」页公共标定移除、`TASK_SHARED_REGIONS` 已删（`EXCLUSIVE_SHARED_REGIONS` 现只含活动/背包两键）。
  - **主界面判断（ui/ui_state.py）**：`is_main_screen(cfg, window)` 在窗口画面里找**商城图标**（`tasks.shared.templates.shop_icon`，
    同「标定（公共区域）」里与活动图标 `activity_icon` 一起标，键集 `MAIN_ICON_TPL_KEYS`）→ True/False；未标/无窗口/抓图失败返回
    **None（调用方自行兜底，别当非主界面）**。任何任务要判断「是否已回到主界面」都用它。
    同文件还有**标志判定 `ui_state.is_present(scene, flags, flag_key, threshold)`**（通用 `_present`：场景里找到该标志模板=True，
    缺失/没找到=False 不抛错）——战斗标识 `battle_flag` 判定即走它（运镖/宝图/秘境），各任务不再各自复制 `_present`。
- **日常分「个人/多人」两区（user 拍板，2026-09-07）**：`tasks/daily.py` 里
  `CHAINABLE_SINGLE`=个人组（宝图/运镖/秘境/三界奇缘/帮派签到/活跃度奖励/趣味鉴赏/刮刮乐，每窗口独立链）、
  `MULTI_BARRIER`=多人组（刷副本/抓鬼，集体屏障：所有活跃号停靠同一步等齐→组队→队长跑→放行）。
  `GROUP_OF` 由任务名判定分组；**steps 全局有序=执行顺序**（按 `tasks.daily.GROUPS` 两段拼接：多人在前、
  组内保留存储相对顺序；两区分组固定、不提供互换）。**整组开关 `tasks.daily.group_enabled`**（{single,multi}→bool，缺省
  全开）：区头开关整组停用/启用，引擎 `_enabled_steps` 过滤时跳过整组（行级 enabled 独立保留，重开整组即恢复）。
  **步骤清单必须四处同源（引擎也算一处）**：老 config 存储 steps 里没有的新任务行，界面
  `DailyPage._normalize`/`daily_card.ordered_steps` 与引擎 `DailyTask._enabled_steps` 一律「补到末尾、默认启用」
  （已禁用的行带 enabled=False 存着，谁都不会把它补回来），且四者都按唯一的 `tasks.daily.GROUPS` 分段——
  否则就会「界面显示这行/这个顺序、引擎却跳过/先跑别的」。`GROUPS` 是区序唯一来源（UI 两个 `_GROUPS` 也引用它），
  加新区只改 `GROUP_OF`+`GROUP_TITLES`。**进个人组的前提**是任务有 `CHAINS_PER_WINDOW=True` +
  `make_chain_driver(wctx)`（每窗口 record + 单步推进，非阻塞；帮派签到/活跃度已是轮转状态机）。
  **三界奇缘/趣味鉴赏/刮刮乐 置 `CHAIN_SEQUENTIAL=True`（user 拍板 2026-09-22；刮刮乐 2026-10-20 加入，
  因子：刮奖是几秒的长连续动作，切前台会打断节奏）**：日常多开时**逐号顺序执行**——
  主循环指定唯一持有者、`_drive_chain_until_yield(blocking=True)` 把它从头做完整再放行，排队的号跳过等待
  （不做跨号轮转）；窗口失效的号不占持有权（不会饿死别号）。任务单独跑（独立页/多开轮转）不受影响。
  多人步只走集体 `_run_collective`，不建独立链。
  **「登录游戏」不在链里（user 拍板 2026-09-29）**：它自己起客户端（没有「已开好的号」可跑），
  故不在 daily.steps/GROUP_OF/SINGLE_TASK_ORDER，入口只有「日常」页顶部可折叠登录区——
  点「账号库」弹窗（`ui/account_gallery.py`，仿队长ID 库；数据/槽文件在 `core/account_history.py`）：
  **最多标定 `LOGIN_ACCOUNT_SLOTS`(10) 个账号**（框选账号卡片/头像，物理槽 `templates/tm_login_account0..9.png`，
  名字可改），**最多挑 `LOGIN_MAX_ACCOUNTS`(5) 个去登录**、按挑的先后依次登录
  （存 `tasks.login.accounts` 有序 list + `account_library` 清单，**账号图不再进登录标定向导**）。
  点「开始登录」起独立 `DailyPage.login_runner`
  （与整条龙/单跑互斥；急停按钮自动覆盖 `vars(page)` 里的 runner）。
  **任务「单跑」（日常页点任务名/副本名）忽略「日常」页的整链设置（user 拍板 2026-09-24）**：
  只按该任务自己的流程走——不套整体时间上限/定时、绝不「跑完关机」、不按「跑完解散」收尾解散、
  不触发「自动整理背包」；「已组队」是会话条件属共享组队设置、不动。实现= `DailyPage._single_build_cfg` 内存覆盖。
- **组队是共享能力、单独可一键触发**：握手在 `core/teaming.TeamFormation`；通用页有「选队长 + 一键组队」
  （跑 `DungeonTask`），角色参数存共享 `tasks.teaming`。副本/抓鬼各自单跑时都先自动组队；**但「日常」一趟里的多个多人步只组一次**——
  队伍要活到最后一个多人步跑完才解散（`daily._disband_after_multi`），故 `daily` 用 `_team_ready` 记「本趟已组好队」，
  经 `_collective_run` 临时把 `tasks.teaming.skip_team` 置 True 让后续多人步（副本 2..N、抓鬼）复用（`finally` 恢复、只改内存不落盘），
  任务侧用 `self._team_formed` 回报队伍可用。手动单跑各任务不受影响。
- **公共区域标定（`tasks.shared`）**：活动列表区 `activity_list` / 背包列表区 `bag_list` 跨任务画面相同、共用一套标定，
  统一存共享命名空间 `tasks.shared.regions`，**只在「通用」页「标定（公共区域）」标定一次**（各任务 CALIBRATION 不再列出这两项）；
  `core/config.py task_config()` 读取时自动叠加进各任务 regions（运行时与就绪判定都吃到；新任务直接用 `tc["regions"]` 读即可，
  别各标一份）。共享键集合在 `core/config.SHARED_REGION_KEYS`；各任务「就绪判定」还要查的共享键在 `core/config.TASK_SHARED_REQ`。
  **共享模板 `battle_flag` 战斗标识、`clock` 小闹钟(寻路) 也统一在此标定**（存 `tasks.shared.templates`、进位 `core/config.SHARED_TPL_KEYS`，
  `task_config` 同时把它们叠加进各任务 templates、任务直接用 `tc["templates"]["battle_flag"]`/`["clock"]` 读，各任务不再自带这两项标定、
  preflight 缺它时提示去「通用」页框）。必标关系在 `TASK_SHARED_TPL_REQ`：战斗标识=运镖/宝图，小闹钟=副本（跑到目标寻路/每轮收尾）与抓鬼
  （点任务条目寻路）；抓鬼**另读** `battle_flag` 判「是否已进战」，好按 `loop.battle_timeout_sec`（新语义=**每场战斗之间**的等待，默认 10s、
  旧值 2400 由 `_migrate_zhuagui_battle_gap` 一次性压回）超时补点小闹钟、连补 `battle_recover_max` 次进不了战即中止，另有 `round_timeout_sec`
  整轮兜底——但 `battle_flag` 对抓鬼是**可选**的（不在必标列表里，未标=不判进战、不补点，退回只等「是否继续」的旧行为）；
  `core/config` 里有一次性的 `_migrate_shared_clock`，把用户旧位置（`tasks.dungeon.templates.clock`/`tasks.zhuagui.templates.gg_nav`，
  旧抓鬼侧另留 `gg_nav` 兼容读取兜底）搬到 shared、不用重标。
  再次强调运行时只读 shared 标注副本：标定对话框**写**共享键走原始 `tasks.shared`、**写本任务**时剥离叠加值（`_save` 内
  `EXCLUSIVE_SHARED_REGIONS` + `SHARED_TPL_KEYS`），两者读库均经 `task_config` 合并。
  **拓印临摹资产已不在共享里**：标定迁到「工具」页「拓印」（存 `tasks.tuoying`，见上「拓印」条），各任务不重复列出、不参与就绪。
  ⚠ 标定对话框写共享键走 `tasks.shared`、绝不回写任务自身命名空间（`ui/calibrate_dialog.py` 的 `_target_regions`/`_save` 只看 `EXCLUSIVE_SHARED_REGIONS`）；先补共享标定时让旧任务自带值兜底。
  **弹窗守卫**：可选共享模板 `popup_close`（活动/公告弹窗右上角「×」，同在此标；不进 `SHARED_TPL_KEYS`、不叠加、不参与就绪）。
  守卫挂在 `base.Task._interruptible_sleep`（节流 `popup_guard.interval_sec`，顶层配置），对**当前前台**窗口截图找 `×`→拟人点掉；
  同一弹窗连点 `max_clicks` 次不消失 → Esc 兜底（`esc_fallback`+`max_esc` 封顶）→ 告警放弃。多开绝不在后台号点（`window.is_foreground` 门控）；
  拓印阶段 `_trace_active` 置位挂起守卫（`dungeon_base._handle_tuoying` 包了 try/finally）。
  **活动界面就绪确认（user 2026-09-26 拍板）**：可选共享模板 `activity_ui_flag`（活动面板打开后独有的元素，同在公共标定、不叠加不参与就绪）。
  各任务发完 `open_activity` 快捷键后走 `base.Task._wait_activity_ui`（轮询整窗找该模板，默认 3s）——**确认活动界面真弹好了才开始滚轮翻卡片**，
  避免界面延迟/被公告弹窗挡时在空画面上白翻。**未标定/超时→退回原固定 sleep(0.6s)**，故不标也能照常跑；阈值/等待秒数 `loop.activity_ui_wait_sec`。
- **「队长ID 库」（`ui/leader_gallery.py` + 纯函数 `core/leader_history.py`）非显而易见的约束**：
  **激活图路径永远是 `templates/tm_leader_id.png`**（teaming 与 calibrate 都写死读它），切换当前队长 = 把选中历史图
  **字节复制覆盖**该文件、**绝不改 config 路径串**，故 `TeamFormation` 零改、零回归。⚠ 就绪度判定只看
  `templates.leader_id` 是否**非空**（不看文件存在），故历史空时 `leader_history` 把它置 None。画廊/槽位/广播刷新等
  细节见 memory `leader-id-gallery`。

> 改动历史不在本文件——查 `git log`（commit 正文写了改了什么、为什么）和 memory 目录
> （treasure-map-task / escort-task / secret-realm-task / daily-chain-task / teaming-and-dungeon-task 等）。

## 怎么跑
- 用户侧：双击 `启动.bat`，界面里「标定/加装备」→ 看 captures/ 或跑一遍 → 就绪后直接点「运行」实跑。
- 停止：界面「停止」按钮，或**急停热键**（默认 Ctrl+Alt+F12，设置里可改），或**鼠标甩到屏幕角**
  （默认右上角，设置 `failsafe_corner` 可改/可关）。
  ⚠ 甩角急停由 **GUI 轮询真实光标位置**实现（`app._poll_hotkey` + `_in_failsafe_corner`），不是 pyautogui
  FAILSAFE——默认 sendinput 后端走 SendInput 底层注入、**根本不经过 pyautogui、没有内置 FAILSAFE**，故必须
  GUI 自己兜。三种急停都走 `app.stop_all_tasks()`（停所有页面所有 TaskRunner），只能停在两次原子动作之间，单次拟人化移动/点击会先走完。
- ⚠ 改动多止于代码层验证（py_compile / example.json 合法 / 纯逻辑模拟）；识别/点击/多开轮转的真实手感
  需用户开 2~3 个号自测。报 bug 时按「哪个任务的哪一步点歪/没识别」定位。

## 环境备注
- Windows 11，PowerShell 主 shell；也有 Bash 工具。
- git 推送走 Clash 代理端口 7897（见全局 CLAUDE.md），本项目已是 git 仓库。
- 日志除全局面板外逐行同步写 `logs/日志-YYYYMMDD_HHMMSS.log`（每次启动一个，崩溃/强杀不丢；
  `config.log_to_file` 可关、`log_keep_files` 自动清理保留 N 份，设置页「基础」区可调）。
- 记忆目录有更详细背景：game-mhxy-shikong / sniper-design-decisions / project-architecture。
- **调试/预览产物一律写仓库内默认位置（`captures_debug/…`）或临时目录 `C:\Users\hq\AppData\Local\Temp\opencode`**，
  命令行工具（如 `trace_preview`）**不要传 `--out` 指到桌面**（user 2026-10-18 拍板：不碰桌面）。
