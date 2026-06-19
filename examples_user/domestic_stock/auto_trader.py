"""
한국투자증권 Open API 기반 자동매매 시스템
=========================================
전략: 이동평균선 교차 (골든크로스 / 데드크로스)

- 단기 이동평균선(기본 5일)이 장기 이동평균선(기본 20일)을 아래에서 위로 돌파(골든크로스) -> 매수
- 단기 이동평균선이 장기 이동평균선을 위에서 아래로 돌파(데드크로스) -> 매도

[실행 모드]
1. 일반 모드 (기본)
     uv run python auto_trader.py
   장이 열려있는 동안 60초마다 신호를 확인하고, 신호 발생 시 모의 주문을 낸다.

2. 테스트 모드
     uv run python auto_trader.py --test
   전략 동작과 주문/기록 흐름을 즉시 검증하기 위해, 각 종목에 매수 주문을
   1건씩 강제로 실행하고 거래 내역을 기록한다. (모의투자 거래 기록 확보용)

※ 모의투자(svr="vps") 환경에서 동작합니다.
"""

import sys
import os
import csv
import time
import logging
from datetime import datetime, timedelta

import pandas as pd

# 샘플 저장소의 공통 인증/함수 모듈 경로 추가
sys.path.extend(['..', '.'])
import kis_auth as ka
from domestic_stock_functions import *

# ----------------------------------------------------------------------------
# 설정값 (필요에 따라 자유롭게 수정)
# ----------------------------------------------------------------------------
TARGET_STOCKS = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "035720": "카카오",
}

SHORT_WINDOW = 5      # 단기 이동평균 기간(일)
LONG_WINDOW = 20      # 장기 이동평균 기간(일)
ORDER_QTY = 1         # 신호 발생 시 주문 수량(주)
LOOP_INTERVAL = 60    # 반복 주기(초)

LOG_FILE = "trade_log.csv"

# 장 운영 시간 (한국시간 09:00 ~ 15:30)
MARKET_OPEN = (9, 0)
MARKET_CLOSE = (15, 30)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 보조 함수
# ----------------------------------------------------------------------------
def is_market_open() -> bool:
    """현재 시각이 장 운영 시간(평일 09:00~15:30)인지 판단."""
    now = datetime.now()
    if now.weekday() >= 5:  # 토(5), 일(6)
        return False
    open_t = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)
    close_t = now.replace(hour=MARKET_CLOSE[0], minute=MARKET_CLOSE[1], second=0, microsecond=0)
    return open_t <= now <= close_t


def get_close_prices(stock_code: str, days: int = 40) -> pd.Series:
    """최근 일별 종가 시계열을 반환한다."""
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=days * 2)  # 휴일 고려해 넉넉히

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
        return pd.Series(dtype="float64")

    df = df.sort_values("stck_bsop_date")
    closes = pd.to_numeric(df["stck_clpr"], errors="coerce").dropna()
    closes.index = df["stck_bsop_date"].values[: len(closes)]
    return closes


def detect_signal(closes: pd.Series) -> str:
    """
    이동평균 교차 신호를 판단한다.
    반환값: "BUY"(골든크로스) / "SELL"(데드크로스) / "HOLD"(신호 없음)
    """
    if len(closes) < LONG_WINDOW + 1:
        return "HOLD"

    short_ma = closes.rolling(SHORT_WINDOW).mean()
    long_ma = closes.rolling(LONG_WINDOW).mean()

    short_now, short_prev = short_ma.iloc[-1], short_ma.iloc[-2]
    long_now, long_prev = long_ma.iloc[-1], long_ma.iloc[-2]

    crossed_up = short_prev <= long_prev and short_now > long_now     # 골든크로스
    crossed_down = short_prev >= long_prev and short_now < long_now   # 데드크로스

    if crossed_up:
        return "BUY"
    if crossed_down:
        return "SELL"
    return "HOLD"


def get_current_price(stock_code: str) -> int:
    """현재가를 조회해 정수로 반환."""
    result = inquire_price(env_dv="demo", fid_cond_mrkt_div_code="J", fid_input_iscd=stock_code)
    if result is None or result.empty:
        return 0
    return int(result.iloc[0]["stck_prpr"])


