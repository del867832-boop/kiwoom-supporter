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


def get_investor_trend(days: int = 7) -> list:
    """삼성전자+SK하이닉스 합산 외인·기관 일별 순매수 (최근 days일, 완성 데이터만)"""
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
            "tr_id": "FHKST01010900",
        }
        # 종목별 날짜 → (frgn, orgn) 누적
        day_map: dict = {}
        for iscd in ("005930", "000660"):
            try:
                r = requests.get(
                    f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
                    headers=headers,
                    params={"fid_cond_mrkt_div_code": "J", "fid_input_iscd": iscd},
                    timeout=10,
                )
                for row in r.json().get("output", []):
                    date = row.get("stck_bsop_date", "")
                    frgn = row.get("frgn_ntby_tr_pbmn", "")
                    orgn = row.get("orgn_ntby_tr_pbmn", "")
                    if date and frgn and orgn:
                        prev = day_map.get(date, (0, 0))
                        day_map[date] = (prev[0] + int(frgn), prev[1] + int(orgn))
            except Exception as e:
                print(f"  수급 조회 실패 ({iscd}): {e}")

        # 날짜 내림차순 정렬 후 최근 days일 반환
        result = []
        for date in sorted(day_map.keys(), reverse=True)[:days]:
            frgn, orgn = day_map[date]
            result.append({
                "date": date,                 # YYYYMMDD
                "frgn": frgn,                 # 백만원 단위
                "orgn": orgn,
            })
        return result
    except Exception as e:
        print(f"  수급 조회 실패: {e}")
        return []


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


def get_ai_view(mkt: dict, disclosures: list, investor_trend: list = None) -> str:
    """Claude로 장전 시황 + 수급 트렌드 판단 생성"""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        def fmt(d, fmt_str="{:.2f}"):
            return fmt_str.format(d["change_pct"]) if d else "N/A"

        disc_text = "\n".join(
            f"- {d['corp_name']}: {d['report_nm']}" for d in disclosures
        ) or "없음"

        inv_text = ""
        if investor_trend:
            def m(v):
                s = "+" if v >= 0 else ""
                return f"{s}{v/100:.0f}억" if abs(v) >= 100 else f"{s}{v}백만"
            lines = []
            for d in investor_trend:
                dt = d["date"]
                lines.append(f"{dt[4:6]}/{dt[6:8]} 외인{m(d['frgn'])} 기관{m(d['orgn'])}")
            inv_text = "\n최근 7일 수급 (삼성전자+하이닉스):\n" + "\n".join(lines) + "\n"

        prompt = (
            f"한국 주식 투자자를 위한 장전 시황 브리핑을 4문장으로 작성해주세요.\n\n"
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
            f"작성 규칙:\n"
            f"- 4문장 (①미국지수·원자재 요약 ②환율 영향 ③7일 수급 트렌드 판단 ④오늘 장 전망)\n"
            f"- 전문가 격식체(~입니다/~습니다), 수치 근거 명시\n"
            f"- 수급 트렌드가 있으면 외인/기관 누적 매수·매도 흐름과 시사점 반드시 포함\n"
            f"- 질문·이모지 금지"
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
                  investor_trend: list = None, leaders: list = None) -> str:
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

    # 수급 트렌드 (7일)
    investor_line = ""
    if investor_trend:
        def fmt_bar(v):
            # 외인/기관 순매수 방향을 간단한 기호로
            if v > 0: return "▲"
            if v < 0: return "▼"
            return "─"

        rows_txt = []
        for d in investor_trend:
            dt = d["date"]
            label = f"{dt[4:6]}/{dt[6:8]}"
            frgn_bar = fmt_bar(d["frgn"])
            orgn_bar = fmt_bar(d["orgn"])
            rows_txt.append(
                f"{label}  외인{frgn_bar}{fmt_money(d['frgn'])}  기관{orgn_bar}{fmt_money(d['orgn'])}"
            )
        # 연속 순매수 일수 계산 (삼성전자+하이닉스 합산 기준)
        consec_buy = consec_sell = 0
        for d in investor_trend:
            if d["frgn"] > 0:
                consec_buy  += 1
                consec_sell  = 0
            else:
                consec_sell += 1
                consec_buy   = 0

        if consec_buy >= 2:
            consec_badge = f"🔥 외인 {consec_buy}일 연속 순매수\n"
        elif consec_sell >= 2:
            consec_badge = f"⚠️ 외인 {consec_sell}일 연속 순매도\n"
        else:
            consec_badge = ""

        investor_line = (
            f"\n💰 수급 동향 7일 (삼성전자+하이닉스)\n"
            f"{consec_badge}"
            + "\n".join(rows_txt) + "\n"
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

    mkt            = get_market_data()
    futures        = get_kospi_futures()
    if futures:
        mkt["futures"] = futures
    investor_trend = get_investor_trend(7)
    leaders        = get_prev_leaders()
    disclosures    = get_dart_disclosures()
    ai_view        = get_ai_view(mkt, disclosures, investor_trend)
    msg            = build_message(mkt, disclosures, ai_view, now, investor_trend, leaders)

    tg_send(msg)
    print("  발송 완료")
    print(msg)


if __name__ == "__main__":
    run()
