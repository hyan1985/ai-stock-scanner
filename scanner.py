"""
AI产业链轮动扫描

流程：
  1. 板块量能 — 6个板块逐一判断资金是否在流入、是否拥挤、是否轮动
  2. 个股诊断 — 在板块结论框架下，对每只股票给出上车/观望建议
"""

import datetime
import numpy as np
import pandas as pd
from typing import Optional, List, Dict
from dataclasses import dataclass, field

from tushare_client import TushareClient
from config import STOCK_POOL, THS_SECTOR_MAP, BENCHMARKS


# ============================================================
# 数据结构
# ============================================================

@dataclass
class SectorSnap:
    """板块快照"""
    name: str
    priority: int
    stock_count: int
    preset_crowded: bool = False

    # 量能
    avg_volume_ratio: float = 1.0     # 今日平均量比
    vol_trend: str = "持平"           # 放量 / 缩量 / 持平
    vol_trend_ratio: float = 1.0      # 近5日均量 / 前5日均量

    # 资金
    net_mf: float = 0.0               # 板块主力净流入(万元)
    mf_per_stock: float = 0.0         # 平均每只主力净流入

    # 涨跌
    avg_pct: float = 0.0              # 今日平均涨跌幅
    avg_pct_5d: float = 0.0
    avg_pct_20d: float = 0.0

    # 趋势
    uptrend_count: int = 0            # 上升趋势个股数
    downtrend_count: int = 0

    # 综合判断
    status: str = "正常"              # 活跃 / 正常 / 退潮 / 过热
    status_icon: str = ""
    summary: str = ""


@dataclass
class StockVerdict:
    """个股结论"""
    code: str
    name: str
    sector: str
    priority: int

    close: float = 0.0
    pct_chg: float = 0.0
    volume_ratio: float = 1.0
    pct_5d: float = 0.0
    pct_20d: float = 0.0
    trend: str = "震荡"
    net_mf: float = 0.0

    verdict: str = "观望"             # 可上车 / 可关注 / 观望 / 回避
    score: int = 0
    risk_label: str = ""              # 风险标记：⚠️高危追涨 / ⚠️涨幅过大 / ""
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ============================================================
# 扫描引擎
# ============================================================