def place_order(trenv, stock_code: str, signal: str, price: int) -> str:
    """
    모의투자 주문을 실행한다.
    signal 이 BUY 면 매수, SELL 이면 매도. 시장가(ord_dvsn='01') 주문.
    반환값: 주문 결과 메시지
    """
    ord_dv = "buy" if signal == "BUY" else "sell"
    try:
        result = order_cash(
            env_dv="demo",
            ord_dv=ord_dv,
            cano=trenv.my_acct,
            acnt_prdt_cd=trenv.my_prod,
            pdno=stock_code,
            ord_dvsn="01",           # 01: 시장가
            ord_qty=str(ORDER_QTY),
            ord_unpr="0",            # 시장가이므로 0
            excg_id_dvsn_cd="KRX",
        )
        try:
            odno = result.iloc[0].get("ODNO", "")
            return f"주문성공(주문번호:{odno})" if odno else "주문성공"
        except Exception:
            return "주문성공"
    except Exception as e:
        logger.error(f"주문 실패({stock_code}): {e}")
        return f"주문실패: {e}"


def log_trade(timestamp, stock_code, stock_name, signal, price, qty, status):
    """거래 내역을 CSV 파일에 한 줄 추가한다."""
    file_exists = os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["시각", "종목코드", "종목명", "신호", "주문가격", "수량", "결과"])
        writer.writerow([timestamp, stock_code, stock_name, signal, price, qty, status])


# ----------------------------------------------------------------------------
# 테스트 모드: 강제 주문으로 거래 기록 확보
# ----------------------------------------------------------------------------
def run_test():
    """각 종목에 매수 주문을 1건씩 강제 실행하여 거래 기록을 만든다."""
    ka.auth(svr="vps", product="01")
    trenv = ka.getTREnv()
    logger.info(f"[테스트 모드] 인증 완료 — 계좌번호: {trenv.my_acct}")
    logger.info("각 종목에 시장가 매수 주문을 1건씩 실행합니다.")
    logger.info("=" * 50)

    for code, name in TARGET_STOCKS.items():
        try:
            price = get_current_price(code)
            status = place_order(trenv, code, "BUY", price)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_trade(ts, code, name, "BUY", price, ORDER_QTY, status)
            logger.info(f"[{name}] 매수 주문 — 가격 {price:,}원 -> {status}")
            time.sleep(1)  # 호출 제한 회피
        except Exception as e:
            logger.error(f"[{name}] 주문 중 오류: {e}")

    logger.info("=" * 50)
    logger.info(f"테스트 주문 완료. 거래 기록은 {LOG_FILE} 에 저장되었습니다.")
    logger.info("모의투자 계좌의 '주문/체결 내역'에서도 확인할 수 있습니다.")


# ----------------------------------------------------------------------------
# 일반 모드: 실시간 자동매매 루프
# ----------------------------------------------------------------------------
def run():
    ka.auth(svr="vps", product="01")
    trenv = ka.getTREnv()
    logger.info(f"인증 완료 — 계좌번호: {trenv.my_acct}")
    logger.info(f"대상 종목: {', '.join(TARGET_STOCKS.values())}")
    logger.info(f"전략: {SHORT_WINDOW}일/{LONG_WINDOW}일 이동평균 교차")
    logger.info("=" * 50)

    last_signal = {code: "HOLD" for code in TARGET_STOCKS}

    while True:
        if not is_market_open():
            logger.info("장 운영 시간이 아닙니다. 60초 후 다시 확인합니다.")
            time.sleep(LOOP_INTERVAL)
            continue

        for code, name in TARGET_STOCKS.items():
            try:
                closes = get_close_prices(code)
                signal = detect_signal(closes)

                if signal == "HOLD" or signal == last_signal[code]:
                    logger.info(f"[{name}] 신호 없음 (HOLD)")
                    continue

                price = get_current_price(code)
                status = place_order(trenv, code, signal, price)
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                log_trade(ts, code, name, signal, price, ORDER_QTY, status)
                last_signal[code] = signal

                label = "매수" if signal == "BUY" else "매도"
                logger.info(f"[{name}] {label} 신호! 가격 {price:,}원 -> {status}")

            except Exception as e:
                logger.error(f"[{name}] 처리 중 오류: {e}")

        logger.info(f"{LOOP_INTERVAL}초 대기...")
        logger.info("-" * 50)
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    if "--test" in sys.argv:
        run_test()
    else:
        try:
            run()
        except KeyboardInterrupt:
            logger.info("사용자가 프로그램을 종료했습니다.")
