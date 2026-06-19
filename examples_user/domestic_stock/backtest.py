"""
이동평균 교차 전략 백테스트
============================
과거 일별 종가 데이터에 이동평균 교차 전략을 적용해
매수/매도 시점을 찾아내고 backtest_log.csv 로 거래 내역을 저장한다.

실시간 자동매매(auto_trader.py)는 장이 열렸을 때만 신호가 발생하지만,
이 스크립트는 과거 데이터를 이용하므로 언제든 실행해 거래 기록을 만들 수 있다.
(과제의 '거래 기록' 제출용으로 활용 가능)
"""

import sys
import csv
import logging
from datetime import datetime, timedelta

import pandas as pd

sys.path.extend(['..', '.'])
import kis_auth as ka
from domestic_stock_functions import *

# auto_trader 와 동일한 설정
TARGET_STOCKS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035720": "카카오",
}
SHORT_WINDOW = 5
LONG_WINDOW = 20
ORDER_QTY = 1
LOG_FILE = "backtest_log.csv"

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def get_close_df(stock_code: str, days: int = 120) -> pd.DataFrame:
    """최근 일별 시세를 DataFrame(날짜, 종가)으로 반환."""
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days * 2)

    _, df = inquire_daily_itemchartprice(
        env_dv="demo",
        fid_cond_mrkt_div_code="J",
        fid_input_iscd=stock_code,
        fid_input_date_1=start_dt.strftime("%Y%m%d"),
        fid_input_date_2=end_dt.strftime("%Y%m%d"),
        fid_period_div_code="D",
        fid_org_adj_prc="1",
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.sort_values("stck_bsop_date").reset_index(drop=True)
    df["close"] = pd.to_numeric(df["stck_clpr"], errors="coerce")
    df = df.dropna(subset=["close"])
    return df[["stck_bsop_date", "close"]]


def backtest_stock(code: str, name: str, writer):
    """한 종목에 대해 과거 전체 구간의 교차 신호를 찾아 기록."""
    df = get_close_df(code)
    if df.empty or len(df) < LONG_WINDOW + 1:
        logger.info(f"[{name}] 데이터 부족 — 건너뜀")
        return 0

    df["short_ma"] = df["close"].rolling(SHORT_WINDOW).mean()
    df["long_ma"] = df["close"].rolling(LONG_WINDOW).mean()

    trade_count = 0
    position = False  # 현재 보유 중인지

    for i in range(1, len(df)):
        s_now, s_prev = df["short_ma"].iloc[i], df["short_ma"].iloc[i - 1]
        l_now, l_prev = df["long_ma"].iloc[i], df["long_ma"].iloc[i - 1]

        if pd.isna(s_prev) or pd.isna(l_prev):
            continue

        date = df["stck_bsop_date"].iloc[i]
        price = int(df["close"].iloc[i])

        # 골든크로스 -> 매수 (미보유 시에만)
        if s_prev <= l_prev and s_now > l_now and not position:
            writer.writerow([date, code, name, "BUY", price, ORDER_QTY])
            logger.info(f"[{name}] {date}  🔵 매수  {price:,}원")
            position = True
            trade_count += 1

        # 데드크로스 -> 매도 (보유 시에만)
        elif s_prev >= l_prev and s_now < l_now and position:
            writer.writerow([date, code, name, "SELL", price, ORDER_QTY])
            logger.info(f"[{name}] {date}  🔴 매도  {price:,}원")
            position = False
            trade_count += 1

    return trade_count


def run():
    # 시세 조회를 위해 인증 (모의투자)
    ka.auth(svr="vps", product="01")
    logger.info("백테스트 시작 — 이동평균 교차 전략")
    logger.info(f"대상: {', '.join(TARGET_STOCKS.values())}")
    logger.info(f"전략: {SHORT_WINDOW}일/{LONG_WINDOW}일 이동평균 교차")
    logger.info("=" * 50)

    with open(LOG_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["날짜", "종목코드", "종목명", "신호", "가격", "수량"])

        total = 0
        for code, name in TARGET_STOCKS.items():
            total += backtest_stock(code, name, writer)

    logger.info("=" * 50)
    logger.info(f"총 {total}건의 거래 신호를 {LOG_FILE} 에 저장했습니다.")


if __name__ == "__main__":
    run()
