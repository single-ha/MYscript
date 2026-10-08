# -*- coding: utf-8 -*-
"""
账号库（登录游戏用）：最多标定 10 个账号的卡片/头像图，选其中最多 5 个进登录队列。
纯逻辑、零 GUI 依赖，可单测；UI 薄壳见 ui/account_gallery.py（仿队长ID 库 ui/leader_gallery.py）。

与队长ID 库（core/leader_history.py）同样的「固定物理槽 + config 记列表」思路，但语义不同：
  · 队长ID 库是「当前1 + 历史3」的**单选激活**（切谁就覆盖激活图 tm_leader_id.png）；
  · 账号库是**多选**（最多 5 个 = 登录队列），每个账号一张独立的图、谁也不覆盖谁。
- 10 个固定物理槽 templates/tm_login_account0..9.png（纯 ASCII 名，避开中文路径坑）。
  槽位号 = 账号的稳定身份：删除某账号只腾出它的槽，下次标定复用该槽（不会串号）。
- 框选暂存：标定时 calibrate_template_direct 写到 templates/tm_login_account_pick.png，
  add_account 立刻把它字节复制进空槽（复制成功才删暂存图）——与队长ID 库的
  「calibrate 写激活图 → push 复制进槽」是同一套约定。
- config（tasks.login 下，只碰这两个键，不整块回写）：
    account_library = [{slot, name}, ...]     账号库，slot 0..9，name 用户可改（默认 账号N）
    accounts        = [slot, ...]             登录队列（**按点击顺序**、最多 5 个；空=没选）
  读写一律走【原始】tasks.login 命名空间（不经 task_config），故绝不会把「通用」页共享标定
  （活动/背包列表区、战斗标识/小闹钟）顺手抄进本任务——那正是 CLAUDE.md 记的污染坑。
"""

import os

from . import config as cfg_mod
from . import vision
from .config import LOGIN_ACCOUNT_SLOTS, LOGIN_MAX_ACCOUNTS

TASK = "login"
PICK_REL = "templates/tm_login_account_pick.png"   # 框选暂存图（标定写这里，库里立刻复制走）
SLOT_REL = "templates/tm_login_account{}.png"      # 账号库物理槽 0..9
MAX_SLOTS = LOGIN_ACCOUNT_SLOTS                    # 账号库容量（10）
MAX_PICK = LOGIN_MAX_ACCOUNTS                      # 登录队列上限（5）


# ----------------------------------------------------------------------
# 小工具
# ----------------------------------------------------------------------
def slot_rel(slot):
    """账号槽位号 -> 模板相对路径。"""
    return SLOT_REL.format(int(slot))


def default_name(slot):
    return "账号%d" % (int(slot) + 1)


def _abs(rel):
    return rel if os.path.isabs(rel) else str(cfg_mod.PROJECT_ROOT / rel)


def _ns(cfg):
    """取**原始** tasks.login 命名空间 dict（不经 task_config，避免共享标定被顺手写进来）。"""
    return (cfg.get("tasks", {}) or {}).setdefault(TASK, {})


def _commit(cfg, ns):
    cfg_mod.set_task_config(cfg, TASK, ns)
    cfg_mod.save_config(cfg)


def _copy(src_rel, dst_rel):
    img = vision.load_template(src_rel)
    if img is None:
        return False
    return bool(vision.save_image(dst_rel, img))


def _remove(rel):
    try:
        p = _abs(rel)
        if os.path.exists(p):
            os.remove(p)
    except OSError:
        pass


# ----------------------------------------------------------------------
# 读
# ----------------------------------------------------------------------
def get_library(cfg):
    """账号库列表 [{slot, name}, ...]，按 slot 升序（= 账号1、账号2… 的自然顺序）。"""
    ns = _ns(cfg)
    items = []
    for it in (ns.get("account_library") or []):
        try:
            slot = int(it.get("slot"))
        except (TypeError, ValueError):
            continue
        if not 0 <= slot < MAX_SLOTS:
            continue
        name = (it.get("name") or "").strip() or default_name(slot)
        items.append({"slot": slot, "name": name})
    items.sort(key=lambda x: x["slot"])
    return items


def get_names(cfg):
    """{slot: name} 便于渲染。"""
    return {it["slot"]: it["name"] for it in get_library(cfg)}


def slot_exists(slot):
    """该槽的图是否真的在磁盘上（config 有记录但图丢了也要如实反映）。"""
    return os.path.exists(_abs(slot_rel(slot)))


def free_slots(cfg):
    used = {it["slot"] for it in get_library(cfg)}
    return [s for s in range(MAX_SLOTS) if s not in used]


def get_selection(cfg):
    """登录队列 = 有序槽位号列表（按点击顺序，最多 MAX_PICK 个）。
    自动剔除库里已删/越界的项，故删账号不会留下悬空选择。"""
    ns = _ns(cfg)
    valid = {it["slot"] for it in get_library(cfg)}
    out = []
    for v in (ns.get("accounts") or []):
        try:
            s = int(v)
        except (TypeError, ValueError):
            continue
        if s in valid and s not in out:
            out.append(s)
    return out[:MAX_PICK]


