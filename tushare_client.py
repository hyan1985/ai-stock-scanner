"""
Tushare 数据客户端 — 最小化 API 调用，适用于 5000 积分级别账号
"""

import os
import time
import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from typing import Optional, Dict, List

import tushare as ts

from config import TUSHARE_TOKEN_ENV, STOCK_POOL, THS_SECTOR_MAP, BENCHMARKS


class TushareClient:
    def __init__(self, token: Optional[str] = None):
        token = token or os.environ.get(TUSHARE_TOKEN_ENV, "")
        if not token:
            raise ValueError(
                f"未找到 Tushare token。请设置环境变量 {TUSHARE_TOKEN_ENV} "
                "或传入 token 参数。获取: https://tushare.pro"
            )
        ts.set_token(token)
        self.pro = ts.pro_api()
        self._cache: Dict[str, pd.DataFrame] = {}

    @staticmethod
    def _shanghai_today() -> datetime.date:
        """GitHub Actions 默认 UTC，必须用北京时间判断「今天」。"""
        return datetime.datetime.now(ZoneInfo("Asia/Shanghai")).date()

    def _get_latest_trade_date(self) -> str:
        """返回本次扫描应使用的 A 股交易日。

        - 如果是交易日且北京时间 15:00 后（Tushare 已收全日线）→ 取今天
        - 如果是非交易日（周末/假日）→ 取最近一个交易日
        - 通过 trade_cal 查询，从近到远逐日试探，要求 ≥90% 标的已有日线
        """
        today = self._shanghai_today()
        start = today - datetime.timedelta(days=10)
        cache_key = f"td_{start.strftime('%Y%m%d')}_{today.strftime('%Y%m%d')}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        codes = self.get_all_stock_codes()
        min_coverage = max(1, int(len(codes) * 0.9))
        probe_codes = ",".join(codes[:20])  # 单次请求覆盖多板块样本

        try:
            cal = self.pro.trade_cal(
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=today.strftime("%Y%m%d"),
                is_open="1",
            )
            if cal is not None and not cal.empty:
                dates = sorted(cal["cal_date"].astype(str).tolist(), reverse=True)
                for d in dates:
                    try:
                        probe = self.pro.daily(
                            ts_code=probe_codes,
                            start_date=d,
                            end_date=d,
                        )
                        if probe is None or probe.empty:
                            continue
                        probe.columns = probe.columns.str.lower()
                        got = probe["ts_code"].nunique()
                        if got >= min_coverage:
                            self._cache[cache_key] = d
                            return d
                    except Exception:
                        continue
        except Exception:
            pass

        # 回退：只用星期几估算（仍按北京时间）
        if today.weekday() == 5:
            today -= datetime.timedelta(days=1)
        elif today.weekday() == 6:
            today -= datetime.timedelta(days=2)
        return today.strftime("%Y%m%d")

    def get_all_stock_codes(self) -> List[str]:
        codes = []
        for tier in STOCK_POOL.values():
            for s in tier["stocks"]:
                codes.append(s["code"])
        return codes

    def get_stock_name_map(self) -> Dict[str, str]:
        return {
            s["code"]: s["name"]
            for tier in STOCK_POOL.values()
            for s in tier["stocks"]
        }

    # ------------------------------------------------------------
    # 核心：一次拉取所有股票的日线 + 量比
    # ------------------------------------------------------------
    def get_daily_data(
        self, trade_date: Optional[str] = None, lookback_days: int = 40
    ) -> pd.DataFrame:
        codes = self.get_all_stock_codes()
        if trade_date is None:
            trade_date = self._get_latest_trade_date()

        end_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=lookback_days + 15)
        start_date = start_dt.strftime("%Y%m%d")

        cache_key = f"daily_{start_date}_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 一次拉取全部29只股票
        all_codes = ",".join(codes)
        df = self.pro.daily(
            ts_code=all_codes,
            start_date=start_date,
            end_date=trade_date,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df.columns = df.columns.str.lower()
        df["trade_date"] = df["trade_date"].astype(str)
        df = df.sort_values(["ts_code", "trade_date"])

        # 自己计算量比：当日成交量 / 前5日均量
        df["volume_ratio"] = df.groupby("ts_code")["vol"].transform(
            lambda x: x / x.rolling(5, min_periods=1).mean().shift(1)
        )
        df["volume_ratio"] = df["volume_ratio"].fillna(1.0).clip(0.1, 10)

        self._cache[cache_key] = df
        return df

    # ------------------------------------------------------------
    # 资金流向 — 一次拉取
    # ------------------------------------------------------------
    def get_moneyflow(
        self, trade_date: Optional[str] = None, lookback_days: int = 5
    ) -> pd.DataFrame:
        codes = self.get_all_stock_codes()
        if trade_date is None:
            trade_date = self._get_latest_trade_date()

        end_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=lookback_days + 5)
        start_date = start_dt.strftime("%Y%m%d")

        cache_key = f"mf_{start_date}_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        all_codes = ",".join(codes)
        try:
            df = self.pro.moneyflow(
                ts_code=all_codes,
                start_date=start_date,
                end_date=trade_date,
            )
            if df is not None and not df.empty:
                df.columns = df.columns.str.lower()
                df["trade_date"] = df["trade_date"].astype(str)
                self._cache[cache_key] = df
                return df
        except Exception:
            pass

        return pd.DataFrame()

    # ------------------------------------------------------------
    # 同花顺板块指数 — 板块量能从真实板块指数拉取
    # ------------------------------------------------------------
    def get_ths_daily(
        self, trade_date: Optional[str] = None, lookback_days: int = 40
    ) -> pd.DataFrame:
        if trade_date is None:
            trade_date = self._get_latest_trade_date()

        end_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=lookback_days + 15)
        start_date = start_dt.strftime("%Y%m%d")

        cache_key = f"ths_{start_date}_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        all_ths = ",".join(THS_SECTOR_MAP.values())
        try:
            df = self.pro.ths_daily(
                ts_code=all_ths,
                start_date=start_date,
                end_date=trade_date,
            )
            if df is not None and not df.empty:
                df.columns = df.columns.str.lower()
                df["trade_date"] = df["trade_date"].astype(str)
                df = df.sort_values(["ts_code", "trade_date"])

                # 计算板块量比
                df["volume_ratio"] = df.groupby("ts_code")["vol"].transform(
                    lambda x: x / x.rolling(5, min_periods=1).mean().shift(1)
                )
                df["volume_ratio"] = df["volume_ratio"].fillna(1.0).clip(0.1, 10)

                self._cache[cache_key] = df
                return df
        except Exception as e:
            print(f"  [警告] 同花顺板块数据获取失败: {e}")

        return pd.DataFrame()

    # ------------------------------------------------------------
    # A股大盘指数 — 上证、深证、创业板、科创50
    # ------------------------------------------------------------
    def get_index_daily(
        self, trade_date: Optional[str] = None, lookback_days: int = 25
    ) -> pd.DataFrame:
        if trade_date is None:
            trade_date = self._get_latest_trade_date()

        end_dt = datetime.datetime.strptime(trade_date, "%Y%m%d")
        start_dt = end_dt - datetime.timedelta(days=lookback_days + 10)
        start_date = start_dt.strftime("%Y%m%d")

        cache_key = f"idx_{start_date}_{trade_date}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # index_daily 不支持逗号分隔多代码，逐个拉取后合并
        frames = []
        for code in BENCHMARKS.values():
            try:
                df = self.pro.index_daily(
                    ts_code=code,
                    start_date=start_date,
                    end_date=trade_date,
                )
                if df is not None and not df.empty:
                    df.columns = df.columns.str.lower()
                    df["trade_date"] = df["trade_date"].astype(str)
                    df = df.sort_values(["ts_code", "trade_date"])
                    frames.append(df)
            except Exception as e:
                print(f"  [警告] 指数 {code} 数据获取失败: {e}")

        if frames:
            result = pd.concat(frames, ignore_index=True)
            self._cache[cache_key] = result
            return result

        return pd.DataFrame()
