#!/usr/bin/env python3
"""
AI产业链轮动扫描

用法:
    python main.py                    # 全量：先板块量能，再个股诊断
    python main.py --board            # 只看可上车+可关注
    python main.py --sector 液冷      # 只看一个板块
    python main.py --json             # JSON 输出
"""

import argparse
import os
import sys
import datetime
import json

from config import TUSHARE_TOKEN_ENV, STOCK_POOL
from tushare_client import TushareClient
from scanner import Scanner


def main():
    parser = argparse.ArgumentParser(description="AI产业链轮动扫描")
    parser.add_argument("--date", "-d", type=str, default=None,
                        help="交易日期 YYYYMMDD")
    parser.add_argument("--token", "-t", type=str, default=None,
                        help="Tushare token")
    parser.add_argument("--board", "-b", action="store_true",
                        help="只看可上车+可关注")
    parser.add_argument("--sector", "-s", type=str, default=None,
                        help="只看指定板块")
    parser.add_argument("--json", action="store_true",
                        help="JSON 输出到终端")
    parser.add_argument("--save-json", type=str, default=None, const="data.json",
                        nargs="?", metavar="PATH",
                        help="保存JSON到文件供HTML面板读取 (默认: data.json)")
    args = parser.parse_args()

    token = args.token or os.environ.get(TUSHARE_TOKEN_ENV, "")
    if not token:
        print("❌ 未找到 Tushare token")
        print(f"   设置: export {TUSHARE_TOKEN_ENV}=\"your_token\"")
        print("   获取: https://tushare.pro")
        sys.exit(1)

    try:
        client = TushareClient(token=token)
        scanner = Scanner(client)
        sectors, stocks, indices = scanner.run(trade_date=args.date)
        print(f"扫描交易日: {scanner.trade_date}")

        # 先保存完整 JSON + 生成嵌入式 HTML 面板
        if args.save_json:
            json_path = args.save_json
            full_json = _build_json(scanner.trade_date, sectors, stocks, indices)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(full_json, f, ensure_ascii=False, indent=2)
            print(f"数据已保存: {json_path}")

            from zhihu_report import save_zhihu_report
            report_path = save_zhihu_report(full_json)
            print(f"知乎研报已生成: {report_path}")

            # 生成自包含 HTML 面板
            html_path = os.path.join(os.path.dirname(json_path) or ".", "dashboard.html")
            _inject_html(html_path, full_json)
            print(f"面板已更新: {html_path}")

        # 过滤
        if args.sector:
            sectors = [s for s in sectors if args.sector in s.name]
            stocks  = [r for r in stocks if args.sector in r.sector]
        if args.board:
            stocks = [r for r in stocks if r.verdict in ("可上车", "可关注")]

        if args.json:
            _output_json(scanner.trade_date, sectors, stocks, indices)
        else:
            _output_text(scanner.trade_date, sectors, stocks, indices, args)

    except Exception as e:
        print(f"\n❌ 失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def _output_text(trade_date, sectors, stocks, indices, args):
    """先板块概览，再个股列表"""
    dt = datetime.datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")

    board = [r for r in stocks if r.verdict == "可上车"]
    watch = [r for r in stocks if r.verdict == "可关注"]
    avoid = [r for r in stocks if r.verdict == "回避"]

    # ========================
    # 板块量能概览
    # ========================
    print()
    print("┌" + "─" * 74 + "┐")
    print(f"│  AI产业链轮动扫描  {dt}                                     │")
    print("├" + "─" * 74 + "┤")
    print(f"│  可上车 {len(board):>2}  |  可关注 {len(watch):>2}  |  观望 {len(stocks)-len(board)-len(watch)-len(avoid):>2}  |  回避 {len(avoid):>2}                      │")
    print("└" + "─" * 74 + "┘")

    # 大盘指数概览
    if indices:
        parts = [f"{i['name']} {i['pct_chg']:+.2f}%" for i in indices]
        print()
        print(f"  大盘: {'  '.join(parts)}")

    print()
    print("━" * 76)
    print("  板块量能")
    print("━" * 76)
    print(f"  {'':<16} {'量比':>5} {'量趋势':>6} {'主力(亿)':>8}  {'今日%':>7} {'5日%':>7} {'20日%':>7} {'趋势':>6}")
    print("  " + "─" * 74)

    for s in sectors:
        name = s.name
        vr   = f"{s.avg_volume_ratio:.2f}"
        vt   = s.vol_trend
        mf   = f"{s.net_mf/1e4:+.2f}" if s.net_mf else "N/A"
        pct  = f"{s.avg_pct:+.2f}%" if s.avg_pct else "N/A"
        p5   = f"{s.avg_pct_5d:+.2f}%" if s.avg_pct_5d else "N/A"
        p20  = f"{s.avg_pct_20d:+.2f}%" if s.avg_pct_20d else "N/A"
        tend = f"{s.uptrend_count}↑{s.downtrend_count}↓"

        icon = {"活跃": "🔥", "过热": "⚠️", "退潮": "🌊", "偏强": "➕", "偏弱": "➖", "正常": "➡️"}.get(s.status, "  ")
        print(f"  {icon}{name:<14} {vr:>5} {vt:>6} {mf:>8}  {pct:>7} {p5:>7} {p20:>7} {tend:>6}")

    print()
    for s in sectors:
        print(f"  [{s.status_icon} {s.name}] {s.summary}")

    # ========================
    # 个股诊断
    # ========================
    if args.board:
        print()
        print("━" * 76)
        print("  可上车 / 可关注")
        print("━" * 76)
        _print_stock_table(stocks)
        for r in stocks:
            print(f"    [{r.name}]  {_one_liner(r)}")
        return

    groups = [
        ("✅ 可上车", [r for r in stocks if r.verdict == "可上车"]),
        ("👀 可关注", [r for r in stocks if r.verdict == "可关注"]),
        ("⏳ 观望",   [r for r in stocks if r.verdict == "观望"]),
        ("⛔ 回避",   [r for r in stocks if r.verdict == "回避"]),
    ]

    for icon, group in groups:
        if not group:
            continue
        print()
        print(f"  {icon} ({len(group)}只)")
        _print_stock_table(group)

        for r in group:
            summary = _one_liner(r)
            print(f"    [{r.name}]  {summary}")

    print()


def _one_liner(r):
    """生成个股一句话总结"""
    parts = []

    # 风险标记优先展示
    if r.risk_label:
        parts.append(r.risk_label.replace("\u26a0\ufe0f", "高风险"))

    # 量价配合
    if r.volume_ratio > 1.2 and r.pct_chg > 0:
        parts.append("放量上涨")
    elif r.volume_ratio > 1.2 and r.pct_chg < -2:
        parts.append("放量下跌")
    elif r.pct_chg > 2:
        parts.append("价涨")
    elif r.pct_chg < -2:
        parts.append("价跌")

    if r.volume_ratio < 0.5:
        parts.append("极度缩量")
    elif r.volume_ratio < 0.8:
        parts.append("缩量")

    # 位置
    if r.pct_20d > 35:
        parts.append(f"20日+{r.pct_20d:.0f}%过热")
    elif r.pct_20d > 20:
        parts.append(f"20日+{r.pct_20d:.0f}%偏高")
    elif r.pct_20d < -20:
        parts.append("深度超跌")
    elif r.pct_20d < -5:
        parts.append("短期超跌")

    # 主力
    if r.net_mf > 0:
        parts.append("主力流入")
    elif r.net_mf < 0:
        parts.append("主力流出")

    # 去重取前3
    seen = set()
    deduped = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return "；".join(deduped[:3]) if deduped else "继续观察"


def _print_stock_table(stocks):
    """打印个股表格"""
    print(f"  {'代码':<10} {'名称':<8} {'板块':<14} {'得分':>4} {'今日%':>7} {'量比':>6} {'20日%':>7} {'趋势':>4} {'风险':>6}")
    print("  " + "─" * 72)
    for r in stocks:
        code = r.code.split(".")[0]
        pct  = f"{r.pct_chg:+.2f}%" if r.pct_chg else "N/A"
        vr   = f"{r.volume_ratio:.2f}" if r.volume_ratio else "N/A"
        p20  = f"{r.pct_20d:+.1f}%" if r.pct_20d else "N/A"
        risk = r.risk_label.replace("\u26a0\ufe0f", "") if r.risk_label else ""
        print(f"  {code:<10} {r.name:<8} {r.sector:<14} {r.score:>4} {pct:>7} {vr:>6} {p20:>7} {r.trend:>4} {risk:>6}")


def _output_json(trade_date, sectors, stocks, indices):
    print(json.dumps(_build_json(trade_date, sectors, stocks, indices), ensure_ascii=False, indent=2))


def _build_json(trade_date, sectors, stocks, indices):
    return {
        "trade_date": trade_date,
        "indices": indices,
        "sectors": [
            {
                "name": s.name, "priority": s.priority,
                "avg_volume_ratio": round(s.avg_volume_ratio, 2),
                "vol_trend": s.vol_trend, "vol_trend_ratio": round(s.vol_trend_ratio, 2),
                "vol_trend_coef": round(s.vol_trend_coef, 4),
                "net_mf": round(s.net_mf, 2),
                "avg_pct": round(s.avg_pct, 2), "avg_pct_5d": round(s.avg_pct_5d, 2),
                "avg_pct_20d": round(s.avg_pct_20d, 2),
                "uptrend_count": s.uptrend_count, "downtrend_count": s.downtrend_count,
                "status": s.status, "summary": s.summary,
            } for s in sectors
        ],
        "stocks": [
            {
                "code": r.code, "name": r.name, "sector": r.sector,
                "priority": r.priority,
                "close": r.close, "pct_chg": round(r.pct_chg, 2),
                "volume_ratio": round(r.volume_ratio, 2),
                "pct_5d": round(r.pct_5d, 2), "pct_20d": round(r.pct_20d, 2),
                "trend": r.trend, "net_mf": round(r.net_mf, 2),
                "verdict": r.verdict, "score": r.score, "sector_rank": r.sector_rank,
                "risk_label": r.risk_label,
                "reasons": r.reasons, "warnings": r.warnings,
            } for r in stocks
        ]
    }


def _inject_html(html_path: str, data: dict):
    """将扫描数据注入 dashboard.html，生成自包含面板。支持反复注入。"""
    import os, re
    if not os.path.exists(html_path):
        print(f"  [跳过] 面板模板不存在: {html_path}")
        return
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    data_json = json.dumps(data, ensure_ascii=False)
    # 正则匹配 var SCAN_DATA = ...; (首次是 __DATA_PLACEHOLDER__，后续是已注入的 JSON)
    html = re.sub(
        r"var SCAN_DATA = (?:__DATA_PLACEHOLDER__|\{[\s\S]*?\});",
        f"var SCAN_DATA = {data_json};",
        html,
    )
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)


if __name__ == "__main__":
    main()
