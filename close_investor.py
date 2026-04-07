#!/usr/bin/env python3
"""
장 마감 후 수급 알림
16:00 KST 실행 - 당일 거래대금 상위 종목 외인·기관 확정 순매수 + 연속 동향 + 섹터 + Claude 분석
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
ANTHROPIC_API_KEY  = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
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


# ── 거래대금 상위 종목 ─────────────────────────────────────────────

def get_top_tickers(token: str, n: int = 10) -> list:
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


# ── 당일 외인·기관 확정 순매수 + 연속 동향 ──────────────────────────

def get_today_investor(token: str, ticker: str) -> dict:
    """당일 확정 수급 + 외인 연속 순매수 일수 반환"""
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

        # 당일 확정 데이터 (첫 번째 비어있지 않은 row)
        frgn = orgn = prsn = None
        for row in rows:
            f = row.get("frgn_ntby_tr_pbmn", "")
            if f:
                frgn = int(f)
                orgn = int(row.get("orgn_ntby_tr_pbmn") or 0)
                prsn = int(row.get("prsn_ntby_tr_pbmn") or 0)
                break

        if frgn is None:
            return {}

        # 외인 연속 순매수/순매도 일수 계산
        consec = 0
        direction = None
        for row in rows:
            f = row.get("frgn_ntby_tr_pbmn", "")
            if not f:
                continue
            curr = 1 if int(f) > 0 else -1
            if direction is None:
                direction = curr
            if curr == direction:
                consec += 1
            else:
                break

        return {
            "frgn":   frgn,
            "orgn":   orgn,
            "prsn":   prsn,
            "consec": consec * (direction or 1),   # 양수=연속매수, 음수=연속매도
        }
    except Exception as e:
        print(f"  수급 조회 실패 ({ticker}): {e}")
        return {}


# ── 업종(섹터) 조회 ───────────────────────────────────────────────

def get_sector(token: str, ticker: str) -> str:
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": "FHKST01010100",
    }
    try:
        r = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker},
            timeout=10,
        )
        return r.json().get("output", {}).get("bstp_kor_isnm", "기타")
    except Exception:
        return "기타"


# ── Claude 수급 원인 분석 ─────────────────────────────────────────

def get_ai_analysis(stock_data: list) -> str:
    """외인·기관 수급 원인 분석 (상위 5종목 기준)"""
    if not ANTHROPIC_API_KEY or not stock_data:
        return ""
    try:
        def m(v):
            s = "+" if v >= 0 else ""
            return f"{s}{v/100:.0f}억" if abs(v) >= 100 else f"{s}{v}백만"

        items = []
        for d in stock_data[:5]:
            consec = d.get("consec", 0)
            consec_str = f"외인 {abs(consec)}일연속{'매수' if consec > 0 else '매도'}" if consec else ""
            items.append(
                f"{d['name']}: 외인{m(d['frgn'])} 기관{m(d['orgn'])} {consec_str}"
            )

        prompt = (
            f"오늘 한국 주식시장 거래대금 상위 종목의 외인·기관 수급 데이터입니다.\n\n"
            + "\n".join(items)
            + "\n\n"
            f"위 수급 데이터를 보고 다음을 3~4문장으로 분석해주세요:\n"
            f"1. 외인·기관이 오늘 매수/매도한 가능한 이유 (글로벌 매크로, 섹터 이슈, 수급 트렌드)\n"
            f"2. 연속 매수/매도 흐름에서 읽히는 시사점\n"
            f"3. 내일 장에 대한 시사점\n\n"
            f"규칙: 전문가 격식체(~입니다/~습니다), 수치 근거 명시, 질문·이모지 금지"
        )

        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":    "claude-haiku-4-5-20251001",
                "max_tokens": 800,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=20,
        )
        return res.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"  Claude 분석 실패: {e}")
        return ""


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
    print(f"\n{'='*50}")
    print(f"  장 마감 수급 알림  {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    token = get_token()
    if not token:
        print("  토큰 발급 실패")
        return

    dynamic     = get_top_tickers(token, 10)
    fixed_codes = {t for t, _ in FIXED_TICKERS}
    stocks      = FIXED_TICKERS + [(c, n) for c, n in dynamic if c not in fixed_codes]
    print(f"  조회 종목: {', '.join(n for _, n in stocks)}")

    lines      = []
    stock_data = []   # Claude 분석용
    sector_map : dict = {}   # sector → [name, ...]

    for ticker, name in stocks:
        inv = get_today_investor(token, ticker)
        if not inv:
            print(f"  {name}: 데이터 없음")
            lines.append(f"{name:<10}  집계 중")
            time.sleep(0.3)
            continue

        # 섹터 조회
        sector = get_sector(token, ticker)
        time.sleep(0.2)

        sector_map.setdefault(sector, []).append({"name": name, "frgn": inv["frgn"]})

        # 연속 동향 표시
        consec = inv.get("consec", 0)
        consec_str = ""
        if abs(consec) >= 2:
            consec_str = f"  {'🔥' if consec >= 3 else '📌'} 외인 {abs(consec)}일연속{'매수' if consec > 0 else '매도'}"

        frgn_arrow = "▲" if inv["frgn"] >= 0 else "▼"
        orgn_arrow = "▲" if inv["orgn"] >= 0 else "▼"
        line = (
            f"{name:<10}  "
            f"외인{frgn_arrow}{fmt_money(inv['frgn'])}  "
            f"기관{orgn_arrow}{fmt_money(inv['orgn'])}"
            f"{consec_str}"
        )
        lines.append(line)
        stock_data.append({**inv, "name": name, "sector": sector})
        print(f"  ✅ {line}")
        time.sleep(0.3)

    # 주도 섹터 요약 (외인 순매수 기준 상위 3개)
    sector_frgn = {}
    for sec, items in sector_map.items():
        sector_frgn[sec] = sum(i["frgn"] for i in items)
    top_sectors = sorted(sector_frgn.items(), key=lambda x: -x[1])[:3]
    sector_line = "  ".join(
        f"{s}({'▲' if v>=0 else '▼'})" for s, v in top_sectors
    ) if top_sectors else ""

    # Claude 수급 원인 분석
    ai_text = get_ai_analysis(stock_data)

    date_str = now.strftime("%m/%d")
    msg = (
        f"📊 {date_str} 장 마감 수급 확정\n"
        f"\n"
        + "\n".join(lines)
        + f"\n"
    )
    if sector_line:
        msg += f"\n🏭 오늘의 주도 섹터\n{sector_line}\n"
    if ai_text:
        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n{ai_text}\n"
    msg += (
        f"\n📊 10:30 / 14:00 / 15:30 거래대금 상위 종목 실시간 업데이트합니다.\n"
        f"팔로우하고 정보 얻어가세요! 🙌"
    )

    tg_send(msg)
    print("  발송 완료")
    print(msg)


if __name__ == "__main__":
    run()
