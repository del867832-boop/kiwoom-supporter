#!/usr/bin/env python3
"""
키움 서포터즈 - 종목 뉴스 알림
장중 30분마다 실행 / 신뢰 매체 + 주요 키워드 필터 / KIS API 거래대금 상위 종목 연동
"""

import os
import time
import requests
import feedparser
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

KST = timezone(timedelta(hours=9))

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")
KIS_APP_KEY        = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET     = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL       = "https://openapi.koreainvestment.com:9443"

LOOKBACK_MIN = 32   # 30분 실행 간격 + 2분 여유

# 항상 포함할 종목
FIXED_STOCKS = ["삼성전자", "SK하이닉스"]

# 신뢰 매체 (이 중 하나라도 포함 시 통과)
TRUSTED_SOURCES = [
    "한국경제", "매일경제", "머니투데이", "연합뉴스", "이데일리",
    "헤럴드경제", "파이낸셜뉴스", "조선비즈", "아시아경제", "서울경제",
    "뉴시스", "뉴스1", "한경", "중앙일보", "동아일보",
]

# 중요 키워드 (제목에 하나라도 있으면 통과)
IMPORTANT_KEYWORDS = [
    "급등", "급락", "상한가", "하한가", "호재", "악재",
    "공시", "실적", "수주", "계약", "신고가", "신저가",
    "목표가", "상향", "하향", "매수", "매도",
    "외국인", "기관", "대량", "어닝", "서프라이즈", "쇼크",
    "분기", "영업이익", "흑자", "적자", "배당",
]

# ETF·펀드 제외 키워드
SKIP_STOCKS = ["KODEX", "TIGER", "KINDEX", "ARIRANG", "인버스", "레버리지", "ETN", "리츠", "채권"]


# ── KIS API: 거래대금 상위 종목명 조회 ────────────────────────────

def get_top_stock_names(n: int = 8) -> list:
    """거래대금 상위 n개 종목명 반환 (실패 시 빈 리스트)"""
    try:
        res = requests.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={"grant_type": "client_credentials", "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET},
            timeout=10,
        )
        token = res.json().get("access_token")
        if not token:
            return []

        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": KIS_APP_KEY,
            "appsecret": KIS_APP_SECRET,
            "tr_id": "FHPST01710000",
            "custtype": "P",
        }
        params = {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
            "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
            "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "000000", "FID_INPUT_PRICE_1": "",
            "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
        }
        data   = requests.get(f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/volume-rank",
                              headers=headers, params=params, timeout=10).json()
        names  = []
        for item in data.get("output", []):
            nm = item.get("hts_kor_isnm", "")
            if any(k in nm for k in SKIP_STOCKS):
                continue
            if nm and nm not in FIXED_STOCKS and nm not in names:
                names.append(nm)
            if len(names) >= n:
                break
        return names
    except Exception as e:
        print(f"  KIS 종목 조회 실패: {e}")
        return []


# ── 뉴스 조회 + 필터 ─────────────────────────────────────────────

def get_filtered_news(stock_name: str, seen_urls: set) -> list:
    """신뢰 매체 + 주요 키워드 기준으로 최근 기사 필터링"""
    try:
        query  = quote(f"{stock_name} 주식")
        url    = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        feed   = feedparser.parse(url)
        cutoff = datetime.now(KST) - timedelta(minutes=LOOKBACK_MIN)

        results = []
        for entry in feed.entries[:20]:
            try:
                ps = entry.get("published_parsed")
                if not ps:
                    continue
                pub = datetime(*ps[:6], tzinfo=timezone.utc).astimezone(KST)
                if pub < cutoff:
                    continue

                link = entry.get("link", "")
                if link in seen_urls:
                    continue

                title  = entry.get("title", "")
                source = ""
                if " - " in title:
                    title, source = title.rsplit(" - ", 1)
                title  = title.strip()
                source = source.strip()

                # 신뢰 매체 필터
                if not any(s in source for s in TRUSTED_SOURCES):
                    continue

                # 중요 키워드 필터
                if not any(k in title for k in IMPORTANT_KEYWORDS):
                    continue

                seen_urls.add(link)
                results.append({
                    "title":  title,
                    "source": source,
                    "link":   link,
                    "pub":    pub,
                })
                if len(results) >= 2:
                    break
            except Exception:
                continue
        return results
    except Exception as e:
        print(f"  뉴스 조회 실패 ({stock_name}): {e}")
        return []


# ── 텔레그램 발송 ─────────────────────────────────────────────────

def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  텔레그램 오류: {e}")


# ── 메인 ─────────────────────────────────────────────────────────

def run():
    now = datetime.now(KST)
    print(f"\n{'='*50}")
    print(f"  뉴스 알림  {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    # 모니터링 종목: 삼전·하닉 고정 + 거래대금 상위 8개
    dynamic = get_top_stock_names(8)
    stocks  = FIXED_STOCKS + [s for s in dynamic if s not in FIXED_STOCKS]
    print(f"  모니터링 종목: {', '.join(stocks)}")

    seen_urls = set()
    total     = 0

    for stock in stocks:
        articles = get_filtered_news(stock, seen_urls)
        for art in articles:
            time_str = art["pub"].strftime("%H:%M")
            msg = (
                f"📰 [{stock}] {time_str}\n"
                f"\n"
                f"{art['title']}\n"
                f"({art['source']})\n"
                f"\n"
                f"{art['link']}"
            )
            tg_send(msg)
            print(f"  ✅ [{stock}] {art['title'][:35]}...")
            total += 1
            time.sleep(0.3)
        time.sleep(0.5)

    if total == 0:
        print(f"  최근 {LOOKBACK_MIN}분 내 주요 뉴스 없음")
    else:
        print(f"  완료 ({total}건 발송)")


if __name__ == "__main__":
    run()
