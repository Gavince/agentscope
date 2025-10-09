#!/bin/bash

# update_code.sh - 更新代码到远程仓库的脚本

set -e  # 遇到错误时退出

echo ">>> 开始更新代码..."

# 检查当前分支
current_branch=$(git branch --show-current)
echo ">>> 当前分支: $current_branch"

# 检查是否有未提交的更改
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo ">>> 检测到未提交的更改，正在提交..."
    git add .
    git commit -m "自动提交: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ">>> 更改已提交"
else
    echo ">>> 没有检测到未提交的更改"
fi

# 如果当前不在 main 分支，切换到 main 分支
if [ "$current_branch" != "main" ]; then
    echo ">>> 切换到 main 分支..."
    git checkout main
else
    echo ">>> 已在 main 分支"
fi

# 检查是否有远程仓库
if git remote get-url origin >/dev/null 2>&1; then
    echo ">>> 远程仓库已配置: $(git remote get-url origin)"
    
    # 拉取远程更改
    echo ">>> 拉取远程更改..."
    git pull origin main
    
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
else
    echo ">>> 未配置远程仓库，跳过推送"
fi

echo ">>> 代码更新完成！"