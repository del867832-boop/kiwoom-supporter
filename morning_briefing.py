#!/usr/bin/env python3
"""
키움 서포터즈 - 장전 시황 브리핑
매일 08:50 KST 발송
미국 지수 + 환율/원자재(Stooq) + DART 주요 공시 + Claude AI 뷰
"""

import os
import csv
import io
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
DART_API_KEY       = os.getenv("DART_API_KEY")
ANTHROPIC_API_KEY  = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
KIS_APP_KEY        = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET     = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL       = "https://openapi.koreainvestment.com:9443"


# ── Stooq 데이터 조회 ─────────────────────────────────────────────

def get_stooq(symbol: str) -> dict:
    """Stooq 현재가 + 전일 종가(Prev) → 변화율 계산"""
    try:
        url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcvp&h&e=csv"
        res = requests.get(url, timeout=10,
                           headers={"User-Agent": "Mozilla/5.0"})
        rows = list(csv.DictReader(io.StringIO(res.text)))
        if not rows:
            return {}
        row   = rows[0]
        close = row.get("Close", "N/D")
        prev  = row.get("Prev",  "N/D")
        if not close or not prev or close == "N/D" or prev == "N/D":
            return {}
        close, prev = float(close), float(prev)
        return {
            "close":      close,
            "change_pct": (close - prev) / prev * 100,
        }
    except Exception as e:
        print(f"  Stooq 실패 ({symbol}): {e}")
        return {}


def get_market_data() -> dict:
    """미국 지수 + 원자재 + 환율"""
    result = {k: get_stooq(v) for k, v in {
        "sp500":  "^spx",
        "nasdaq": "^ndx",
        "dow":    "^dji",
        "sox":    "smh.us",   # 필라델피아 반도체 ETF
        "usdkrw": "usdkrw",
    }.items()}

    # WTI·금: 가격은 실물 심볼, 변화율은 ETF 심볼에서 가져옴
    wti_price = get_stooq("cl.f")
    wti_chg   = get_stooq("uso.us")   # WTI ETF
    if wti_price and wti_chg:
        result["wti"] = {"close": wti_price["close"], "change_pct": wti_chg["change_pct"]}
    elif wti_price:
        result["wti"] = wti_price
    else:
        result["wti"] = {}

    gold_price = get_stooq("xauusd")
    gold_chg   = get_stooq("gld.us")  # 금 ETF
    if gold_price and gold_chg:
        result["gold"] = {"close": gold_price["close"], "change_pct": gold_chg["change_pct"]}
    elif gold_price:
        result["gold"] = gold_price
    else:
        result["gold"] = {}

    return result


# ── DART 주요 공시 ────────────────────────────────────────────────

def get_watch_companies() -> set:
    """전일 거래대금 상위 + 시총 상위 고정 종목명 집합"""
    # 시총 상위 고정 목록
    TOP_MARKET_CAP = {
        "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스",
        "현대차", "기아", "셀트리온", "NAVER", "카카오", "삼성SDI",
        "POSCO홀딩스", "KB금융", "신한지주", "하나금융지주", "현대모비스",
        "LG화학", "한국전력", "삼성물산", "SK이노베이션", "두산에너빌리티",
    }
    # KIS API로 전일 거래대금 상위 추가
    dynamic = set()
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        token = res.json().get("access_token")
        if token:
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
            data = requests.get(
                f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
                headers=headers, params=params, timeout=10,
            ).json()
            SKIP = ["KODEX","TIGER","KINDEX","ARIRANG","인버스","레버리지","ETN","리츠","채권"]
            for item in data.get("output", [])[:30]:
                nm = item.get("hts_kor_isnm", "")
                if nm and not any(k in nm for k in SKIP):
                    dynamic.add(nm)
    except Exception as e:
        print(f"  KIS 종목 조회 실패: {e}")

    return TOP_MARKET_CAP | dynamic


def get_dart_disclosures() -> list:
    """당일 공시 중 관심 종목(거래대금 상위 + 시총 상위) 필터링"""
    if not DART_API_KEY:
        return []
    try:
        today    = datetime.now(KST).strftime("%Y%m%d")
        watch    = get_watch_companies()

        items = []
        for ptype in ("B", "A"):   # B: 주요사항보고, A: 정기공시
            res  = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key":  DART_API_KEY,
                    "bgn_de":     today,
                    "end_de":     today,
                    "pblntf_ty":  ptype,
                    "page_count": 100,
                },
                timeout=10,
            )
            data = res.json()
            if data.get("status") == "000":
                items.extend(data.get("list", []))

        # 관심 종목에 포함된 공시만 필터
        filtered = [
            i for i in items
            if any(w in i.get("corp_name", "") for w in watch)
        ]
        return filtered[:5]
    except Exception as e:
        print(f"  DART 조회 실패: {e}")
        return []


