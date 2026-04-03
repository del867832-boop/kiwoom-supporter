#!/usr/bin/env python3
"""
키움증권 서포터즈 2기 - 장중 실시간 종목 정보 포스터
KIS API 기반 실시간 데이터

스케줄:
  09:05 KST - 시초가 분석  3종목
  10:30 KST - 오전 흐름    4종목
  14:00 KST - 오후 수급    3종목  (합계 10개)

실행:
  python kiwoom_supporter.py --now         # 현재 시간 기준 자동 배치
  python kiwoom_supporter.py --morning     # 09:05 배치 강제 실행
  python kiwoom_supporter.py --midday      # 10:30 배치 강제 실행
  python kiwoom_supporter.py --afternoon   # 14:00 배치 강제 실행
"""

import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

# ── 환경변수 ──────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
KIS_APP_KEY        = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET     = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL       = "https://openapi.koreainvestment.com:9443"

# ── 종목 배치 배분 ─────────────────────────────────────────────────
BATCH_STOCKS = {
    "morning":   ["005930", "000660", "005380"],
    "midday":    ["000270", "035420", "373220", "207940"],
    "afternoon": ["105560", "055550", "051910"],
}

NAME_MAP = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "005380": "현대차",
    "000270": "기아",
    "035420": "NAVER",
    "373220": "LG에너지솔루션",
    "207940": "삼성바이오로직스",
    "105560": "KB금융",
    "055550": "신한지주",
    "051910": "LG화학",
}

SECTOR_MAP = {
    "005930": "반도체",
    "000660": "반도체",
    "005380": "자동차",
    "000270": "자동차",
    "035420": "IT플랫폼",
    "373220": "2차전지",
    "207940": "바이오",
    "105560": "금융",
    "055550": "금융",
    "051910": "화학",
}

BATCH_LABEL = {
    "morning":   "시초가 분석",
    "midday":    "오전 흐름",
    "afternoon": "오후 수급",
}

# ── KIS API ────────────────────────────────────────────────────────

_token_cache: dict = {"token": None, "expires": None}


def get_token() -> str:
    now = datetime.now(KST)
    if _token_cache["token"] and _token_cache["expires"] and now < _token_cache["expires"]:
        return _token_cache["token"]

    url  = f"{KIS_BASE_URL}/oauth2/tokenP"
    body = {
        "grant_type": "client_credentials",
        "appkey":     KIS_APP_KEY,
        "appsecret":  KIS_APP_SECRET,
    }
    res  = requests.post(url, json=body, timeout=10)
    data = res.json()

    if "access_token" not in data:
        raise Exception(f"KIS 토큰 발급 실패: {data.get('msg1', data)}")

    _token_cache["token"]   = data["access_token"]
    _token_cache["expires"] = datetime.now(KST) + timedelta(hours=23)
    print("  KIS 토큰 발급 완료")
    return _token_cache["token"]


def get_stock_info(ticker: str) -> dict:
    """현재가 · 등락률 · 거래량 · 거래대금 · 52주 고저 조회"""
    token = get_token()
    url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         "FHKST01010100",
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd":         ticker,
    }
    res = requests.get(url, headers=headers, params=params, timeout=10)
    out = res.json().get("output", {})

    return {
        "price":       int(out.get("stck_prpr",    0)),
        "change":      int(out.get("prdy_vrss",    0)),
        "change_rate": float(out.get("prdy_ctrt",  0)),
        "volume":      int(out.get("acml_vol",     0)),
        "tr_value":    int(out.get("acml_tr_pbmn", 0)),
        "open":        int(out.get("stck_oprc",    0)),
        "high":        int(out.get("stck_hgpr",    0)),
        "low":         int(out.get("stck_lwpr",    0)),
        "w52_high":    int(out.get("w52_hgpr",     0)),
        "w52_low":     int(out.get("w52_lwpr",     0)),
    }


# ── 유가 / 환율 ───────────────────────────────────────────────────

def get_macro() -> dict:
    """WTI 유가 + 달러 환율 조회 (yfinance)"""
    try:
        import yfinance as yf
        wti = yf.Ticker("CL=F").fast_info
        usd = yf.Ticker("USDKRW=X").fast_info
        return {
            "oil":    round(wti.last_price, 1),
            "usdkrw": int(usd.last_price),
        }
    except Exception:
        return {"oil": None, "usdkrw": None}


# ── 포맷 헬퍼 ─────────────────────────────────────────────────────

def fmt_price(v: int) -> str:
    return f"{v:,}원"

def fmt_value(v: int) -> str:
    if v >= 1_000_000_000_000: return f"{v/1_000_000_000_000:.1f}조"
    if v >= 100_000_000:       return f"{v/100_000_000:.0f}억"
    return f"{v:,}원"

def fmt_vol(v: int) -> str:
    if v >= 10_000: return f"{v/10_000:.1f}만주"
    return f"{v:,}주"


# ── 한줄 코멘트 ───────────────────────────────────────────────────

