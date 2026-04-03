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
ANTHROPIC_API_KEY  = os.getenv("ANTHROPIC_API_KEY")


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
    symbols = {
        "sp500":  "^spx",
        "nasdaq": "^ndx",
        "dow":    "^dji",
        "sox":    "smh.us",  # 필라델피아 반도체 ETF (SOX 대용)
        "wti":    "cl.f",
        "gold":   "xauusd",
        "usdkrw": "usdkrw",
    }
    return {k: get_stooq(v) for k, v in symbols.items()}


# ── DART 주요 공시 ────────────────────────────────────────────────

def get_dart_disclosures() -> list:
    """어제~오늘 주요사항보고 + 정기공시 최대 5건"""
    if not DART_API_KEY:
        return []
    try:
        today     = datetime.now(KST).strftime("%Y%m%d")
        yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y%m%d")

        items = []
        for ptype in ("B", "A"):   # B: 주요사항보고, A: 정기공시
            res  = requests.get(
                "https://opendart.fss.or.kr/api/list.json",
                params={
                    "crtfc_key":   DART_API_KEY,
                    "bgn_de":      yesterday,
                    "end_de":      today,
                    "pblntf_ty":   ptype,
                    "page_count":  20,
                },
                timeout=10,
            )
            data = res.json()
            if data.get("status") == "000":
                items.extend(data.get("list", []))

        # 대형주 우선
        MAJOR = ["삼성전자", "SK하이닉스", "LG에너지솔루션", "현대차",
                 "NAVER", "카카오", "삼성바이오로직스", "기아", "POSCO"]
        priority = [i for i in items if any(m in i.get("corp_name", "") for m in MAJOR)]
        others   = [i for i in items if i not in priority]
        return (priority + others)[:5]
    except Exception as e:
        print(f"  DART 조회 실패: {e}")
        return []


# ── Claude AI 뷰 ──────────────────────────────────────────────────

def get_ai_view(mkt: dict, disclosures: list) -> str:
    """Claude로 장전 한줄 시황 뷰 생성"""
    if not ANTHROPIC_API_KEY:
        return ""
    try:
        def fmt(d, fmt_str="{:.2f}"):
            return fmt_str.format(d["change_pct"]) if d else "N/A"

        disc_text = "\n".join(
            f"- {d['corp_name']}: {d['report_nm']}" for d in disclosures
        ) or "없음"

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
            f"달러원 {mkt['usdkrw']['close']:.0f}원\n\n"
            f"오늘 주요 공시:\n{disc_text}\n\n"
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


def build_message(mkt: dict, disclosures: list, ai_view: str, now: datetime) -> str:
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

    def line(label, d, price_fmt="{:,.0f}"):
        if not d:
            return f"{label:<16} ─"
        p    = price_fmt.format(d["close"])
        chg  = fmt_change(d)
        return f"{label:<16} {p}   {chg}"

    # 공시 섹션
    disc_lines = ""
    if disclosures:
        items = "\n".join(f"• {d['corp_name']} - {d['report_nm']}" for d in disclosures)
        disc_lines = f"\n📋 주요 공시\n{items}\n"

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
    disclosures = get_dart_disclosures()
    ai_view     = get_ai_view(mkt, disclosures)
    msg         = build_message(mkt, disclosures, ai_view, now)

    tg_send(msg)
    print("  발송 완료")
    print(msg)


if __name__ == "__main__":
    run()