# ── Claude AI 뷰 ──────────────────────────────────────────────────

def get_kospi_futures() -> dict:
    """코스피200 야간선물 (Stooq ks200f.f)"""
    return get_stooq("ks200f.f")


def get_market_investor() -> dict:
    """전일 코스피 외국인·기관 순매수 (삼성전자+SK하이닉스 합산)"""
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        token = res.json().get("access_token")
        if not token:
            return {}
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
            "tr_id": "FHKST01010900",
        }
        total_frgn, total_orgn = 0, 0
        found = False
        for iscd in ("005930", "000660"):   # 삼성전자, SK하이닉스
            try:
                r = requests.get(
                    f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
                    headers=headers,
                    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": iscd},
                    timeout=10,
                )
                rows = r.json().get("output", [])
                # 당일 row는 장 마감 전 빈값 → 데이터 있는 첫 row(전일) 사용
                for row in rows:
                    frgn = row.get("frgn_ntby_tr_pbmn", "")
                    orgn = row.get("orgn_ntby_tr_pbmn", "")
                    if frgn and orgn:
                        total_frgn += int(frgn)
                        total_orgn += int(orgn)
                        found = True
                        break
            except Exception as e:
                print(f"  수급 조회 실패 ({iscd}): {e}")
        return {"frgn": total_frgn, "orgn": total_orgn} if found else {}
    except Exception as e:
        print(f"  수급 조회 실패: {e}")
        return {}


def get_prev_leaders(n: int = 5) -> list:
    """전일 거래대금 상위 종목 (ETF 제외)"""
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        token = res.json().get("access_token")
        if not token:
            return []
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
        data = requests.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=headers, params=params, timeout=10,
        ).json()
        SKIP = ["KODEX","TIGER","KINDEX","ARIRANG","HANARO","KBSTAR",
                "인버스","레버리지","ETN","리츠","채권","선물"]
        leaders = []
        for item in data.get("output", []):
            nm   = item.get("hts_kor_isnm", "")
            rate = item.get("prdy_ctrt", "0")
            if nm and not any(k in nm for k in SKIP):
                leaders.append({"name": nm, "rate": float(rate or 0)})
            if len(leaders) >= n:
                break
        return leaders
    except Exception as e:
        print(f"  주도주 조회 실패: {e}")
        return []


def get_ai_view(mkt: dict, disclosures: list, investor: dict = None) -> str:
    """Claude로 장전 한줄 시황 뷰 생성"""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        def fmt(d, fmt_str="{:.2f}"):
            return fmt_str.format(d["change_pct"]) if d else "N/A"

        disc_text = "\n".join(
            f"- {d['corp_name']}: {d['report_nm']}" for d in disclosures
        ) or "없음"

        inv_text = ""
        if investor and (investor.get("frgn") or investor.get("orgn")):
            def m(v):
                s = "+" if v >= 0 else ""
                av = abs(v)
                if av >= 100: return f"{s}{v/100:.0f}억"
                return f"{s}{v}백만"
            inv_text = (
                f"\n전일 수급 (삼성전자+하이닉스):\n"
                f"외국인 {m(investor['frgn'])} / 기관 {m(investor['orgn'])}\n"
            )

        prompt = (
            f"한국 주식 투자자를 위한 장전 시황 뷰를 3문장으로 작성해주세요.\n\n"
            f"미국 지수 (전일 마감):\n"
            f"S&P500 {fmt(mkt.get('sp500'))}% / "
            f"나스닥 {fmt(mkt.get('nasdaq'))}% / "
            f"다우 {fmt(mkt.get('dow'))}% / "
            f"필라델피아반도체 {fmt(mkt.get('sox'))}%\n\n"
            f"원자재·환율:\n"
            f"WTI ${mkt['wti']['close']:.1f} ({fmt(mkt.get('wti'))}%) / "
            f"금 ${mkt['gold']['close']:.0f} / "
            f"달러원 {mkt['usdkrw']['close']:.0f}원\n"
            f"{inv_text}"
            f"\n오늘 주요 공시:\n{disc_text}\n\n"
            f"작성 규칙: 3문장, 전문가 격식체(~입니다/~습니다), 수치 근거 명시, 질문·이모지 금지, 국내 시장 영향 중심"
        )

        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 300,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        return res.json()["content"][0]["text"].strip()
    except Exception as e:
        print(f"  Claude 뷰 실패: {e}")
        return ""


# ── 포맷 ─────────────────────────────────────────────────────────

