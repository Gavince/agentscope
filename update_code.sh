#!/bin/bash
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
    # 先尝试拉取远程更改，避免推送冲突
    if ! git pull origin main; then
        echo "!!! 无法拉取远程更改，可能存在冲突。"
        echo "    请手动解决冲突后执行："
        echo "    git add <冲突文件> && git commit"
        echo "    然后重新运行此脚本。"
        exit 1
    fi
    # 再次推送
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

