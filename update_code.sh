#!/bin/bash
# 健壮版脚本：保持 fork 仓库与上游同步，并更新自己的分支
# 新增：前置检查未提交修改、stash 暂存、冲突友好提示、清理残留锁文件

set -euo pipefail  # 严格模式：未定义变量报错、管道出错则退出

# ====================== 配置项（可根据需要修改）======================
UPSTREAM_BRANCH="main"          # 上游主分支
MY_BRANCH=${1:-"dev_0905"}      # 默认开发分支（支持参数传入）
STASH_MSG="auto-stash: 同步上游前暂存的本地修改"  # stash 备注

# ====================== 工具函数 =======================
# 检查是否有未提交的修改
check_uncommitted() {
    if ! git diff --quiet || ! git diff --cached --quiet; then
        echo "⚠️  检测到工作区有未提交的修改，将自动暂存..."
        # 暂存所有未提交修改（包括已暂存/未暂存）
        git stash push -u -m "$STASH_MSG" || {
            echo "!!! 暂存修改失败，请手动处理未提交的修改后重试！"
            exit 1
        }
        STASHED=true
    else
        STASHED=false
    fi
}

# 恢复暂存的修改（如果有）
restore_stash() {
    if [ "$STASHED" = true ]; then
        echo ">>> 恢复之前暂存的本地修改..."
        # 恢复 stash，若有冲突则提示手动解决
        if ! git stash pop; then
            echo "⚠️  恢复暂存修改时出现冲突，请手动解决后执行："
            echo "    git add <冲突文件> && git commit"
            exit 1
        fi
    fi
}

# 清理 Git 残留锁文件（避免 index.lock 导致操作失败）
clean_git_lock() {
    if [ -f .git/index.lock ]; then
        echo "⚠️  检测到 Git 锁文件，自动清理..."
        rm -f .git/index.lock
    fi
}

# ====================== 主逻辑 =======================
echo "===== 开始同步上游仓库并更新分支 ====="
clean_git_lock  # 前置清理锁文件

# 步骤1：前置检查并暂存未提交修改（核心：解决分支切换覆盖问题）
check_uncommitted

# 步骤2：拉取上游最新代码
echo ">>> 从上游仓库拉取最新代码..."
git fetch upstream || {
    echo "!!! 拉取上游代码失败，请检查网络或 upstream 配置！"
    exit 1
}

# 步骤3：切换并同步 main 分支
echo ">>> 切换到 main 分支并同步 upstream..."
# 强制切换到 main（若有未解决冲突，先 abort）
if ! git checkout main; then
    echo "⚠️  切换 main 分支失败，尝试取消未完成的合并/变基..."
    git merge --abort 2>/dev/null || true
    git rebase --abort 2>/dev/null || true
    git checkout main || {
        echo "!!! 切换 main 分支失败，请手动处理后重试！"
        exit 1
    }
fi

# 检查 main 是否需要更新
LOCAL_HASH=$(git rev-parse main)
UPSTREAM_HASH=$(git rev-parse upstream/$UPSTREAM_BRANCH)
if [ "$LOCAL_HASH" = "$UPSTREAM_HASH" ]; then
    echo ">>> main 已经是最新的，无需更新。"
else
    echo ">>> 更新 main 分支（rebase 模式）..."
    if ! git pull --rebase upstream $UPSTREAM_BRANCH; then
        echo "!!! rebase 出现冲突，请手动解决后执行："
        echo "    git add <冲突文件> && git rebase --continue"
        echo "    （解决后重新执行本脚本）"
        restore_stash  # 恢复暂存的修改
        exit 1
    fi

    echo ">>> 推送最新的 main 到自己仓库..."
    git push origin main || {
        echo "!!! 推送 main 到远程失败，请检查权限或网络！"
        restore_stash
        exit 1
    }
fi

# 步骤4：切换并合并到开发分支
echo ">>> 切换到开发分支 $MY_BRANCH..."
if ! git checkout $MY_BRANCH; then
    echo "⚠️  切换 $MY_BRANCH 分支失败，尝试取消未完成的合并/变基..."
    git merge --abort 2>/dev/null || true
    git rebase --abort 2>/dev/null || true
    git checkout $MY_BRANCH || {
        echo "!!! 切换 $MY_BRANCH 分支失败，请手动处理后重试！"
        restore_stash
        exit 1
    }
fi

# 检查是否需要合并 main
if git merge-base --is-ancestor main $MY_BRANCH; then
    echo ">>> $MY_BRANCH 已经包含 main 的最新提交，无需合并。"
else
    echo ">>> 将 main 合并到 $MY_BRANCH..."
    if ! git merge main --no-edit; then  # --no-edit 自动生成合并提交信息
        echo "!!! 合并出现冲突，请手动解决后执行："
        echo "    git add <冲突文件> && git commit"
        restore_stash  # 恢复暂存的修改
        exit 1
    fi
    echo ">>> $MY_BRANCH 已成功合并 main 的最新代码。"
    
    # 可选：推送合并后的开发分支到远程（根据需要注释/取消注释）
    # echo ">>> 推送 $MY_BRANCH 到远程仓库..."
    # git push origin $MY_BRANCH
fi

# 步骤5：恢复暂存的本地修改（如果有）
restore_stash

echo "===== 同步完成！====="
exit 0