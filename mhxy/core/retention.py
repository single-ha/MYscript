# -*- coding: utf-8 -*-
"""
数据保留策略：按「修改时间」统一清理旧的日志 / 留证截图 / 录屏。

背景：旧实现是「按数量」清理（日志留最新 N 个、录屏留最近 N 次运行），并不是按天，
只要数量没超阈值，三天前（甚至更早）的文件会一直留着；截图目录更是完全没有清理。
现统一为单一规则——删除修改时间早于「最近 keep_days 天」的内容（user 拍板，默认 3 天）：

  · logs / captures / captures_debug：删过期**文件**，再清空因此变空的日期目录；
  · recordings：按整个 run_* 文件夹的修改时间删（保证一次运行的视频同生共死、不拆散），
    非 run_ 目录一律不碰。

全程 best-effort：单个文件/目录删除失败只跳过，绝不冒泡影响任务或启动。
keep_days<=0 = 关闭自动清理（保留全部）。
"""

import os
import time
import shutil

from .config import (LOGS_DIR, CAPTURES_DIR, CAPTURES_DEBUG_DIR, RECORDINGS_DIR)

# 录屏每次「运行」文件夹前缀（与 core.recorder._RUN_PREFIX 保持一致，这里不反向 import 以免循环依赖）
_RUN_PREFIX = "run_"


def _is_expired(fp, cutoff_ts):
    """文件修改时间早于 cutoff_ts 即过期。取不到 mtime 视为不过期（保守，不误删）。"""
    try:
        return os.path.getmtime(fp) < cutoff_ts
    except OSError:
        return False


def prune_files_older_than(root, keep_days, now=None):
    """删除 root 下修改时间早于 keep_days 天的文件，再清空变空的子目录。

    返回删除的文件数。root 不存在 / keep_days<=0 时直接返回 0。"""
    if not keep_days or keep_days <= 0:
        return 0
    try:
        if not root or not os.path.isdir(str(root)):
            return 0
    except OSError:
        return 0
    now_ts = time.time() if now is None else now
    cutoff = now_ts - float(keep_days) * 86400.0
    removed = 0

    # 1) 删过期文件（rglob 递归，覆盖 YYYY-MM-DD 日期子目录及各类子文件夹）
    for dirpath, _dirnames, filenames in os.walk(str(root), topdown=False):
        for name in filenames:
            fp = os.path.join(dirpath, name)
            if _is_expired(fp, cutoff):
                try:
                    os.remove(fp)
                    removed += 1
                except OSError:
                    pass

    # 2) 清空因此变空的目录（最深的先删，避免父目录因子目录非空删不掉）
    for dirpath, _dirnames, _filenames in os.walk(str(root), topdown=False):
        if os.path.normcase(os.path.abspath(dirpath)) == \
                os.path.normcase(os.path.abspath(str(root))):
            continue  # 根目录本身永远保留
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except OSError:
            pass
    return removed


def prune_recordings_older_than(root, keep_days, now=None):
    """按整个 run_* 文件夹的修改时间删除过期录屏运行，非 run_ 目录不碰。

    用 run 文件夹自身的 mtime 判断（一次运行期间会持续写帧、mtime 随内容更新），
    保证同一次运行的多个视频同生共死。返回删除的 run 文件夹数。"""
    if not keep_days or keep_days <= 0:
        return 0
    try:
        if not root or not os.path.isdir(str(root)):
            return 0
    except OSError:
        return 0
    now_ts = time.time() if now is None else now
    cutoff = now_ts - float(keep_days) * 86400.0
    removed = 0
    try:
        entries = list(os.scandir(str(root)))
    except OSError:
        return 0
    for entry in entries:
        try:
            if not entry.name.startswith(_RUN_PREFIX) or not entry.is_dir():
                continue
            if entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry.path, ignore_errors=True)
                if not os.path.exists(entry.path):
                    removed += 1
        except OSError:
            pass
    return removed


def prune_all(keep_days, now=None):
    """对日志/截图/调试图/录屏统一应用「保留最近 keep_days 天」。

    返回各目录删除量的字典，便于日志核对。keep_days<=0 时全部跳过（关闭自动清理）。"""
    stats = {}
    if not keep_days or keep_days <= 0:
        return stats
    # 运行期才读取模块全局常量（而非用 import 期固化的 _FLAT_DIRS），既便于测试 monkeypatch 重定向，
    # 也保证常量被其它模块替换后这里跟随生效。
    flat_roots = (LOGS_DIR, CAPTURES_DIR, CAPTURES_DEBUG_DIR)
    for root in flat_roots:
        try:
            stats[os.path.basename(str(root))] = prune_files_older_than(root, keep_days, now=now)
        except Exception:
            stats[os.path.basename(str(root))] = 0
    try:
        stats[os.path.basename(str(RECORDINGS_DIR))] = \
            prune_recordings_older_than(RECORDINGS_DIR, keep_days, now=now)
    except Exception:
        stats[os.path.basename(str(RECORDINGS_DIR))] = 0
    return stats
