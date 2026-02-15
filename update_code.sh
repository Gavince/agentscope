#!/usr/bin/env bash
# sync-and-protect.sh   (2026 推荐版)
# 用途：
#   1. 保持 main 与 upstream 同步（rebase + safe push）
#   2. 把 main 的最新代码带到开发分支（默认 rebase）
#   3. 自动 stash 保护未提交的学习/实验内容
#   4. 冲突时提供详细指引并等待用户解决（兼容 GitLens）
#   5. 完成后推送开发分支到 origin（使用 --force-with-lease）

set -euo pipefail
IFS=$'\n\t'

# ─── 配置（可通过环境变量覆盖） ──────────────────────────────────────
: "${UPSTREAM_REMOTE:=upstream}"
: "${UPSTREAM_BRANCH:=main}"
: "${DEFAULT_DEV_BRANCH:=dev_0905}"

PUSH=true
USE_REBASE=true   # 默认用 rebase，--merge 可切换

# ─── 参数解析 ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)   PUSH=false; shift ;;
        --merge)     USE_REBASE=false; shift ;;
        --help|-h)
            echo "用法: $0 [dev-branch] [--no-push] [--merge]"
            echo "  --no-push    不推送任何内容到 origin"
            echo "  --merge      对开发分支使用 merge 而不是 rebase"
            exit 0
            ;;
        -*)
            echo "未知选项: $1" >&2
            exit 2
            ;;
        *)
            DEFAULT_DEV_BRANCH="$1"
            shift
            ;;
    esac
done

DEV_BRANCH="$DEFAULT_DEV_BRANCH"

# ─── 前置检查 ──────────────────────────────────────────────────────────
if ! git remote get-url "$UPSTREAM_REMOTE" &>/dev/null; then
    echo "× 未找到 upstream remote" >&2
    echo "   请先执行： git remote add upstream <上游URL>" >&2
    exit 1
fi

if ! git show-ref --verify --quiet "refs/heads/$DEV_BRANCH"; then
    echo "× 本地没有分支 $DEV_BRANCH" >&2
    git branch --list >&2
    exit 1
fi

echo "┌──────────────────────────────────────┐"
echo "│ 同步 upstream → main → $DEV_BRANCH   │"
echo "└──────────────────────────────────────┘"
echo "  开发分支： $DEV_BRANCH"
echo "  推送保护： $( $PUSH && echo '开启' || echo '关闭 (--no-push)' )"
echo "  更新方式： $( $USE_REBASE && echo 'rebase (推荐学习场景)' || echo 'merge' )"

# ─── 1. 保护当前工作区 ────────────────────────────────────────────────
if ! git diff --quiet --exit-code || ! git diff --cached --quiet; then
    echo "→ 检测到未提交更改，自动 stash 保护..."
    STASH_MSG="WIP-auto-$(date '+%Y%m%d-%H%M') before sync"
    git stash push -m "$STASH_MSG" || { echo "stash 失败"; exit 1; }
    STASHED=true
else
    STASHED=false
fi

# ─── 2. 更新 main ──────────────────────────────────────────────────────
echo ""
echo "→ 更新 main 分支..."
git fetch --prune "$UPSTREAM_REMOTE"
git fetch --prune origin

if git merge-base --is-ancestor "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}" main &>/dev/null; then
    echo "  main 已是最新的"
else
    git checkout main
    echo "  rebase upstream/$UPSTREAM_BRANCH → main ..."
    if ! git rebase "${UPSTREAM_REMOTE}/${UPSTREAM_BRANCH}"; then
        echo "× main rebase 冲突！请手动解决："
        echo "  git rebase --continue   或   git rebase --abort"
        exit 1
    fi

    if $PUSH; then
        echo "→ 安全推送 main 到 origin..."
        git push --force-with-lease origin main || {
            echo "× push 失败（可能远程有新提交）"
            echo "  建议： git pull --rebase origin main 后再试"
            exit 1
        }
    else
        echo "  (--no-push，跳过 main push)"
    fi
fi

# ─── 3. 更新开发分支 ──────────────────────────────────────────────────
echo ""
echo "→ 更新 $DEV_BRANCH ..."
git checkout "$DEV_BRANCH"

if git merge-base --is-ancestor main "$DEV_BRANCH" &>/dev/null; then
    echo "  $DEV_BRANCH 已包含 main 最新内容"
else
    if $USE_REBASE; then
        echo "  rebase main → $DEV_BRANCH (使用 --autostash)..."
        if git rebase --autostash main; then
            echo "  rebase 成功"
        else
            echo ""
            echo "┌────────────────────────────────────────────────────────────┐"
            echo "│                  ⚠️  rebase 冲突！                        │"
            echo "│  Git 已自动 stash 你的未提交更改，请现在手动解决冲突      │"
            echo "└────────────────────────────────────────────────────────────┘"
            echo ""
            echo "推荐解决步骤（在 VSCode / GitLens 中操作最方便）："
            echo "  1. git status                  查看冲突文件列表"
            echo "  2. 打开冲突文件，解决标记（<<<<<<< HEAD ... ======= ... >>>>>>>）"
            echo "     • modify/delete 冲突： git add（保留改动）或 git rm（跟随上游删除）"
            echo "  3. git add <已解决的文件>"
            echo "  4. git rebase --continue       继续处理下一个 commit"
            echo "     （或 git rebase --skip       跳过当前 commit）"
            echo "     （或 git rebase --abort      完全放弃本次 rebase）"
            echo ""
            echo "解决所有冲突并执行 --continue 完成后，在此终端按 Enter 继续脚本..."
            read -p "（已完成 rebase --continue，按 Enter）"

            if git rev-parse --verify REBASE_HEAD >/dev/null 2>&1; then
                echo "× rebase 似乎还未结束，请检查 git status"
                echo "  如需放弃： git rebase --abort"
                exit 1
            fi

            echo "  rebase 已完成，autostash 自动恢复工作区"
        fi
    else
        # merge 方式（备用）
        echo "  merge main → $DEV_BRANCH ..."
        if ! git merge --no-edit main; then
            echo "× merge 冲突！请手动解决后 git commit"
            exit 1
        fi
    fi
fi

# ─── 4. 推送开发分支（保护你的学习进度） ─────────────────────────────
if $PUSH; then
    echo ""
    echo "→ 推送 $DEV_BRANCH 到 origin (--force-with-lease)..."
    if git push --force-with-lease origin "$DEV_BRANCH"; then
        echo "  ✓ 已推送 $DEV_BRANCH 到远程"
    else
        echo "× push 失败，可能远程有新提交"
        echo "  建议先： git pull --rebase origin $DEV_BRANCH 再重试"
    fi
else
    echo "  (--no-push，跳过 $DEV_BRANCH push)"
fi

# ─── 5. 恢复 stash（如果有） ──────────────────────────────────────────
if $STASHED; then
    echo ""
    echo "→ 恢复之前 stash 的工作区..."
    git stash pop || echo "  pop 可能有冲突，请手动 git stash apply 并检查"
fi

# ─── 结束 ──────────────────────────────────────────────────────────────
echo ""
echo "✓ 同步 & 保护完成"
echo "  当前分支： $(git branch --show-current)"
echo "  最近提交概览："
git log --oneline --graph -n 6