"""生成适合发布知乎的每日轮动研报（Markdown）。"""

from __future__ import annotations

import datetime
import json
import os
from typing import Dict, List, Optional


def _fmt_date(trade_date: str) -> str:
    return datetime.datetime.strptime(trade_date, "%Y%m%d").strftime("%Y-%m-%d")


def _code6(code: str) -> str:
    return code.split(".")[0]


def _sign_pct(v: float) -> str:
    return f"{v:+.2f}%"


def _mf_yi(v: float) -> str:
    return f"{v / 1e4:+.2f}亿"


def _status_emoji(status: str) -> str:
    return {"活跃": "🔥", "过热": "⚠️", "退潮": "🌊", "正常": "➖"}.get(status, "")


def _pick_headline(sectors: List[dict]) -> str:
    active = [s for s in sectors if s.get("status") == "活跃"]
    hot = [s for s in sectors if s.get("status") == "过热"]
    if active:
        names = "、".join(s["name"].split("/")[0] for s in active[:3])
        return f"{names}等板块量能抬升"
    if hot:
        return f"{hot[0]['name']}等板块情绪偏热"
    retreat = [s for s in sectors if s.get("status") == "退潮"]
    if retreat:
        return "部分板块出现退潮信号"
    return "板块分化，宜精选个股"


def _stock_line(s: dict) -> str:
    code = _code6(s["code"])
    risk = s.get("risk_label", "").replace("⚠️", "").strip()
    risk_part = f"（{risk}）" if risk else ""
    reason_list = [
        r for r in s.get("reasons", [])
        if not r.startswith("板块内第")
    ][:4]
    reasons = "、".join(reason_list)
    hints = "、".join(s.get("warnings", [])[:2])
    tail = reasons or hints or "继续观察量价"
    return (
        f"- **{s['name']}**（{code}）"
        f" 收盘{s['close']:.2f}，今日{_sign_pct(s['pct_chg'])}，"
        f"20日{_sign_pct(s['pct_20d'])}，板块内第{s.get('sector_rank', '-')} "
        f"{risk_part}\n"
        f"  - {tail}"
    )


