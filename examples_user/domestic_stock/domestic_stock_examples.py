import sys
import logging
import pandas as pd

sys.path.extend(['..', '.'])
import kis_auth as ka
from domestic_stock_functions import *

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 모의투자 인증
ka.auth(svr="vps", product="01")
trenv = ka.getTREnv()

# 인증 결과 확인
print("=" * 40)
print("✅ 인증 성공!")
print("계좌번호:", trenv.my_acct)
print("=" * 40)