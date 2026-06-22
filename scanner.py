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
from config import STOCK_POOL, THS_SECTOR_MAP, BENCHMARKS, THRESHOLDS


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
    vol_trend: str = "持平"           # 放量 / 缩量 / 温和放量 / 温和缩量 / 持平
    vol_trend_ratio: float = 1.0      # 3日量能比: 扫描日成交量 / 3日前成交量
    vol_trend_coef: float = 0.0       # 3日量能趋势系数 VTC（见 _calc_vol_trend_3d）

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
    sector_rank: int = 0              # 板块内综合排名（1=最优）
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

        # 板块内相对比较 → 择优 1-2 可上车
        self._rank_stocks_within_sectors(stocks)

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

        # --- 量能趋势：扫描日起往前3个交易日的量能趋势系数 VTC ---
        vol_series = self._get_volume_series(ths_df, df, codes)
        coef, ratio, label = self._calc_vol_trend_3d(vol_series)
        snap.vol_trend_coef = coef
        snap.vol_trend_ratio = ratio
        snap.vol_trend = label

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
        else:
            vol_up = label.startswith("放量") or label.startswith("温和放量")
            vol_down = label.startswith("缩量") or label.startswith("温和缩量")
            if vol_up and snap.net_mf > 0 and snap.avg_pct_5d > 0:
                snap.status = "活跃"
                snap.status_icon = "🔥"
                reasons.append(f"{label}+资金流入")
            elif vol_up and snap.avg_pct > 0:
                snap.status = "活跃"
                snap.status_icon = "🔥"
                reasons.append(f"{label}上涨")
            elif vol_up and snap.avg_pct < -1:
                snap.status = "退潮"
                snap.status_icon = "🌊"
                reasons.append("放量下跌，资金在退")
            elif vol_down and snap.avg_pct_5d < -2:
                snap.status = "退潮"
                snap.status_icon = "🌊"
                reasons.append(f"{label}阴跌")

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

        # ========== 因子采集（板块内相对比较，不在此处定 verdict）==========
        reasons = []
        warnings = []
        vr = v.volume_ratio
        ma5  = np.mean(closes[-5:])  if n >= 5  else 0
        ma20 = np.mean(closes[-20:]) if n >= 20 else 0

        # 量价与资金（描述性标签）
        if v.pct_chg > 2 and vr > 1.2:
            reasons.append("放量上涨")
        elif v.pct_chg > 0 and vr >= 0.8:
            reasons.append("温和上涨")
        elif v.pct_chg < -3 and vr > 1.5:
            warnings.append("放量下跌")
        elif v.pct_chg < 0 and vr < 0.8:
            reasons.append("缩量回调")

        if vr > 2.5:
            warnings.append(f"量比{vr:.1f}异常放大")
        elif vr < 0.5:
            warnings.append(f"极度缩量({vr:.2f})")

        if v.net_mf > 0:
            reasons.append("主力流入")
        elif v.net_mf < 0:
            warnings.append("主力流出")

        if ma5 > 0 and ma20 > 0 and v.close > ma5 > ma20:
            reasons.append("多头排列")
            ma_score = 3
        elif v.close > ma5 > ma20 * 0.98:
            reasons.append("短中期向好")
            ma_score = 2
        elif ma20 > 0 and v.close > ma20:
            reasons.append("站上20日线")
            ma_score = 1
        elif ma20 > 0 and v.close < ma20 < ma5:
            warnings.append("空头排列")
            ma_score = 0
        elif ma20 > 0 and v.close < ma20:
            warnings.append("跌破20日线")
            ma_score = 0
        else:
            ma_score = 1

        if v.trend == "上升":
            reasons.append("上升趋势")
        elif v.trend == "下跌":
            warnings.append("下跌趋势")

        if v.pct_20d > 80:
            v.risk_label = "⚠️高位博弈"
            warnings.append(f"{v.pct_20d:.0f}%高位博弈")
        elif v.pct_20d > 50:
            v.risk_label = "⚠️涨幅已大"
            warnings.append(f"{v.pct_20d:.0f}%涨幅已大")
        elif v.pct_20d > 35:
            warnings.append(f"{v.pct_20d:.0f}%偏高")
        elif v.pct_20d < -20:
            reasons.append("深度超跌")
        elif v.pct_20d < -10:
            reasons.append("短期超跌")

        if snap.preset_crowded and v.pct_20d > 20:
            warnings.append("板块拥挤+个股涨幅大")
        if snap.status == "退潮":
            warnings.append("板块退潮")

        trend_score = {"上升": 2, "震荡": 1, "下跌": 0}.get(v.trend, 1)
        vol_price = (
            v.pct_chg * min(vr, 2.5)
            if v.pct_chg > 0
            else v.pct_chg - abs(v.pct_chg) * 0.3
        )

        # 板块内比较用原始因子（越大越好，除 position 外已按偏好构造）
        v._factors = {
            "mf": float(v.net_mf),
            "vol_price": float(vol_price),
            "trend": float(trend_score + ma_score),
            "vr_health": float(-abs(vr - 1.1)),
            "position": float(-abs(v.pct_20d - 12)),
        }
        v._force_avoid = v.pct_chg < -3 and vr > 1.5

        v.reasons = reasons
        v.warnings = warnings
        v.verdict = "观望"
        v.score = 0

        return v

    # ---------------------------------------------------------------
    # 量能趋势 & 板块内排名
    # ---------------------------------------------------------------

    def _get_volume_series(
        self, ths_df: pd.DataFrame, df: pd.DataFrame, codes: List[str]
    ) -> np.ndarray:
        """板块成交量序列（优先同花顺板块指数）。"""
        if not ths_df.empty and len(ths_df) >= 3:
            return ths_df["vol"].values.astype(float)
        vols = df.pivot_table(
            index="trade_date", columns="ts_code", values="vol", aggfunc="sum"
        ).sort_index()
        if vols.empty:
            return np.array([])
        return vols.sum(axis=1).values.astype(float)

    def _calc_vol_trend_3d(self, vols: np.ndarray) -> tuple:
        """
        3日量能趋势系数 VTC（Volume Trend Coefficient）

        取扫描日及前2个交易日共3天成交量 [V0, V1, V2]（时间升序），
        对 x=[0,1,2] 做线性回归得斜率 s，VTC = s / mean(V)。
        VTC > 0 表示量能逐日抬升，< 0 表示递减。

        同时计算 3日量能比 R3 = V2 / V0（扫描日相对3日前）。
        """
        days = int(THRESHOLDS.get("vol_trend_days", 3))
        if vols is None or len(vols) < days:
            return 0.0, 1.0, "持平"

        y = vols[-days:].astype(float)
        if y.mean() <= 0:
            return 0.0, 1.0, "持平"

        x = np.arange(days, dtype=float)
        slope = float(np.polyfit(x, y, 1)[0])
        vtc = slope / y.mean()
        ratio = float(y[-1] / y[0]) if y[0] > 0 else 1.0

        expand = THRESHOLDS.get("vol_trend_expand", 0.05)
        shrink = THRESHOLDS.get("vol_trend_shrink", -0.05)
        mild_up = THRESHOLDS.get("vol_trend_mild_expand", 0.02)
        mild_dn = THRESHOLDS.get("vol_trend_mild_shrink", -0.02)

        pct = (ratio - 1) * 100
        pct_s = f"{pct:+.0f}%"

        if vtc >= expand:
            label = f"放量({pct_s})"
        elif vtc >= mild_up:
            label = f"温和放量({pct_s})"
        elif vtc <= shrink:
            label = f"缩量({pct_s})"
        elif vtc <= mild_dn:
            label = f"温和缩量({pct_s})"
        else:
            label = f"持平({pct_s})"

        return round(vtc, 4), round(ratio, 3), label

    @staticmethod
    def _percentile_rank(values: List[float], higher_better: bool = True) -> List[float]:
        """返回 0~1 分位，ties 取平均名次。"""
        n = len(values)
        if n == 0:
            return []
        if n == 1:
            return [1.0]

        order = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j < n - 1 and values[order[j + 1]] == values[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0
            for k in range(i, j + 1):
                ranks[order[k]] = avg_rank
            i = j + 1

        if not higher_better:
            ranks = [n - 1 - r for r in ranks]

        return [(r / (n - 1)) for r in ranks]

    def _rank_stocks_within_sectors(self, stocks: List[StockVerdict]) -> None:
        """各板块内因子横向比较，择优推荐 Top N。"""
        weights = THRESHOLDS.get("factor_weights", {
            "mf": 0.25,
            "vol_price": 0.20,
            "trend": 0.25,
            "vr_health": 0.15,
            "position": 0.15,
        })
        top_board = int(THRESHOLDS.get("sector_board_top", 2))
        top_watch = int(THRESHOLDS.get("sector_watch_top", 3))

        for p in sorted(STOCK_POOL.keys()):
            group = [s for s in stocks if s.priority == p]
            if not group:
                continue

            for s in group:
                if getattr(s, "_force_avoid", False):
                    s.verdict = "回避"
                    s.score = 0
                    s.sector_rank = 99

            candidates = [s for s in group if s.verdict != "回避"]
            if not candidates:
                continue

            factor_names = list(weights.keys())
            rank_matrix = {fn: [] for fn in factor_names}
            for s in candidates:
                f = getattr(s, "_factors", {})
                for fn in factor_names:
                    rank_matrix[fn].append(f.get(fn, 0.0))

            composite = [0.0] * len(candidates)
            for fn in factor_names:
                w = weights.get(fn, 0)
                if w <= 0:
                    continue
                pr = self._percentile_rank(rank_matrix[fn], higher_better=True)
                for i, r in enumerate(pr):
                    composite[i] += w * r

            ranked = sorted(
                zip(candidates, composite),
                key=lambda x: x[1],
                reverse=True,
            )

            for rank, (s, comp) in enumerate(ranked, start=1):
                s.sector_rank = rank
                s.score = int(round(comp * 100))
                if rank <= top_board:
                    s.verdict = "可上车"
                    s.reasons.insert(0, f"板块内第{rank}")
                elif rank <= top_board + top_watch:
                    s.verdict = "可关注"
                    s.reasons.insert(0, f"板块内第{rank}")
                else:
                    s.verdict = "观望"

            for s in group:
                if hasattr(s, "_factors"):
                    del s._factors
                if hasattr(s, "_force_avoid"):
                    del s._force_avoid

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
