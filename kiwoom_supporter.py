#!/usr/bin/env python3
"""
키움증권 서포터즈 2기 - 장중 거래대금 상위 실시간 포스터
KIS API 기반 실시간 데이터

스케줄:
  10:30 KST - 오전 거래대금 상위  4종목
  14:00 KST - 오후 거래대금 상위  3종목
  15:30 KST - 마감 직전 상위      3종목  (합계 10개)

실행:
  python kiwoom_supporter.py --now         # 현재 시간 기준 자동 배치
  python kiwoom_supporter.py --midday      # 10:30 배치 강제 실행
  python kiwoom_supporter.py --afternoon   # 14:00 배치 강제 실행
  python kiwoom_supporter.py --close       # 15:30 배치 강제 실행
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

# ── 배치 설정 ─────────────────────────────────────────────────────
BATCH_SIZE = {
    "midday":    4,   # 10:30
    "afternoon": 3,   # 14:00
    "close":     3,   # 15:30
}

BATCH_LABEL = {
    "midday":    "오전 거래대금 상위",
    "afternoon": "오후 거래대금 상위",
    "close":     "마감 직전 상위",
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


def get_top_stocks(n: int) -> list:
    """거래대금 상위 n개 종목 조회"""
    token = get_token()
    url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey":    KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id":     "FHPST01710000",
        "custtype":  "P",
    }
    params = {
        "FID_COND_MRK_DIV_CODE":   "J",
        "FID_COND_SCR_DIV_CODE":   "20171",
        "FID_INPUT_ISCD":          "0000",
        "FID_DIV_CLS_CODE":        "0",
        "FID_BLNG_CLS_CODE":       "0",
        "FID_TRGT_CLS_CODE":       "111111111",
        "FID_TRGT_EXLS_CLS_CODE":  "000000",
        "FID_INPUT_PRICE_1":       "",
        "FID_INPUT_PRICE_2":       "",
        "FID_VOL_CNT":             "",
        "FID_INPUT_DATE_1":        "",
    }
    res  = requests.get(url, headers=headers, params=params, timeout=10)
    data = res.json()

    stocks = []
    for item in data.get("output", [])[:n]:
        try:
            stocks.append({
                "ticker":      item.get("mksc_shrn_iscd", ""),
                "name":        item.get("hts_kor_isnm", ""),
                "price":       int(item.get("stck_prpr",   0)),
                "change":      int(item.get("prdy_vrss",   0)),
                "change_rate": float(item.get("prdy_ctrt", 0)),
                "volume":      int(item.get("acml_vol",    0)),
                "tr_value":    int(item.get("acml_tr_pbmn",0)),
                "open":        int(item.get("stck_oprc",   0)),
                "high":        int(item.get("stck_hgpr",   0)),
                "low":         int(item.get("stck_lwpr",   0)),
                "w52_high":    0,
                "w52_low":     0,
            })
        except Exception:
            continue
    return stocks


# ── 유가 / 환율 ───────────────────────────────────────────────────

def get_macro() -> dict:
    """WTI 유가 + 달러 환율 조회"""
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
    rate  = info["change_rate"]
    parts = []

    if batch == "midday" and info["open"] > 0 and info["change"] != 0:
        prev_close = info["price"] - info["change"]
        if prev_close > 0:
            gap_pct = (info["open"] - prev_close) / prev_close * 100
            if gap_pct >= 1:
                parts.append(f"갭업 +{gap_pct:.1f}%")
            elif gap_pct <= -1:
                parts.append(f"갭다운 {gap_pct:.1f}%")

    if   rate >= 3:  parts.append("강세 흐름")
    elif rate >= 1:  parts.append("상승 흐름")
    elif rate <= -3: parts.append("약세 흐름")
    elif rate <= -1: parts.append("하락 흐름")
    else:            parts.append("보합권")

    return " · ".join(parts) if parts else "장중 모니터링 중"


# ── 포스트 빌드 ───────────────────────────────────────────────────

def build_post(info: dict, batch: str, rank: int, now: datetime, macro: dict = None) -> str:
    name     = info["name"]
    ticker   = info["ticker"]
    arrow    = "▲" if info["change_rate"] > 0 else ("▼" if info["change_rate"] < 0 else "─")
    sign     = "+" if info["change_rate"] > 0 else ""
    icon     = "📈" if info["change_rate"] >= 0 else "📉"
    comment  = get_comment(info, batch)
    time_str = now.strftime("%H:%M")

    macro_line = ""
    if macro:
        parts = []
        if macro.get("oil"):    parts.append(f"WTI ${macro['oil']}")
        if macro.get("usdkrw"): parts.append(f"달러 {macro['usdkrw']:,}원")
        if parts:
            macro_line = "  /  ".join(parts) + "\n"

    return (
        f"{icon} {name}  거래대금 {rank}위  {time_str}\n"
        f"\n"
        f"현재가    {fmt_price(info['price'])}  {arrow} {sign}{info['change_rate']:.2f}%\n"
        f"거래대금  {fmt_value(info['tr_value'])}\n"
        f"거래량    {fmt_vol(info['volume'])}\n"
        f"{macro_line}"
        f"\n"
        f"{comment}\n"
        f"\n"
        f"#{name} #키움 #장중정보 #거래대금상위"
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
    now   = datetime.now(KST)
    label = BATCH_LABEL.get(batch, batch)
    n     = BATCH_SIZE.get(batch, 3)

    print(f"\n{'='*50}")
    print(f"  키움 서포터즈  {now.strftime('%Y-%m-%d %H:%M')} KST  {label}")
    print(f"{'='*50}")

    # 유가 + 환율
    macro = get_macro()
    oil_str = f"WTI  ${macro['oil']}" if macro.get("oil") else ""
    usd_str = f"달러  {macro['usdkrw']:,}원" if macro.get("usdkrw") else ""
    macro_line = "  /  ".join(filter(None, [oil_str, usd_str]))

    header = f"📊 키움 서포터즈  {now.strftime('%m/%d %H:%M')} KST  {label}"
    if macro_line:
        header += f"\n{macro_line}"
    tg_send(header)
    time.sleep(0.3)

    # 거래대금 상위 종목 조회
    try:
        stocks = get_top_stocks(n)
        print(f"  거래대금 상위 {len(stocks)}종목 조회 완료")
    except Exception as e:
        print(f"  ⚠️  종목 조회 실패: {e}")
        tg_send("⚠️ 거래대금 상위 종목 조회 실패")
        return

    for rank, stock in enumerate(stocks, start=1):
        try:
            post = build_post(stock, batch, rank, now, macro)
            print(f"  ✅ [{rank}위] {stock['name']}  {stock['price']:,}원  {stock['change_rate']:+.2f}%")
        except Exception as e:
            print(f"  ⚠️  {stock.get('name','?')} 오류: {e}")
            post = f"📊 {stock.get('name','?')}  {now.strftime('%H:%M')}\n\n데이터 오류\n\n#장중정보"
        tg_send(post)
        time.sleep(0.5)

    print(f"  완료 ({n}개 전송)")


def detect_batch() -> str:
    """KST 기준 배치 자동 감지"""
    now = datetime.now(KST)
    t   = now.hour * 60 + now.minute
    if   t < 13 * 60: return "midday"
    elif t < 15 * 60: return "afternoon"
    else:             return "close"


# ── 진입점 ────────────────────────────────────────────────────────

if __name__ == "__main__":
    if   "--midday"    in sys.argv: run_batch("midday")
    elif "--afternoon" in sys.argv: run_batch("afternoon")
    elif "--close"     in sys.argv: run_batch("close")
    elif "--now" in sys.argv or "-n" in sys.argv:
        run_batch(detect_batch())
    else:
        import schedule as sch
        print("🚀 키움 서포터즈  |  10:30 → 14:00 → 15:30")
        sch.every().day.at("10:30").do(run_batch, "midday")
        sch.every().day.at("14:00").do(run_batch, "afternoon")
        sch.every().day.at("15:30").do(run_batch, "close")
        while True:
            sch.run_pending()
            time.sleep(30)