def get_comment(info: dict, batch: str) -> str:
    rate     = info["change_rate"]
    price    = info["price"]
    w52_high = info["w52_high"]
    w52_low  = info["w52_low"]
    parts    = []

    if batch == "morning" and info["open"] > 0 and info["change"] != 0:
        prev_close = info["price"] - info["change"]
        if prev_close > 0:
            gap_pct = (info["open"] - prev_close) / prev_close * 100
            if gap_pct >= 1:
                parts.append(f"갭업 +{gap_pct:.1f}%")
            elif gap_pct <= -1:
                parts.append(f"갭다운 {gap_pct:.1f}%")

    if w52_high > 0 and price >= w52_high * 0.98:
        parts.append("52주 신고가 근접")
    elif w52_low > 0 and price <= w52_low * 1.05:
        parts.append("52주 저점 근접")

    if   rate >= 3:  parts.append("강세 흐름")
    elif rate >= 1:  parts.append("상승 흐름")
    elif rate <= -3: parts.append("약세 흐름")
    elif rate <= -1: parts.append("하락 흐름")
    else:            parts.append("보합권")

    return " · ".join(parts) if parts else "장중 모니터링 중"


# ── 포스트 빌드 ───────────────────────────────────────────────────

def build_post(ticker: str, info: dict, batch: str, now: datetime, macro: dict = None) -> str:
    name       = NAME_MAP.get(ticker, ticker)
    sector     = SECTOR_MAP.get(ticker, "")
    arrow      = "▲" if info["change_rate"] > 0 else ("▼" if info["change_rate"] < 0 else "─")
    sign       = "+" if info["change_rate"] > 0 else ""
    icon       = "📈" if info["change_rate"] >= 0 else "📉"
    comment    = get_comment(info, batch)
    sector_tag = f"#{sector} " if sector else ""
    time_str   = now.strftime("%H:%M")

    macro_line = ""
    if macro:
        parts = []
        if macro.get("oil"):    parts.append(f"WTI ${macro['oil']}")
        if macro.get("usdkrw"): parts.append(f"달러 {macro['usdkrw']:,}원")
        if parts:
            macro_line = "  /  ".join(parts) + "\n"

    return (
        f"{icon} {name}  {time_str} 현재\n"
        f"\n"
        f"현재가    {fmt_price(info['price'])}  {arrow} {sign}{info['change_rate']:.2f}%\n"
        f"거래대금  {fmt_value(info['tr_value'])}\n"
        f"거래량    {fmt_vol(info['volume'])}\n"
        f"{macro_line}"
        f"\n"
        f"{comment}\n"
        f"\n"
        f"#{name} {sector_tag}#키움 #장중정보"
    )


# ── 텔레그램 ──────────────────────────────────────────────────────

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        print()
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"  텔레그램 오류: {e}")


# ── 배치 실행 ─────────────────────────────────────────────────────

def run_batch(batch: str):
    now     = datetime.now(KST)
    label   = BATCH_LABEL.get(batch, batch)
    tickers = BATCH_STOCKS.get(batch, [])

    print(f"\n{'='*50}")
    print(f"  키움 서포터즈  {now.strftime('%Y-%m-%d %H:%M')} KST  {label}")
    print(f"{'='*50}")

    macro   = get_macro()
    oil_str = f"WTI  ${macro['oil']}" if macro["oil"] else ""
    usd_str = f"달러  {macro['usdkrw']:,}원" if macro["usdkrw"] else ""
    macro_line = "  /  ".join(filter(None, [oil_str, usd_str]))

    header = f"📊 키움 서포터즈  {now.strftime('%m/%d %H:%M')} KST  {label}"
    if macro_line:
        header += f"\n{macro_line}"
    tg_send(header)
    time.sleep(0.3)

    for ticker in tickers:
        name = NAME_MAP.get(ticker, ticker)
        try:
            info = get_stock_info(ticker)
            post = build_post(ticker, info, batch, now, macro)
            print(f"  ✅ {name}  {info['price']:,}원  {info['change_rate']:+.2f}%")
        except Exception as e:
            print(f"  ⚠️  {name} 오류: {e}")
            post = (
                f"📊 {name}  {now.strftime('%H:%M')} 현재\n\n"
                f"데이터 조회 중 오류가 발생했습니다.\n\n"
                f"#{name} #장중정보"
            )
        tg_send(post)
        time.sleep(0.5)

    print(f"  완료 ({len(tickers)}개 전송)")


def detect_batch() -> str:
    """KST 기준 배치 자동 감지"""
    now = datetime.now(KST)
    t   = now.hour * 60 + now.minute
    if   t < 10 * 60: return "morning"
    elif t < 13 * 60: return "midday"
    else:             return "afternoon"


# ── 진입점 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if   "--morning"   in sys.argv: run_batch("morning")
    elif "--midday"    in sys.argv: run_batch("midday")
    elif "--afternoon" in sys.argv: run_batch("afternoon")
    elif "--now" in sys.argv or "-n" in sys.argv:
        run_batch(detect_batch())
    else:
        import schedule as sch
        print("🚀 키움 서포터즈  |  장중 자동 포스팅")
        print("   09:05 시초가 → 10:30 오전 → 14:00 오후\n")
        sch.every().day.at("09:05").do(run_batch, "morning")
        sch.every().day.at("10:30").do(run_batch, "midday")
        sch.every().day.at("14:00").do(run_batch, "afternoon")
        while True:
            sch.run_pending()
            time.sleep(30)
