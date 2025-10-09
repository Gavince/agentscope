#!/bin/bash
<<<<<<< HEAD
<<<<<<< HEAD
<<<<<<< HEAD
=======
# 改进版脚本：保持 fork 仓库与上游同步，并更新自己的分支
>>>>>>> 39c23917 (更新 update_code.sh 脚本，添加 fork 仓库同步功能)

set -euo pipefail  # 出错即退、未定义变量即错、管道失败即错

# 配置项
UPSTREAM_BRANCH="main"                 # 上游分支名
MY_BRANCH=${1:-"dev_0905"}             # 默认开发分支（可通过第1个参数传入）

# 日志与错误输出
info() { echo ">>> $*"; }
warn() { echo "!!! $*"; }
die() { warn "$*"; exit 1; }

# 前置检查
ensure_remote_exists() {
    local remote_name="$1"
    if ! git remote | grep -q "^${remote_name}$"; then
        die "未找到远端 '${remote_name}'。请先执行：git remote add ${remote_name} <url>"
    fi
}

<<<<<<< HEAD
echo ">>> 代码更新完成！"
=======
=======
>>>>>>> 619039f0 (修改)
# 改进版脚本：保持 fork 仓库与上游同步，并更新自己的分支

set -e  # 如果命令出错则退出

# 上游分支
UPSTREAM_BRANCH="main"
# 默认开发分支（可通过参数传入）
MY_BRANCH=${1:-"dev_0905"}

echo ">>> 从上游仓库拉取最新代码..."
git fetch upstream

echo ">>> 切换到 main 分支并同步 upstream..."
git checkout main

# 检查是否有新提交
LOCAL_HASH=$(git rev-parse main)
UPSTREAM_HASH=$(git rev-parse upstream/$UPSTREAM_BRANCH)

if [ "$LOCAL_HASH" = "$UPSTREAM_HASH" ]; then
    echo ">>> main 已经是最新的，无需更新。"
else
    echo ">>> 更新 main 分支..."
    if ! git pull --rebase upstream $UPSTREAM_BRANCH; then
        echo "!!! rebase 出现冲突，请手动解决后执行："
        echo "    git rebase --continue"
        exit 1
    fi

    echo ">>> 推送最新的 main 到自己仓库..."
    git push origin main
fi

echo ">>> 切换到开发分支 $MY_BRANCH..."
git checkout $MY_BRANCH

# 检查是否有 main 的更新需要合并
if git merge-base --is-ancestor main $MY_BRANCH; then
    echo ">>> $MY_BRANCH 已经包含 main 的最新提交，无需合并。"
else
    echo ">>> 将 main 合并到 $MY_BRANCH..."
    if ! git merge main; then
        echo "!!! 合并出现冲突，请手动解决后执行："
        echo "    git add <冲突文件> && git commit"
        exit 1
    fi
    echo ">>> $MY_BRANCH 已成功合并 main 的最新代码。"
fi

echo ">>> 更新完成！"

<<<<<<< HEAD
>>>>>>> 619039f0 (修改)
=======
ensure_branch_exists() {
    local branch_name="$1"
    if git rev-parse --verify "$branch_name" >/dev/null 2>&1; then
        return 0
    fi

    info "本地不存在分支 $branch_name，尝试从远端创建..."
    if git ls-remote --exit-code --heads origin "$branch_name" >/dev/null 2>&1; then
        git checkout -b "$branch_name" "origin/$branch_name"
    else
        info "远端也无该分支，将从 main 新建本地分支 $branch_name..."
        git checkout main
        git checkout -b "$branch_name" main
    fi
}

sync_main_with_upstream() {
    info "从上游仓库拉取最新代码..."
    git fetch upstream

    info "切换到 main 分支并同步 upstream/${UPSTREAM_BRANCH}..."
    git checkout main

    local local_hash upstream_hash
    local_hash=$(git rev-parse main)
    upstream_hash=$(git rev-parse "upstream/${UPSTREAM_BRANCH}")

    if [ "$local_hash" = "$upstream_hash" ]; then
        info "main 已经是最新的，无需更新。"
    else
        info "更新 main 分支（rebase 到 upstream/${UPSTREAM_BRANCH}）..."
        if ! git pull --rebase upstream "$UPSTREAM_BRANCH"; then
            warn "rebase 出现冲突，请手动解决后执行："
            warn "    git rebase --continue"
            exit 1
        fi

        info "推送最新的 main 到自己仓库..."
        git push origin main
    fi
}

merge_main_into_dev() {
    info "切换到开发分支 ${MY_BRANCH}..."
    ensure_branch_exists "$MY_BRANCH"
    git checkout "$MY_BRANCH"

    if git merge-base --is-ancestor main "$MY_BRANCH"; then
        info "${MY_BRANCH} 已经包含 main 的最新提交，无需合并。"
    else
        info "将 main 合并到 ${MY_BRANCH}..."
        if ! git merge main; then
            warn "合并出现冲突，请手动解决后执行："
            warn "    git add <冲突文件> && git commit"
            exit 1
        fi
        info "${MY_BRANCH} 已成功合并 main 的最新代码。"
    fi
}

main() {
    # 上游分支确认
    ensure_remote_exists upstream
    # 本地仓库上游分支确认
    ensure_remote_exists origin
    # 同步上游分支到本地 main 分支
    sync_main_with_upstream
    # 合并本地 main 分支到开发分支
    merge_main_into_dev
    
    info "更新完成！"
}

main "$@"
>>>>>>> 39c23917 (更新 update_code.sh 脚本，添加 fork 仓库同步功能)
=======
>>>>>>> 619039f0 (修改)