def is_full(cfg):
    return len(get_library(cfg)) >= MAX_SLOTS


# ----------------------------------------------------------------------
# 写
# ----------------------------------------------------------------------
def add_account(cfg, name=None):
    """把刚标定好的暂存图（PICK_REL）收进库里第一个空槽。返回 (ok, msg)。

    库满（10 个）时拒绝——用户拍板「最多标定 10 个账号」，宁可明确拒绝也不偷偷挤掉旧的。"""
    lib = get_library(cfg)
    free = free_slots(cfg)
    if not free:
        return False, f"账号库已满（最多 {MAX_SLOTS} 个），请先删除不用的账号再标定。"
    if not os.path.exists(_abs(PICK_REL)):
        return False, "没拿到刚框选的账号图（标定未成功？）。"
    slot = free[0]
    if not _copy(PICK_REL, slot_rel(slot)):
        return False, "保存账号图失败。"
    _remove(PICK_REL)                      # 暂存图已进槽，删掉免得留一份容易混淆的重复图
    lib.append({"slot": slot, "name": (name or "").strip() or default_name(slot)})
    _commit(cfg, _write_back(cfg, lib))
    return True, f"已加入账号库：{lib[-1]['name']}（共 {len(lib)}/{MAX_SLOTS} 个）"


def _write_back(cfg, lib):
    """把库列表写回命名空间（并顺带把队列里已不存在的槽剔掉）。"""
    ns = _ns(cfg)
    ns["account_library"] = sorted(lib, key=lambda x: x["slot"])
    valid = {it["slot"] for it in ns["account_library"]}
    ns["accounts"] = [s for s in (ns.get("accounts") or []) if s in valid][:MAX_PICK]
    return ns


def rename(cfg, slot, name):
    """改账号名（空名=回默认「账号N」）。"""
    lib = get_library(cfg)
    hit = False
    for it in lib:
        if it["slot"] == int(slot):
            it["name"] = (name or "").strip() or default_name(it["slot"])
            hit = True
            break
    if hit:
        _commit(cfg, _write_back(cfg, lib))
    return hit


def delete_account(cfg, slot):
    """删除账号：删槽文件 + 移出库 + 顺带从登录队列里摘掉。返回 (ok, msg)。"""
    slot = int(slot)
    lib = get_library(cfg)
    if not any(it["slot"] == slot for it in lib):
        return False, "这个账号不在库里。"
    lib = [it for it in lib if it["slot"] != slot]
    _remove(slot_rel(slot))
    _commit(cfg, _write_back(cfg, lib))
    return True, "已删除该账号。"


def _recal(cfg, src_rel, dst_rel):
    """用库里已有的图覆盖标定（重标同一账号，不换槽、不换名）。"""
    if not _copy(src_rel, dst_rel):
        return False
    return True


def recalibrate(cfg, slot):
    """重标某个账号：拿新框的图覆盖它的槽（身份/名字/队列位置都不变）。"""
    if not os.path.exists(_abs(PICK_REL)):
        return False, "没拿到刚框选的账号图（标定未成功？）。"
    if not _recal(cfg, PICK_REL, slot_rel(int(slot))):
        return False, "保存账号图失败。"
    _remove(PICK_REL)
    return True, "已更新该账号的图。"


def set_selected(cfg, slots):
    """直接设登录队列（有序、超上限截断、剔除库里没有的槽）。返回实际存下的队列。"""
    ns = _ns(cfg)
    valid = {it["slot"] for it in get_library(cfg)}
    out = []
    for v in (slots or []):
        try:
            s = int(v)
        except (TypeError, ValueError):
            continue
        if s in valid and s not in out:
            out.append(s)
    ns["accounts"] = out[:MAX_PICK]
    _commit(cfg, ns)
    return ns["accounts"]


def toggle_selected(cfg, slot):
    """点一下切「这个号要不要进登录队列」：已选则移出，未选则按点击顺序追加到末尾。
    返回 (ok, msg, queue)。已满 MAX_PICK 时拒绝（明确提示，不静默丢弃）。"""
    slot = int(slot)
    queue = get_selection(cfg)
    if slot in queue:
        queue.remove(slot)
        ok, msg = True, "已从登录队列移出。"
    else:
        if len(queue) >= MAX_PICK:
            return False, f"最多选 {MAX_PICK} 个号登录（已选满）。先移掉一个再选。", queue
        queue.append(slot)
        ok, msg = True, "已加入登录队列。"
    return ok, msg, set_selected(cfg, queue)


def clear_selection(cfg):
    """清空登录队列（库里账号留着，只是不登录）。"""
    ns = _ns(cfg)
    ns["accounts"] = []
    _commit(cfg, ns)
    return []


def status_text(cfg):
    """一行就绪摘要（账号库配置卡 / 登录区都用这句，别各写一套）。"""
    lib = get_library(cfg)
    sel = get_selection(cfg)
    if not lib:
        return "账号库 空（最多可标定 %d 个）" % MAX_SLOTS, False
    text = "账号库 %d/%d 个　已选 %d/%d 个" % (len(lib), MAX_SLOTS, len(sel), MAX_PICK)
    return text, bool(sel)
