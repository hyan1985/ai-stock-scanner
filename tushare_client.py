"""
Tushare 数据客户端 — 最小化 API 调用，适用于 5000 积分级别账号
"""

import os
import time
import datetime
import pandas as pd
import numpy as np
from typing import Optional, Dict, List

import tushare as ts

from config import TUSHARE_TOKEN_ENV, STOCK_POOL, THS_SECTOR_MAP


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

    def _get_latest_trade_date(self) -> str:
        today = datetime.date.today()
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
