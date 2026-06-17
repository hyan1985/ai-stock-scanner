#!/bin/bash
# ================================================================
# AI产业链轮动扫描 — 一键安装
#
# 用法:
#   chmod +x install.sh
#   ./install.sh
# ================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "  AI产业链轮动扫描 — 安装"
echo "========================================"
echo ""

if [ ! -d ".venv" ]; then
    echo "[1/2] 创建虚拟环境..."
    python3 -m venv .venv
else
    echo "[1/2] 虚拟环境已存在，跳过"
fi

echo "[2/2] 安装依赖 (tushare, pandas, numpy)..."
source .venv/bin/activate
pip install -r requirements.txt -q

if [ -z "$TUSHARE_TOKEN" ]; then
    echo ""
    echo "  ⚠️  未检测到 TUSHARE_TOKEN"
    echo "  获取: https://tushare.pro"
    echo "  设置: export TUSHARE_TOKEN=\"your_token\""
    echo ""
else
    echo "  ✅ TUSHARE_TOKEN 已设置"
fi

echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "  运行:  source .venv/bin/activate && python main.py"
echo "  定时:  ./setup_daily_run.sh  (每日15:30自动扫描)"
echo ""
