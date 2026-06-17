#!/bin/bash
# ================================================================
# AI产业链轮动每日扫描 — macOS 定时任务安装脚本
#
# 用法:
#   chmod +x setup_daily_run.sh
#   ./setup_daily_run.sh
#
# 这会在 macOS launchd 注册一个定时任务，
# 每个交易日 15:30(收盘后) 自动执行扫描。
# ================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_NAME="com.ai.rotation.scanner"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"
PYTHON_BIN="$SCRIPT_DIR/.venv/bin/python"
LOG_DIR="$SCRIPT_DIR/logs"

echo "=== AI产业链轮动扫描 — 定时任务安装 ==="
echo ""
echo "  工作目录: $SCRIPT_DIR"
echo "  Python:   $PYTHON_BIN"
echo "  日志:     $LOG_DIR"
echo ""

# 检查虚拟环境
if [ ! -f "$PYTHON_BIN" ]; then
    echo "  [错误] 未找到虚拟环境，请先运行:"
    echo "    python3 -m venv .venv"
    echo "    source .venv/bin/activate"
    echo "    pip install -r requirements.txt"
    exit 1
fi

# 检查 TUSHARE_TOKEN
if [ -z "$TUSHARE_TOKEN" ]; then
    echo "  [警告] 未设置 TUSHARE_TOKEN 环境变量"
    echo "  请在 ~/.zshrc 中添加:"
    echo "    export TUSHARE_TOKEN=\"your_token_here\""
    echo ""
fi

# 创建日志目录
mkdir -p "$LOG_DIR"

# 生成 launchd plist
cat > "$PLIST_PATH" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>

    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${SCRIPT_DIR}/main.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>TUSHARE_TOKEN</key>
        <string>${TUSHARE_TOKEN:-YOUR_TOKEN_HERE}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>

    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>

    <key>StandardOutPath</key>
    <string>${LOG_DIR}/stdout.log</string>

    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/stderr.log</string>

    <!-- 每个交易日 15:30 (北京时间) 执行 -->
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>30</integer>
        <key>Weekday</key>
        <integer>1</integer>
    </dict>
    <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>30</integer>
        <key>Weekday</key>
        <integer>2</integer>
    </dict>
    <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>30</integer>
        <key>Weekday</key>
        <integer>3</integer>
    </dict>
    <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>30</integer>
        <key>Weekday</key>
        <integer>4</integer>
    </dict>
    <dict>
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>30</integer>
        <key>Weekday</key>
        <integer>5</integer>
    </dict>

    <key>RunAtLoad</key>
    <false/>
</dict>
</plist>
PLISTEOF

# 卸载旧任务（如果存在）
launchctl unload "$PLIST_PATH" 2>/dev/null || true

# 加载新任务
launchctl load "$PLIST_PATH"

echo "  已安装定时任务: $PLIST_PATH"
echo "  每周一至周五 15:30 自动执行扫描"
echo ""
echo "  管理命令:"
echo "    查看状态: launchctl list | grep ${PLIST_NAME}"
echo "    手动触发: launchctl start ${PLIST_NAME}"
echo "    停止任务: launchctl unload $PLIST_PATH"
echo "    查看日志: tail -f $LOG_DIR/stdout.log"
echo ""
echo "  提示: 如果 TUSHARE_TOKEN 未设置，请编辑 plist 文件替换 YOUR_TOKEN_HERE"
echo "        vim $PLIST_PATH"