def fmt_change(d: dict, price_fmt: str = "{:.0f}") -> str:
    if not d:
        return "─"
    sign  = "+" if d["change_pct"] >= 0 else ""
    arrow = "▲" if d["change_pct"] >= 0 else "▼"
    return f"{arrow} {sign}{d['change_pct']:.2f}%"


def build_message(mkt: dict, disclosures: list, ai_view: str, now: datetime,
                  investor: dict = None, leaders: list = None) -> str:
    date_str = now.strftime("%m/%d (%a)").replace(
        "Mon","월").replace("Tue","화").replace("Wed","수").replace(
        "Thu","목").replace("Fri","금").replace("Sat","토").replace("Sun","일")

    sp  = mkt.get("sp500",  {})
    ndx = mkt.get("nasdaq", {})
    dow = mkt.get("dow",    {})
    sox = mkt.get("sox",    {})
    wti = mkt.get("wti",    {})
    gld = mkt.get("gold",   {})
    krw = mkt.get("usdkrw", {})
    fut = mkt.get("futures", {})

    def line(label, d, price_fmt="{:,.0f}"):
        if not d:
            return f"{label:<16} ─"
        p   = price_fmt.format(d["close"])
        chg = fmt_change(d)
        return f"{label:<16} {p}   {chg}"

    def fmt_money(v):
        # v: 백만원 단위 (1억 = 100백만원, 1조 = 1,000,000백만원)
        sign = "+" if v >= 0 else ""
        if abs(v) >= 1000000:
            return f"{sign}{v/1000000:.1f}조"
        if abs(v) >= 100:
            return f"{sign}{v/100:.0f}억"
        return f"{sign}{v}백만"

    # 야간선물
    futures_line = f"\n🌙 코스피200 야간선물\n{line('K200 선물', fut)}\n" if fut else ""

    # 전일 수급
    investor_line = ""
    if investor:
        frgn = investor.get("frgn", 0)
        orgn = investor.get("orgn", 0)
        investor_line = (
            f"\n💰 전일 수급 (삼성전자+하이닉스 합산)\n"
            f"외국인  {fmt_money(frgn)}   기관  {fmt_money(orgn)}\n"
        )

    # 전일 주도주
    leaders_line = ""
    if leaders:
        items = "  ".join(
            f"{i+1}위 {l['name']} {'+' if l['rate']>=0 else ''}{l['rate']:.1f}%"
            for i, l in enumerate(leaders)
        )
        leaders_line = f"\n📌 전일 주도주\n{items}\n"

    # 공시
    disc_lines = ""
    if disclosures:
        items = "\n".join(f"• {d['corp_name']} - {d['report_nm']}" for d in disclosures)
        disc_lines = f"\n📋 당일 공시 (관심 종목)\n{items}\n"

    # AI 뷰
    view_section = f"\n━━━━━━━━━━━━━━━━━━━━\n{ai_view}\n" if ai_view else ""

    return (
        f"📊 {date_str} 장전 시황 브리핑\n"
        f"\n"
        f"🌏 미국 시장 (전일 마감)\n"
        f"{line('S&P500', sp)}\n"
        f"{line('나스닥100', ndx)}\n"
        f"{line('다우', dow)}\n"
        f"{line('반도체ETF(SMH)', sox)}\n"
        f"\n"
        f"💵 환율 · 원자재\n"
        f"{line('달러/원', krw, '{:,.0f}원')}\n"
        f"{line('WTI', wti, '${:.1f}')}\n"
        f"{line('금', gld, '${:,.0f}')}\n"
        f"{futures_line}"
        f"{investor_line}"
        f"{leaders_line}"
        f"{disc_lines}"
        f"{view_section}"
        f"\n"
        f"📊 오늘도 10:30 / 14:00 / 15:30 거래대금 상위 종목 실시간 업데이트합니다.\n"
        f"팔로우하고 정보 얻어가세요! 원하는 종목·지표 있으면 댓글로 알려주세요 🙌"
    )


# ── 텔레그램 ─────────────────────────────────────────────────────

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        print(f"  텔레그램 오류: {e}")


# ── 메인 ─────────────────────────────────────────────────────────

def run():
    now = datetime.now(KST)
    print(f"\n{'='*50}")
    print(f"  장전 시황 브리핑  {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    mkt         = get_market_data()
    futures     = get_kospi_futures()
    if futures:
        mkt["futures"] = futures
    investor    = get_market_investor()
    leaders     = get_prev_leaders()
    disclosures = get_dart_disclosures()
    ai_view     = get_ai_view(mkt, disclosures, investor)
    msg         = build_message(mkt, disclosures, ai_view, now, investor, leaders)

    tg_send(msg)
    print("  발송 완료")
    print(msg)


if __name__ == "__main__":
    run()
