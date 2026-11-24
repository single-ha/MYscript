# -*- coding: utf-8 -*-
"""检查更新：在【源码（git 仓库）环境】用 git 拉取最新代码。

设计约束（user 2026-11-24 拍板）：
  · 只支持源码环境（项目目录带 .git、机器装了 git）。PyInstaller 打包的 exe 交付目录
    只有 exe+config.json+templates、没有 .git，用户机器也未必装 git —— 检测到这种情况
    返回 UNSUPPORTED 由界面提示「请重新下载新版」，绝不抛异常崩溃。
  · 更新前【先 fetch 再检测】：本地有未提交改动、或本地与远端分歧（无法快进）→ 中止并
    提示原因，绝不 reset/强制覆盖。只有工作区干净且能快进时才 merge --ff-only。
  · 本模块只负责拉取，不自动重启进程（避免中断正在跑的任务）；成功后由界面提示手动重启。

所有 subprocess 都是阻塞调用，UI 必须放到后台线程执行（见 settings 页 _check_update），
不能在 Tk 主线程跑，否则 fetch 网络等待会把界面卡死。
"""

import os
import sys
import shutil
import subprocess

from . import config

# 结果状态常量（界面据此选提示颜色）
UNSUPPORTED = "unsupported"   # 非源码环境：无 .git 或找不到 git 程序
UP_TO_DATE = "up_to_date"     # 已是最新，无需更新
UPDATED = "updated"           # 成功快进拉取了新提交
DIRTY = "dirty"               # 本地有未提交改动，为避免覆盖而中止
DIVERGED = "diverged"         # 本地有自己的提交、与远端分歧，无法快进而中止
FAILED = "failed"             # git 报错 / 超时 / 无上游等其它失败

# 网络与 git 操作的总超时秒数（fetch 受网速影响，给宽松些；其余本地操作很快）
FETCH_TIMEOUT = 60.0
LOCAL_TIMEOUT = 15.0
# 汇报里最多列几条新提交（太长会撑爆状态 Label）
MAX_COMMITS_IN_DETAIL = 5

# Windows 下隐藏 subprocess 弹出的黑色控制台窗口（程序本身用 pythonw 无窗口跑，不设会闪窗）
_CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class UpdateResult:
    """一次检查更新的结果。status 取本模块顶部常量；message 给用户看的结论，
    detail 是可展开的细节（新提交/改动文件数/报错原文），need_restart=True 时界面要提示手动重启。"""

    def __init__(self, status, message, detail="", need_restart=False):
        self.status = status
        self.message = message
        self.detail = detail
        self.need_restart = need_restart

    def __repr__(self):
        return f"UpdateResult(status={self.status!r}, message={self.message!r})"