def generate_zhihu_report(data: dict) -> str:
    """从扫描 JSON 生成知乎 Markdown 正文。"""
    trade_date = data["trade_date"]
    dt = _fmt_date(trade_date)
    sectors = data.get("sectors", [])
    stocks = data.get("stocks", [])
    indices = data.get("indices", [])

    board = [s for s in stocks if s["verdict"] == "可上车"]
    watch = [s for s in stocks if s["verdict"] == "可关注"]
    headline = _pick_headline(sectors)

    lines: List[str] = []
    lines.append(f"# 【AI产业链轮动】{dt} 收盘扫描：{headline}")
    lines.append("")
    lines.append(
        f"> 基于 Tushare 行情与资金流，覆盖 {len(stocks)} 只 AI 产业链标的，"
        f"按 **板块内相对比较** 择优推荐。"
        f"完整面板：[在线仪表盘](https://hyan1985.github.io/ai-stock-scanner/)"
    )
    lines.append("")

    # --- 大盘 ---
    lines.append("## 一、大盘环境")
    lines.append("")
    if indices:
        parts = [f"**{i['name']}** {_sign_pct(i['pct_chg'])}（20日 {_sign_pct(i['pct_20d'])}）" for i in indices]
        lines.append(" | ".join(parts))
    else:
        lines.append("（指数数据暂缺）")
    lines.append("")

    board_n, watch_n = len(board), len(watch)
    lines.append(
        f"今日扫描结论：**可上车 {board_n} 只**（每板块 Top2）、"
        f"**可关注 {watch_n} 只**（每板块第3~5名）。"
    )
    lines.append("")

    # --- 板块 ---
    lines.append("## 二、板块量能与轮动")
    lines.append("")
    lines.append("| 板块 | 状态 | 量比 | 3日量趋势 | 主力 | 今日 | 5日 | 20日 |")
    lines.append("| --- | --- | ---: | --- | ---: | ---: | ---: | ---: |")
    for s in sectors:
        em = _status_emoji(s.get("status", ""))
        lines.append(
            f"| {s['name']} | {em}{s.get('status', '')} | "
            f"{s.get('avg_volume_ratio', 0):.2f} | {s.get('vol_trend', '-')} | "
            f"{_mf_yi(s.get('net_mf', 0))} | "
            f"{_sign_pct(s.get('avg_pct', 0))} | "
            f"{_sign_pct(s.get('avg_pct_5d', 0))} | "
            f"{_sign_pct(s.get('avg_pct_20d', 0))} |"
        )
    lines.append("")

    for s in sectors:
        if s.get("summary"):
            lines.append(f"- **{s['name']}**：{s['summary']}")
    lines.append("")

    # --- 核心结论 ---
    lines.append("## 三、今日核心结论")
    lines.append("")
    active_secs = [s["name"] for s in sectors if s.get("status") == "活跃"]
    hot_secs = [s["name"] for s in sectors if s.get("status") == "过热"]
    if active_secs:
        lines.append(f"1. **量能抬升**：{ '、'.join(active_secs) }，短线资金仍在产业链内轮动。")
    if hot_secs:
        lines.append(f"2. **拥挤预警**：{ '、'.join(hot_secs) }情绪偏热，追高需控制仓位。")
    if board:
        top_names = "、".join(f"{s['name']}（{s['sector']}）" for s in board[:4])
        lines.append(f"3. **板块龙头**：{top_names} 等在各自主板块中综合得分领先。")
    lines.append("4. 以下推荐均为 **板块内横向比较** 结果，不代表全市场绝对低估。")
    lines.append("")

    # --- 可上车 ---
    lines.append("## 四、可上车标的（每板块 Top2）")
    lines.append("")
    if not board:
        lines.append("今日无标的进入「可上车」档，宜观望或仅小仓试探。")
        lines.append("")
    else:
        by_sector: Dict[str, List[dict]] = {}
        for s in board:
            by_sector.setdefault(s["sector"], []).append(s)
        for sec in sorted(by_sector, key=lambda x: by_sector[x][0].get("priority", 9)):
            items = sorted(by_sector[sec], key=lambda x: x.get("sector_rank", 99))
            lines.append(f"### {sec}")
            lines.append("")
            for s in items:
                lines.append(_stock_line(s))
            lines.append("")

    # --- 可关注 ---
    lines.append("## 五、可关注标的（每板块第3~5名）")
    lines.append("")
    if watch:
        by_sector = {}
        for s in watch:
            by_sector.setdefault(s["sector"], []).append(s)
        for sec in sorted(by_sector, key=lambda x: by_sector[x][0].get("priority", 9)):
            items = sorted(by_sector[sec], key=lambda x: x.get("sector_rank", 99))
            names = "、".join(
                f"{s['name']}({_code6(s['code'])})" for s in items
            )
            lines.append(f"- **{sec}**：{names}")
        lines.append("")
    else:
        lines.append("（无）")
        lines.append("")

    # --- 免责 ---
    lines.append("---")
    lines.append("")
    lines.append("## 免责声明")
    lines.append("")
    lines.append(
        "本文仅为 AI 产业链量化扫描的结构化输出，**不构成投资建议**。"
        "数据来源于 Tushare，存在延迟与误差可能；"
        "股市有风险，决策请独立判断。"
    )
    lines.append("")
    lines.append(f"*生成时间：{dt} 收盘后 · 工具：ai-stock-scanner*")

    return "\n".join(lines)


def save_zhihu_report(
    data: dict,
    output_dir: Optional[str] = None,
) -> str:
    """写入 Markdown 文件，返回路径。"""
    output_dir = output_dir or os.path.join(
        os.path.dirname(__file__), "reports"
    )
    os.makedirs(output_dir, exist_ok=True)

    trade_date = data["trade_date"]
    content = generate_zhihu_report(data)
    dated = os.path.join(output_dir, f"知乎_AI产业链轮动_{trade_date}.md")
    latest = os.path.join(output_dir, "知乎_最新.md")

    with open(dated, "w", encoding="utf-8") as f:
        f.write(content)
    with open(latest, "w", encoding="utf-8") as f:
        f.write(content)

    return dated


def main():
    import argparse

    parser = argparse.ArgumentParser(description="从 data.json 生成知乎研报")
    parser.add_argument("--input", "-i", default="data.json", help="扫描 JSON 路径")
    parser.add_argument("--output-dir", "-o", default=None, help="输出目录")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)
    path = save_zhihu_report(data, args.output_dir)
    print(f"知乎研报已生成: {path}")
    print(f"同步副本: {os.path.join(os.path.dirname(path), '知乎_最新.md')}")


if __name__ == "__main__":
    main()