class Scanner:
    """板块先行 → 个股诊断"""

    def __init__(self, client: TushareClient):
        self.client = client
        self.trade_date: str = ""

    def run(self, trade_date: Optional[str] = None) -> tuple[List[SectorSnap], List[StockVerdict], List[dict]]:
        self.trade_date = trade_date or self.client._get_latest_trade_date()

        # 拉数据
        daily = self.client.get_daily_data(trade_date=self.trade_date, lookback_days=70)
        if daily.empty:
            raise RuntimeError("无法获取行情数据")
        try:
            mf = self.client.get_moneyflow(trade_date=self.trade_date, lookback_days=5)
        except Exception:
            mf = pd.DataFrame()
        # 同花顺板块指数（真实板块量能）
        try:
            ths = self.client.get_ths_daily(trade_date=self.trade_date, lookback_days=70)
        except Exception:
            ths = pd.DataFrame()
        # A股大盘指数
        try:
            idx = self.client.get_index_daily(trade_date=self.trade_date, lookback_days=25)
        except Exception:
            idx = pd.DataFrame()

        # 生成大盘指数快照
        indices = self._scan_indices(idx)

        # ========================================================
        # STEP 1: 板块量能扫描（使用同花顺板块指数数据）
        # ========================================================
        sectors: List[SectorSnap] = []
        for p in sorted(STOCK_POOL.keys()):
            info = STOCK_POOL[p]
            codes = [s["code"] for s in info["stocks"]]
            ths_code = THS_SECTOR_MAP.get(p, "")
            snap = self._scan_sector(
                info["name"], p, codes, daily, mf, ths, ths_code,
                info.get("拥挤预警", False)
            )
            sectors.append(snap)

        # ========================================================
        # STEP 2: 个股诊断（在板块结论框架下）
        # ========================================================
        sector_map = {s.priority: s for s in sectors}
        stocks: List[StockVerdict] = []
        for p in sorted(STOCK_POOL.keys()):
            info = STOCK_POOL[p]
            snap = sector_map[p]
            for s in info["stocks"]:
                v = self._scan_stock(s["code"], s["name"], info["name"], p, daily, mf, snap)
                stocks.append(v)

        # 排序
        order = {"可上车": 0, "可关注": 1, "观望": 2, "回避": 3}
        stocks.sort(key=lambda r: (order.get(r.verdict, 9), -r.score))

        return sectors, stocks, indices

    # ---------------------------------------------------------------
    # 板块扫描
    # ---------------------------------------------------------------
    def _scan_sector(
        self, name: str, priority: int, codes: List[str],
        daily: pd.DataFrame, mf: pd.DataFrame, ths: pd.DataFrame,
        ths_code: str, preset_crowded: bool,
    ) -> SectorSnap:
        snap = SectorSnap(
            name=name, priority=priority, stock_count=len(codes),
            preset_crowded=preset_crowded,
        )

        # --- 用同花顺板块指数获取真实的板块涨跌与量能 ---
        ths_df = ths[ths["ts_code"] == ths_code].sort_values("trade_date") if not ths.empty else pd.DataFrame()
        ths_today = ths_df[ths_df["trade_date"] == self.trade_date] if not ths_df.empty else pd.DataFrame()

        df = daily[daily["ts_code"].isin(codes)]
        today = df[df["trade_date"] == self.trade_date]

        if not ths_today.empty:
            # 今日涨跌幅（板块指数真实数据）
            snap.avg_pct = ths_today.iloc[0].get("pct_change", 0)
            # 板块量比
            snap.avg_volume_ratio = ths_today.iloc[0].get("volume_ratio", 1.0)
        else:
            # 回退：从个股数据汇总（无板块指数时）
            if not today.empty:
                snap.avg_pct = today["pct_chg"].mean()
                snap.avg_volume_ratio = today["volume_ratio"].mean()

        # 5日 / 20日涨跌幅
        if len(ths_df) >= 6:
            ths_closes = ths_df["close"].values.astype(float)
            snap.avg_pct_5d = (ths_closes[-1] / ths_closes[-6] - 1) * 100
            snap.avg_pct_20d = (ths_closes[-1] / ths_closes[-21] - 1) * 100 if len(ths_df) >= 21 else 0
        else:
            # 回退：从个股汇总
            closes_pivot = df.pivot_table(
                index="trade_date", columns="ts_code", values="close", aggfunc="last"
            ).sort_index()
            if len(closes_pivot) >= 6:
                pct5 = (closes_pivot.iloc[-1] / closes_pivot.iloc[-6] - 1) * 100
                snap.avg_pct_5d = pct5.mean()
            if len(closes_pivot) >= 21:
                pct20 = (closes_pivot.iloc[-1] / closes_pivot.iloc[-21] - 1) * 100
                snap.avg_pct_20d = pct20.mean()

        # --- 量能趋势 ---
        if len(ths_df) >= 10:
            ths_vols = ths_df["vol"].values.astype(float)
            recent = ths_vols[-5:].mean()
            prior = ths_vols[-10:-5].mean()
            if prior > 0:
                snap.vol_trend_ratio = recent / prior
                snap.vol_trend = "放量" if snap.vol_trend_ratio > 1.3 else ("缩量" if snap.vol_trend_ratio < 0.7 else "持平")
        else:
            # 回退：从个股汇总
            vols = df.pivot_table(
                index="trade_date", columns="ts_code", values="vol", aggfunc="sum"
            ).sort_index()
            if len(vols) >= 10:
                total_vol = vols.sum(axis=1)
                recent = total_vol.iloc[-5:].mean()
                prior = total_vol.iloc[-10:-5].mean()
                if prior > 0:
                    snap.vol_trend_ratio = recent / prior
                    snap.vol_trend = "放量" if snap.vol_trend_ratio > 1.3 else ("缩量" if snap.vol_trend_ratio < 0.7 else "持平")

        # --- 资金流向（仍然从个股汇总，因为板块指数没有主力资金字段）---
        if not mf.empty:
            mf_today = mf[(mf["ts_code"].isin(codes)) & (mf["trade_date"] == self.trade_date)]
            if not mf_today.empty:
                snap.net_mf = mf_today["net_mf_amount"].sum()
                snap.mf_per_stock = snap.net_mf / len(codes)

        # --- 趋势统计（基于个股数据）---
        for code in codes:
            stock_closes = df[df["ts_code"] == code]["close"].values.astype(float)
            if len(stock_closes) >= 20:
                ma5  = np.mean(stock_closes[-5:])
                ma20 = np.mean(stock_closes[-20:])
                ma5p  = np.mean(stock_closes[-15:-10])
                ma20p = np.mean(stock_closes[-35:-15]) if len(stock_closes) >= 35 else ma20
                if ma5 > ma5p * 1.02 and ma20 > ma20p * 1.01:
                    snap.uptrend_count += 1
                elif ma5 < ma5p * 0.98 and ma20 < ma20p * 0.99:
                    snap.downtrend_count += 1

        # --- 板块状态判断 ---
        reasons = []

        # 过热
        if preset_crowded or snap.avg_pct_20d > 38:
            snap.status = "过热"
            snap.status_icon = "🔥"
            reasons.append("板块过热" if preset_crowded else f"20日涨{snap.avg_pct_20d:.0f}%过热")
        # 活跃
        elif snap.vol_trend == "放量" and snap.net_mf > 0 and snap.avg_pct_5d > 0:
            snap.status = "活跃"
            snap.status_icon = "🔥"
            reasons.append(f"放量+资金流入")
        elif snap.vol_trend == "放量" and snap.avg_pct > 0:
            snap.status = "活跃"
            snap.status_icon = "🔥"
            reasons.append(f"放量上涨")
        # 退潮
        elif snap.vol_trend == "放量" and snap.avg_pct < -1:
            snap.status = "退潮"
            snap.status_icon = "🌊"
            reasons.append("放量下跌，资金在退")
        elif snap.vol_trend == "缩量" and snap.avg_pct_5d < -2:
            snap.status = "退潮"
            snap.status_icon = "🌊"
            reasons.append(f"缩量阴跌")

        # 补充说明
        if snap.net_mf > 0:
            reasons.append(f"主力流入{snap.net_mf/1e4:.1f}亿")
        elif snap.net_mf < 0:
            reasons.append(f"主力流出{abs(snap.net_mf)/1e4:.1f}亿")

        if snap.uptrend_count >= len(codes) * 0.6:
            reasons.append(f"{snap.uptrend_count}/{snap.stock_count}只上升趋势")
        if snap.downtrend_count >= len(codes) * 0.6:
            reasons.append(f"{snap.downtrend_count}/{snap.stock_count}只下跌趋势")

        snap.summary = "；".join(reasons) if reasons else "量能平淡，方向不明"
        return snap

    # ---------------------------------------------------------------
    # 大盘指数快照
    # ---------------------------------------------------------------
    def _scan_indices(self, idx: pd.DataFrame) -> List[dict]:
        index_names = {v: k for k, v in BENCHMARKS.items()}
        result = []
        if idx.empty:
            return result

        for code in BENCHMARKS.values():
            df = idx[idx["ts_code"] == code].sort_values("trade_date")
            if df.empty:
                continue
            today = df[df["trade_date"] == self.trade_date]
            pct_chg = today.iloc[0].get("pct_chg", 0) if not today.empty else 0
            close = today.iloc[0].get("close", 0) if not today.empty else 0
            closes = df["close"].values.astype(float)
            n = len(closes)
            pct_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
            pct_20d = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else 0
            result.append({
                "name": index_names.get(code, code),
                "code": code,
                "close": round(float(close), 2),
                "pct_chg": round(float(pct_chg), 2),
                "pct_5d": round(float(pct_5d), 2),
                "pct_20d": round(float(pct_20d), 2),
            })
        return result

    # ---------------------------------------------------------------
    # 个股诊断
    # ---------------------------------------------------------------
    def _scan_stock(
        self, code: str, name: str, sector: str, priority: int,
        daily: pd.DataFrame, mf: pd.DataFrame, snap: SectorSnap,
    ) -> StockVerdict:
        v = StockVerdict(code=code, name=name, sector=sector, priority=priority)

        df = daily[daily["ts_code"] == code].sort_values("trade_date")
        if df.empty or len(df) < 5:
            v.verdict = "观望"
            v.warnings.append("数据不足")
            return v

        today_rows = df[df["trade_date"] == self.trade_date]
        t = today_rows.iloc[-1] if not today_rows.empty else df.iloc[-1]

        v.close = t["close"]
        v.pct_chg = t.get("pct_chg", 0)
        v.volume_ratio = t.get("volume_ratio", 1.0)

        closes = df["close"].values.astype(float)
        volumes = df["vol"].values.astype(float)
        n = len(closes)

        v.pct_5d  = (closes[-1] / closes[-6]  - 1) * 100 if n >= 6  else 0
        v.pct_20d = (closes[-1] / closes[-21] - 1) * 100 if n >= 21 else 0

        v.trend = self._trend(closes)

        if not mf.empty:
            row = mf[(mf["ts_code"] == code) & (mf["trade_date"] == self.trade_date)]
            if not row.empty:
                v.net_mf = row.iloc[0].get("net_mf_amount", 0)

        # ========== 评分 ==========
        score = 40
        reasons = []
        warnings = []

        # --- A. 量能 (35分) ---
        vr = v.volume_ratio

        # 量比健康度
        if 0.8 <= vr <= 2.0:
            score += 8
        elif vr > 2.5:
            score -= 6
            warnings.append(f"量比{vr:.1f}异常放大")
        elif vr < 0.5:
            score -= 4
            warnings.append(f"极度缩量({vr:.2f})")

        # 量价配合
        if v.pct_chg > 2 and vr > 1.2:
            score += 12
            reasons.append("放量上涨")
        elif v.pct_chg > 0 and vr >= 0.8:
            score += 6
            reasons.append("温和上涨")
        elif v.pct_chg < -3 and vr > 1.5:
            score -= 12
            warnings.append("放量下跌")
        elif v.pct_chg < 0 and vr < 0.8:
            score += 4
            reasons.append("缩量回调")

        # 主力
        if v.net_mf > 0:
            score += 8
            reasons.append("主力流入")
        elif v.net_mf < 0:
            score -= 6
            warnings.append("主力流出")

        # 板块量能加成
        if snap.status == "活跃" and snap.net_mf > 0:
            score += 5
            reasons.append(f"板块活跃+")

        # --- B. 趋势 (30分) ---
        # 均线
        ma5  = np.mean(closes[-5:])  if n >= 5  else 0
        ma20 = np.mean(closes[-20:]) if n >= 20 else 0

        if ma5 > 0 and ma20 > 0 and v.close > ma5 > ma20:
            score += 15
            reasons.append("多头排列")
        elif v.close > ma5 > ma20 * 0.98:
            score += 10
            reasons.append("短中期向好")
        elif ma20 > 0 and v.close > ma20:
            score += 6
            reasons.append("站上20日线")
        elif ma20 > 0 and v.close < ma20 < ma5:
            score -= 10
            warnings.append("空头排列")
        elif ma20 > 0 and v.close < ma20:
            score -= 5
            warnings.append("跌破20日线")

        if v.trend == "上升":
            score += 10
        elif v.trend == "下跌":
            score -= 8
            warnings.append("下跌趋势")

        # 低位突破
        if (v.pct_20d < 15 and v.close > ma20 > 0
                and vr > 1.2 and v.pct_chg > 1 and v.trend != "下跌"):
            score += 8
            reasons.append("低位突破")

        # --- C. 位置 (20分) ---
        if v.pct_20d > 80:
            score -= 30
            v.risk_label = "⚠️高危追涨"
            warnings.append(f"80%+高危追涨")
        elif v.pct_20d > 50:
            score -= 20
            v.risk_label = "⚠️涨幅过大"
            warnings.append(f"{v.pct_20d:.0f}%涨幅过大")
        elif v.pct_20d > 35:
            score -= 12
            warnings.append(f"{v.pct_20d:.0f}%过热")
        elif v.pct_20d > 25:
            score -= 8
            warnings.append(f"涨幅{v.pct_20d:.0f}%偏高")
        elif v.pct_20d < -20:
            score += 8
            reasons.append("深度超跌")
        elif v.pct_20d < -10:
            score += 4
            reasons.append("短期超跌")

        # 连阳
        if n >= 5:
            up = sum(1 for i in range(1, 5) if closes[-1-i] < closes[-i])
            if up >= 4:
                score += 5
                reasons.append("连阳走强")

        # --- D. 优先级 (15分) ---
        if priority <= 2:
            score += 6
            if snap.status != "过热":
                reasons.append("研报首推板块")
        if snap.preset_crowded and v.pct_20d > 20:
            score -= 8
            warnings.append("板块拥挤+个股涨幅大")
        if snap.status == "退潮":
            score -= 5
            warnings.append("板块退潮")

        # ========== 结论 ==========
        score = max(0, min(100, score))
        v.score = score
        v.reasons = reasons
        v.warnings = warnings

        if   score >= 75: v.verdict = "可上车"
        elif score >= 55: v.verdict = "可关注"
        elif score >= 35: v.verdict = "观望"
        else:             v.verdict = "回避"

        # 高危追涨 → 最高只能到"可关注"
        if v.risk_label == "⚠️高危追涨" and v.verdict == "可上车":
            v.verdict = "可关注"

        return v

    @staticmethod
    def _trend(closes: np.ndarray) -> str:
        n = len(closes)
        if n < 20:
            return "震荡"
        ma5  = np.mean(closes[-5:])
        ma5p = np.mean(closes[-15:-10])
        ma20  = np.mean(closes[-20:])
        ma20p = np.mean(closes[-35:-15]) if n >= 35 else ma20
        if ma5 > ma5p * 1.02 and ma20 > ma20p * 1.01:
            return "上升"
        elif ma5 < ma5p * 0.98 and ma20 < ma20p * 0.99:
            return "下跌"
        return "震荡"