def _run_git(git_exe, args, repo_root, timeout=LOCAL_TIMEOUT):
    """跑一条 git 命令，返回 (returncode, stdout, stderr)，输出统一 UTF-8 容错解码。
    core.quotepath=false 让中文文件名正常显示；GIT_TERMINAL_PROMPT=0 杜绝凭据弹窗卡死。"""
    cmd = [git_exe, "-c", "core.quotepath=false"] + args
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.run(
        cmd, cwd=str(repo_root), capture_output=True,
        encoding="utf-8", errors="replace", timeout=timeout,
        env=env, creationflags=_CREATE_NO_WINDOW)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _find_git():
    """定位 git 可执行文件并验证可用；找不到返回 None。"""
    git_exe = shutil.which("git")
    if not git_exe:
        # PATH 里没有时仍给一次机会：某些环境只装了便携 git 但没配 PATH，直接用名字让系统解析
        git_exe = "git"
    try:
        rc, _, _ = _run_git(git_exe, ["--version"], os.getcwd(), timeout=LOCAL_TIMEOUT)
        return git_exe if rc == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def check_for_update(repo_root=None):
    """主入口：检查并在安全前提下快进更新。全程不修改用户未提交的任何东西。

    返回 UpdateResult，永不向外抛 git/文件系统异常（都转成 FAILED/UNSUPPORTED）。
    repo_root 默认取 config.PROJECT_ROOT（源码环境=项目根，frozen=exe 同级目录）。
    """
    root = config.PROJECT_ROOT if repo_root is None else repo_root
    git_dir = os.path.join(str(root), ".git")

    # 1) 环境校验：必须是 git 仓库（exe 分发目录没有 .git）
    if not os.path.isdir(git_dir) and not os.path.isfile(git_dir):
        return UpdateResult(
            UNSUPPORTED,
            "当前不是源码（git 仓库）环境，无法在线更新。",
            "未找到 .git 目录。若使用的是打包版 exe，请重新下载最新版本替换。")
    git_exe = _find_git()
    if not git_exe:
        return UpdateResult(
            UNSUPPORTED,
            "未检测到 git 程序，无法在线更新。",
            "请先安装 git 并加入 PATH，或在源码目录手动执行 git pull。")

    try:
        # 2) 当前分支及其上游（不写死 main，兼容以后改分支名）
        rc, branch, err = _run_git(git_exe, ["rev-parse", "--abbrev-ref", "HEAD"], root)
        if rc != 0 or not branch or branch == "HEAD":
            return UpdateResult(FAILED, "无法确定当前 git 分支，更新中止。", err)
        rc, upstream, err = _run_git(
            git_exe, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
        if rc != 0 or not upstream:
            return UpdateResult(
                FAILED, f"分支「{branch}」没有配置上游远程，无法拉取更新。",
                err or "可用 git branch -u origin/<分支> 绑定上游后重试。")

        # 3) 先 fetch 把远端最新引用拉到本地（只动 .git，不碰工作区文件）
        try:
            rc, out, err = _run_git(git_exe, ["fetch", "--prune"], root, timeout=FETCH_TIMEOUT)
        except subprocess.TimeoutExpired:
            return UpdateResult(FAILED, "git fetch 超时，网络可能不通，更新中止。")
        if rc != 0:
            return UpdateResult(FAILED, "git fetch 失败，更新中止。", err or out)

        # 4) 比较本地 HEAD 与上游
        rc, head, _ = _run_git(git_exe, ["rev-parse", "HEAD"], root)
        rc2, remote, err = _run_git(git_exe, ["rev-parse", upstream], root)
        if rc != 0 or rc2 != 0 or not head or not remote:
            return UpdateResult(FAILED, "无法读取本地/远端提交版本，更新中止。", err)
        if head == remote:
            return UpdateResult(UP_TO_DATE, "当前已是最新版本，无需更新。")

        # 5) 确有新提交。先看工作区是否干净——有未提交改动一律中止，绝不覆盖
        rc, porcelain, err = _run_git(git_exe, ["status", "--porcelain"], root)
        if rc != 0:
            return UpdateResult(FAILED, "无法读取本地工作区状态，更新中止。", err)
        if porcelain:
            changed = [ln[3:] for ln in porcelain.splitlines() if ln.strip()]
            preview = "、".join(changed[:5]) + ("…" if len(changed) > 5 else "")
            return UpdateResult(
                DIRTY,
                "本地有未提交的改动，为避免被覆盖已中止更新。",
                f"涉及文件：{preview or '若干'}。请先提交或 stash 这些改动后再检查更新。")

        # 6) 能否快进：HEAD 必须是上游的祖先（本地没有自己的新提交）
        rc, _, _ = _run_git(git_exe, ["merge-base", "--is-ancestor", "HEAD", upstream], root)
        if rc != 0:
            return UpdateResult(
                DIVERGED,
                "本地提交与远端已分歧，无法快进更新，已中止。",
                "本地存在远端没有的提交。请手动 rebase/合并后再更新，脚本不做强制覆盖。")

        # 7) 拉取前先记录将要进来的提交与改动文件，用于成功后汇报
        rc, log_out, _ = _run_git(
            git_exe, ["log", "--oneline", f"HEAD..{upstream}"], root)
        commits = [ln for ln in log_out.splitlines() if ln.strip()] if rc == 0 else []
        rc, diff_out, _ = _run_git(
            git_exe, ["diff", "--name-only", f"HEAD..{upstream}"], root)
        files = [ln for ln in diff_out.splitlines() if ln.strip()] if rc == 0 else []

        # 8) 干净且可快进 → 快进合并（fetch 已完成，merge --ff-only 等价安全版 pull，不再走网络）
        rc, out, err = _run_git(git_exe, ["merge", "--ff-only", upstream], root)
        if rc != 0:
            return UpdateResult(FAILED, "快进合并失败，更新中止（本地文件未被改动）。", err or out)
        rc, new_head, _ = _run_git(git_exe, ["rev-parse", "HEAD"], root)
        if new_head != remote:
            return UpdateResult(FAILED, "合并后版本校验不一致，更新中止。", err or out)

        detail_lines = []
        if commits:
            detail_lines.append("新增提交：")
            detail_lines.extend(f"  {c}" for c in commits[:MAX_COMMITS_IN_DETAIL])
            if len(commits) > MAX_COMMITS_IN_DETAIL:
                detail_lines.append(f"  …另有 {len(commits) - MAX_COMMITS_IN_DETAIL} 条未列出")
        if files:
            detail_lines.append(f"改动文件 {len(files)} 个。")
        detail_lines.append("请手动关闭并重新打开程序，新版本才会生效。")
        return UpdateResult(
            UPDATED,
            f"更新完成：快进拉取了 {len(commits) or '若干'} 个新提交。",
            "\n".join(detail_lines), need_restart=True)

    except subprocess.TimeoutExpired:
        return UpdateResult(FAILED, "git 操作超时，更新中止。")
    except OSError as e:
        return UpdateResult(FAILED, f"调用 git 失败，更新中止：{e}")
