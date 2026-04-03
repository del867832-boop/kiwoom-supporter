#!/usr/bin/env python3
"""
키움증권 서포터즈 2기 - 장중 거래대금 상위 실시간 포스터
KIS API 기반 실시간 데이터

스케줄:
  10:30 KST - 오전 거래대금 상위 10종목
  14:00 KST - 오후 거래대금 상위 10종목
  15:30 KST - 마감 직전 상위    10종목

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
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")
KIS_BASE_URL       = "https://openapi.koreainvestment.com:9443"

# ── 배치 설정 ─────────────────────────────────────────────────────
BATCH_SIZE = {
    "midday":    10,
    "afternoon": 10,
    "close":     10,
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


# 거래대금 상위 API 실패 시 사용할 대체 종목
FALLBACK_STOCKS = [
    {"ticker": "005930", "name": "삼성전자"},
    {"ticker": "000660", "name": "SK하이닉스"},
    {"ticker": "005380", "name": "현대차"},
    {"ticker": "000270", "name": "기아"},
    {"ticker": "035420", "name": "NAVER"},
    {"ticker": "373220", "name": "LG에너지솔루션"},
    {"ticker": "207940", "name": "삼성바이오로직스"},
    {"ticker": "105560", "name": "KB금융"},
    {"ticker": "055550", "name": "신한지주"},
    {"ticker": "051910", "name": "LG화학"},
]


def get_stock_info(ticker: str, name: str = "") -> dict:
    """개별 종목 현재가 상세 조회"""
    token = get_token()
    url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price"
    headers = {
        "content-type":  "application/json",
        "authorization": f"Bearer {token}",
        "appkey":        KIS_APP_KEY,
        "appsecret":     KIS_APP_SECRET,
        "tr_id":         "FHKST01010100",
    }
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
    res = requests.get(url, headers=headers, params=params, timeout=10)
    out = res.json().get("output", {})

    def _int(k): return int(out.get(k) or 0)
    def _flt(k): return float(out.get(k) or 0)

    return {
        "ticker":      ticker,
        "name":        out.get("hts_kor_isnm") or name,
        "price":       _int("stck_prpr"),
        "change":      _int("prdy_vrss"),
        "change_rate": _flt("prdy_ctrt"),
        "volume":      _int("acml_vol"),
        "tr_value":    _int("acml_tr_pbmn"),
        "open":        _int("stck_oprc"),
        "high":        _int("stck_hgpr"),
        "low":         _int("stck_lwpr"),
        "w52_high":    _int("w52_hgpr"),
        "w52_low":     _int("w52_lwpr"),
        "frgn_ntby":   _int("frgn_ntby_qty"),
        "orgn_ntby":   _int("orgn_ntby_qty"),
    }


def get_top_stocks(n: int) -> list:
    """거래대금 상위 n개 종목 조회. 실패 시 fallback 사용."""
    try:
        token = get_token()
        url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank"
        headers = {
            "content-type":  "application/json",
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHPST01710000",
            "custtype":      "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE":  "J",
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
        print(f"  volume-rank 응답: rt_cd={data.get('rt_cd')} msg={data.get('msg1','')}")

        SKIP = ["KODEX","TIGER","KINDEX","ARIRANG","HANARO","KBSTAR","PLUS",
                "MASTER","TIMEFOLIO","인버스","레버리지","선물","ETN","리츠","채권"]

        def is_skip(nm: str) -> bool:
            return any(k in nm for k in SKIP)

        stocks = []
        for item in data.get("output", []):
            if len(stocks) >= 50:
                break
            nm = item.get("hts_kor_isnm", "")
            if is_skip(nm):
                continue
            try:
                def _i(k): return int(item.get(k) or 0)
                def _f(k): return float(item.get(k) or 0)
                stocks.append({
                    "ticker":      item.get("mksc_shrn_iscd", ""),
                    "name":        nm,
                    "price":       _i("stck_prpr"),
                    "change":      _i("prdy_vrss"),
                    "change_rate": _f("prdy_ctrt"),
                    "volume":      _i("acml_vol"),
                    "tr_value":    _i("acml_tr_pbmn"),
                    "open":        _i("stck_oprc"),
                    "high":        _i("stck_hgpr"),
                    "low":         _i("stck_lwpr"),
                    "w52_high":    0,
                    "w52_low":     0,
                })
            except Exception:
                continue

        if stocks:
            stocks.sort(key=lambda x: x["tr_value"], reverse=True)
            return stocks[:n]
        raise Exception("output 비어있음")

    except Exception as e:
        print(f"  ⚠️ volume-rank 실패 ({e}), fallback 종목으로 대체")
        result = []
        for s in FALLBACK_STOCKS[:n]:
            try:
                info = get_stock_info(s["ticker"], s["name"])
                result.append(info)
                time.sleep(0.2)
            except Exception:
                continue
        return result


# ── 일봉 / 기술적 분석 ───────────────────────────────────────────

def get_price_history(ticker: str, n: int = 60) -> list:
    """KIS API로 최근 n영업일 일봉 데이터 조회"""
    try:
        token = get_token()
        url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-price"
        headers = {
            "content-type":  "application/json",
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHKST01010400",
        }
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd":         ticker,
            "fid_input_date_1":       "",
            "fid_input_date_2":       "",
            "fid_period_div_code":    "D",
            "fid_org_adj_prc":        "0",
        }
        res  = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        rows = data.get("output2") or data.get("output") or []
        return rows[:n]
    except Exception as e:
        print(f"  일봉 조회 실패 ({ticker}): {e}")
        return []


def calc_ta(rows: list) -> dict:
    """MA20 / MA60 / 골든·데드크로스 / RSI(14) / 거래량배율 계산"""
    if not rows or len(rows) < 5:
        return {}

    rows = list(reversed(rows))   # KIS API는 최신이 앞 → 오래된 순 정렬

    closes, volumes = [], []
    for r in rows:
        c = int(r.get("stck_clpr") or 0)
        v = int(r.get("acml_vol")  or 0)
        if c > 0:
            closes.append(c)
            volumes.append(v)

    if not closes:
        return {}

    n      = len(closes)
    result = {}

    # MA5
    if n >= 5:
        ma5 = sum(closes[-5:]) / 5
        result["ma5"]       = ma5
        result["above_ma5"] = closes[-1] > ma5

    # MA20 + 골든/데드크로스
    if n >= 20:
        ma20 = sum(closes[-20:]) / 20
        result["ma20"]       = ma20
        result["above_ma20"] = closes[-1] > ma20

        if n >= 21 and "ma5" in result:
            prev_ma5  = sum(closes[-6:-1])  / 5
            prev_ma20 = sum(closes[-21:-1]) / 20
            if prev_ma5 < prev_ma20 and result["ma5"] > ma20:
                result["cross"] = "골든크로스"
            elif prev_ma5 > prev_ma20 and result["ma5"] < ma20:
                result["cross"] = "데드크로스"

    # MA60
    if n >= 60:
        ma60 = sum(closes[-60:]) / 60
        result["ma60"]       = ma60
        result["above_ma60"] = closes[-1] > ma60

    # RSI (14)
    if n >= 15:
        gains, losses = [], []
        for i in range(n - 14, n):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        avg_gain = sum(gains)  / 14
        avg_loss = sum(losses) / 14
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        result["rsi"] = round(rsi, 1)
        result["rsi_label"] = "과매수" if rsi >= 70 else ("과매도" if rsi <= 30 else "중립")

    # 거래량배율 (오늘 / 직전 20일 평균)
    if len(volumes) >= 21:
        avg_vol = sum(volumes[-21:-1]) / 20
        if avg_vol > 0:
            result["vol_ratio"] = round(volumes[-1] / avg_vol, 1)

    return result


def fmt_ta_line(ta: dict) -> str:
    """기술지표 한 줄 요약"""
    if not ta:
        return ""
    parts = []

    ma_parts = []
    if "above_ma20" in ta:
        ma_parts.append(f"MA20 {'▲' if ta['above_ma20'] else '▼'}")
    if "above_ma60" in ta:
        ma_parts.append(f"MA60 {'▲' if ta['above_ma60'] else '▼'}")
    if ma_parts:
        parts.append(" · ".join(ma_parts))

    if "cross" in ta:
        parts.append(ta["cross"])

    if "rsi" in ta:
        parts.append(f"RSI {ta['rsi']} ({ta['rsi_label']})")

    if "vol_ratio" in ta:
        parts.append(f"거래량 {ta['vol_ratio']}배")

    return ("📊 " + "  |  ".join(parts)) if parts else ""


# ── 외국인 / 기관 수급 ───────────────────────────────────────────

def get_investor_data(ticker: str) -> dict:
    """외국인·기관 당일 순매수량 조회 (실패 시 0 반환)"""
    try:
        token = get_token()
        url   = f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
        headers = {
            "content-type":  "application/json",
            "authorization": f"Bearer {token}",
            "appkey":        KIS_APP_KEY,
            "appsecret":     KIS_APP_SECRET,
            "tr_id":         "FHKST01010900",
        }
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd":         ticker,
        }
        res  = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        rows = data.get("output")
        if not rows:
            return {"frgn": 0, "orgn": 0}
        row  = rows[0] if isinstance(rows, list) else rows
        frgn = int(row.get("frgn_ntby_qty") or 0)
        orgn = int(row.get("orgn_ntby_qty")  or 0)
        return {"frgn": frgn, "orgn": orgn}
    except Exception as e:
        print(f"  investor 오류 ({ticker}): {e}")
        return {"frgn": 0, "orgn": 0}


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


# ── Claude API 동적 멘트 생성 ─────────────────────────────────────

def get_ai_comment(info: dict, inv: dict, batch: str, rank: int, ta: dict = None) -> tuple:
    """Claude API로 코멘트 + 토론 질문 생성. 실패 시 룰 기반 폴백."""
    if not ANTHROPIC_API_KEY:
        return _fallback_comment(info, batch), ""

    name        = info["name"]
    price       = info["price"]
    rate        = info["change_rate"]
    tr_val      = fmt_value(info["tr_value"])
    vol         = fmt_vol(info["volume"])
    frgn        = inv.get("frgn", 0)
    orgn        = inv.get("orgn", 0)
    batch_label = BATCH_LABEL.get(batch, batch)

    frgn_str = f"외국인 {'+' if frgn>=0 else ''}{frgn//10000}만주" if frgn != 0 else "외국인 데이터 없음"
    orgn_str = f"기관 {'+' if orgn>=0 else ''}{orgn//10000}만주"   if orgn != 0 else "기관 데이터 없음"

    ta_str = ""
    if ta:
        ta_parts = []
        if "above_ma20" in ta: ta_parts.append(f"MA20 {'위' if ta['above_ma20'] else '아래'}")
        if "above_ma60" in ta: ta_parts.append(f"MA60 {'위' if ta['above_ma60'] else '아래'}")
        if "cross"      in ta: ta_parts.append(ta["cross"])
        if "rsi"        in ta: ta_parts.append(f"RSI {ta['rsi']}({ta['rsi_label']})")
        if "vol_ratio"  in ta: ta_parts.append(f"거래량 평균比 {ta['vol_ratio']}배")
        ta_str = " / ".join(ta_parts)

    prompt = (
        f"키움증권 커뮤니티 포스팅용 글을 써주세요.\n\n"
        f"종목: {name} (거래대금 {rank}위)\n"
        f"현재가: {price:,}원 ({'+' if rate>=0 else ''}{rate:.2f}%)\n"
        f"거래대금: {tr_val} / 거래량: {vol}\n"
        f"{frgn_str} / {orgn_str}\n"
        f"기술지표: {ta_str if ta_str else '없음'}\n"
        f"시간대: {batch_label}\n\n"
        f"아래 한 블록만 출력하세요 (다른 설명 없이):\n"
        f"[VIEW] 기술지표 기반 판단 2~3문장 (MA 위치·RSI·거래량배율 근거 포함, 매번 다르게, 단정적으로, 질문 형식 금지)"
    )

    try:
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-haiku-4-5-20251001",
                "max_tokens": 200,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=15,
        )
        text     = res.json()["content"][0]["text"].strip()
        view     = ""
        question = ""
        lines    = text.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("[VIEW]"):
                # [VIEW] 이후 여러 줄일 수 있으므로 다음 태그 전까지 수집
                view_lines = [line.replace("[VIEW]", "").strip()]
                for j in range(i + 1, len(lines)):
                    if lines[j].startswith("["):
                        break
                    if lines[j].strip():
                        view_lines.append(lines[j].strip())
                view = " ".join(view_lines).strip()
            elif line.startswith("[QUESTION]"):
                question = line.replace("[QUESTION]", "").strip()
        if view:
            print(f"  AI 뷰 완료: {view[:20]}...")
            return view, ""
    except Exception as e:
        print(f"  AI 멘트 실패 ({e}), 폴백 사용")

    return _fallback_comment(info, batch), ""


def _fallback_comment(info: dict, batch: str) -> str:
    """룰 기반 한줄 코멘트 (AI 실패 시)"""
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


def _fallback_discussion(info: dict, inv: dict) -> str:
    """룰 기반 토론 질문 (AI 실패 시)"""
    rate = info["change_rate"]
    frgn = inv.get("frgn", 0)
    orgn = inv.get("orgn", 0)

    if frgn > 200000 and rate > 0:
        return f"외국인이 오늘 {fmt_vol(frgn)} 순매수 중인데, 같이 따라가는 게 맞을까요? 👇"
    if frgn < -200000 and rate < 0:
        return f"외국인이 {fmt_vol(abs(frgn))} 빠지는 구간입니다. 저점 매수 타이밍으로 보시나요? 👇"
    if orgn > 100000 and frgn > 0:
        return f"외국인·기관 동시 매수 중입니다. 이 구간에서 어떻게 대응하고 계세요? 👇"
    if rate >= 3:
        return f"+{rate:.1f}% 강세인데 추격 매수 vs 눌림목 대기, 어떻게 보세요? 👇"
    if rate <= -3:
        return f"{rate:.1f}% 하락 중입니다. 손절 vs 물타기 vs 관망, 의견 나눠요 👇"
    if rate > 0:
        return f"오늘 상승 중인데 익절 타이밍 고민되시는 분 있으세요? 👇"
    return f"지금 이 종목 어떻게 보고 계세요? 매수·홀딩·관망 의견 주세요 👇"


# ── 포스트 빌드 ───────────────────────────────────────────────────

def build_post(info: dict, inv: dict, batch: str, rank: int, now: datetime,
               macro: dict = None, ta: dict = None) -> str:
    name     = info["name"]
    arrow    = "▲" if info["change_rate"] > 0 else ("▼" if info["change_rate"] < 0 else "─")
    sign     = "+" if info["change_rate"] > 0 else ""
    icon     = "📈" if info["change_rate"] >= 0 else "📉"
    comment, question = get_ai_comment(info, inv, batch, rank, ta)
    time_str = now.strftime("%H:%M")

    macro_line = ""
    if macro:
        parts = []
        if macro.get("oil"):    parts.append(f"WTI ${macro['oil']}")
        if macro.get("usdkrw"): parts.append(f"달러 {macro['usdkrw']:,}원")
        if parts:
            macro_line = " / ".join(parts) + "\n"

    frgn = inv.get("frgn", 0)
    orgn = inv.get("orgn", 0)
    frgn_str = f"{'▲' if frgn>=0 else '▼'} {fmt_vol(abs(frgn))}" if frgn != 0 else "─"
    orgn_str = f"{'▲' if orgn>=0 else '▼'} {fmt_vol(abs(orgn))}" if orgn != 0 else "─"

    inv_lines = ""
    if frgn != 0 or orgn != 0:
        inv_lines = f"외국인    {frgn_str}\n기관      {orgn_str}\n"

    ta_line    = fmt_ta_line(ta) + "\n" if ta else ""
    follow_line = (
        "📊 매일 10:30 / 14:00 / 15:30 거래대금 상위 종목 실시간 업데이트합니다.\n"
        "팔로우하고 정보 얻어가세요! 원하는 종목이나 지표 있으면 댓글로 알려주세요 🙌"
    )

    return (
        f"{icon} {name}  {time_str} 현재\n"
        f"\n"
        f"현재가    {fmt_price(info['price'])}  {arrow} {sign}{info['change_rate']:.2f}%\n"
        f"거래대금  {fmt_value(info['tr_value'])}\n"
        f"거래량    {fmt_vol(info['volume'])}\n"
        f"{inv_lines}"
        f"{ta_line}"
        f"\n"
        f"{comment}\n"
        f"\n"
        f"{follow_line}\n"
        f"\n"
        f"{macro_line}"
        f"#{name} #장중정보 #거래대금상위"
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


# ── 팔로우 유도 문구 ──────────────────────────────────────────────

FOLLOW_MSG = (
    "매일 10:30 / 14:00 / 15:30 장중 거래대금 상위 종목 실시간 정보 올리고 있어요 📊\n"
    "팔로우하시면 수급·외국인 동향 빠르게 받아보실 수 있습니다!"
)


# ── 배치 실행 ─────────────────────────────────────────────────────

def run_batch(batch: str):
    now   = datetime.now(KST)
    label = BATCH_LABEL.get(batch, batch)
    n     = BATCH_SIZE.get(batch, 10)

    print(f"\n{'='*50}")
    print(f"  키움 서포터즈  {now.strftime('%Y-%m-%d %H:%M')} KST  {label}")
    print(f"{'='*50}")

    macro   = get_macro()
    oil_str = f"WTI ${macro['oil']}"        if macro.get("oil")    else ""
    usd_str = f"달러 {macro['usdkrw']:,}원" if macro.get("usdkrw") else ""
    macro_line = " / ".join(filter(None, [oil_str, usd_str]))

    header = (
        f"📊 {now.strftime('%m/%d %H:%M')} KST  {label}\n"
        f"{macro_line + chr(10) if macro_line else ''}"
        f"\n{FOLLOW_MSG}"
    )
    tg_send(header)
    time.sleep(0.3)

    try:
        stocks = get_top_stocks(n)
        print(f"  거래대금 상위 {len(stocks)}종목 조회 완료")
    except Exception as e:
        print(f"  ⚠️  종목 조회 실패: {e}")
        tg_send("⚠️ 거래대금 상위 종목 조회 실패")
        return

    for rank, stock in enumerate(stocks, start=1):
        name = stock.get("name", "?")
        try:
            detail = get_stock_info(stock["ticker"], name)
            if detail["tr_value"] > 0:
                stock.update(detail)
            time.sleep(0.2)

            inv = get_investor_data(stock["ticker"])
            time.sleep(0.2)

            history = get_price_history(stock["ticker"])
            ta      = calc_ta(history)
            time.sleep(0.2)

            title = f"{name} {now.strftime('%m/%d')} 장중 실시간"
            body  = build_post(stock, inv, batch, rank, now, macro, ta)

            tg_send(title)
            time.sleep(0.2)
            tg_send(body)
            frgn_disp = f"외국인 {'+' if inv['frgn']>=0 else ''}{inv['frgn']//10000}만주" if inv['frgn'] != 0 else ""
            print(f"  ✅ {name}  {stock['price']:,}원  {stock['change_rate']:+.2f}%  {frgn_disp}")
        except Exception as e:
            print(f"  ⚠️  {name} 오류: {e}")
            tg_send(f"📊 {name}  {now.strftime('%H:%M')}\n\n데이터 오류")
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
