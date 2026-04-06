#!/usr/bin/env python3
"""
장 마감 후 수급 알림
16:30 KST 실행 - 당일 거래대금 상위 종목의 외인·기관 확정 순매수 발송
"""

import os
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
KIS_APP_KEY        = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET     = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL       = "https://openapi.koreainvestment.com:9443"

FIXED_TICKERS = [("005930", "삼성전자"), ("000660", "SK하이닉스")]
SKIP_STOCKS   = ["KODEX","TIGER","KINDEX","ARIRANG","HANARO","KBSTAR",
                 "인버스","레버리지","ETN","리츠","채권","선물"]


# ── KIS 토큰 ──────────────────────────────────────────────────────

def get_token() -> str:
    res = requests.post(
        f"{KIS_BASE_URL}/oauth2/tokenP",
        json={"grant_type": "client_credentials",
              "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
        timeout=10,
    )
    return res.json().get("access_token", "")


# ── 거래대금 상위 종목 (티커+이름) ────────────────────────────────

def get_top_tickers(token: str, n: int = 10) -> list:
    """거래대금 상위 n개 (ticker, name) 반환, 고정 종목 제외"""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": "FHPST01710000", "custtype": "P",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000", "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
    }
    try:
        data = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=headers, params=params, timeout=10,
        ).json()
    except Exception as e:
        print(f"  거래대금 조회 실패: {e}")
        return []

    fixed_codes = {t for t, _ in FIXED_TICKERS}
    result = []
    for item in data.get("output", []):
        code = item.get("mksc_shrn_iscd", "")
        name = item.get("hts_kor_isnm", "")
        if not code or not name:
            continue
        if any(k in name for k in SKIP_STOCKS):
            continue
        if code in fixed_codes:
            continue
        result.append((code, name))
        if len(result) >= n:
            break
    return result


# ── 당일 외인·기관 순매수 조회 ────────────────────────────────────

def get_today_investor(token: str, ticker: str) -> dict:
    """FHKST01010900 output[0] (당일 확정 데이터)"""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010900",
    }
    try:
        r = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
            headers=headers,
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker},
            timeout=10,
        )
        rows = r.json().get("output", [])
        if not rows:
            return {}
        row = rows[0]
        frgn = row.get("frgn_ntby_tr_pbmn", "")
        orgn = row.get("orgn_ntby_tr_pbmn", "")
        prsn = row.get("prsn_ntby_tr_pbmn", "")
        if not frgn:   # 아직 확정 안 됨
            return {}
        return {
            "frgn": int(frgn),
            "orgn": int(orgn or 0),
            "prsn": int(prsn or 0),
        }
    except Exception as e:
        print(f"  수급 조회 실패 ({ticker}): {e}")
        return {}


# ── 포맷 ─────────────────────────────────────────────────────────

def fmt_money(v: int) -> str:
    sign = "+" if v >= 0 else ""
    if abs(v) >= 1000000:
        return f"{sign}{v/1000000:.1f}조"
    if abs(v) >= 100:
        return f"{sign}{v/100:.0f}억"
    return f"{sign}{v}백만"


# ── 텔레그램 ─────────────────────────────────────────────────────

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  텔레그램 오류: {e}")


# ── 메인 ─────────────────────────────────────────────────────────

def run():
    now = datetime.now(KST)
    today = now.strftime("%Y%m%d")
    print(f"\n{'='*50}")
    print(f"  장 마감 수급 알림  {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    token = get_token()
    if not token:
        print("  토큰 발급 실패")
        return

    # 고정 + 거래대금 상위 10개
    dynamic = get_top_tickers(token, 10)
    fixed_codes = {t for t, _ in FIXED_TICKERS}
    stocks = FIXED_TICKERS + [(c, n) for c, n in dynamic if c not in fixed_codes]
    print(f"  조회 종목: {', '.join(n for _, n in stocks)}")

    lines = []
    for ticker, name in stocks:
        inv = get_today_investor(token, ticker)
        if not inv:
            print(f"  {name}: 데이터 없음")
            lines.append(f"{name:<10}  데이터 집계 중")
            time.sleep(0.3)
            continue

        frgn_str = fmt_money(inv["frgn"])
        orgn_str = fmt_money(inv["orgn"])
        frgn_arrow = "▲" if inv["frgn"] >= 0 else "▼"
        orgn_arrow = "▲" if inv["orgn"] >= 0 else "▼"
        line = f"{name:<10}  외인{frgn_arrow}{frgn_str}  기관{orgn_arrow}{orgn_str}"
        lines.append(line)
        print(f"  ✅ {line}")
        time.sleep(0.3)

    date_str = now.strftime("%m/%d")
    msg = (
        f"📊 {date_str} 장 마감 수급 확정\n"
        f"(거래대금 상위 종목 외인·기관 당일 순매수)\n"
        f"\n"
        + "\n".join(lines)
        + f"\n\n"
        f"📊 10:30 / 14:00 / 15:30 거래대금 상위 종목 실시간 업데이트합니다.\n"
        f"팔로우하고 정보 얻어가세요! 🙌"
    )
    tg_send(msg)
    print("  발송 완료")
    print(msg)


if __name__ == "__main__":
    run()
