#!/usr/bin/env python3
"""
키움 서포터즈 - 거래대금 상위 종목 뉴스 실시간 알림
장중 30분마다 실행, 최근 35분 내 신규 기사 텔레그램 발송
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

# 모니터링 종목 (주요 대형주 + 거래 활발한 종목)
WATCH_STOCKS = [
    "삼성전자", "SK하이닉스", "LG에너지솔루션", "삼성바이오로직스",
    "현대차", "NAVER", "카카오", "기아", "POSCO홀딩스", "KB금융",
    "셀트리온", "삼성SDI", "현대모비스", "신한지주", "하나금융지주",
]

LOOKBACK_MIN = 35   # 최근 몇 분 내 기사만 발송


def get_recent_news(stock_name: str, minutes: int = LOOKBACK_MIN) -> list:
    """Google News RSS로 최근 N분 내 기사 조회"""
    try:
        query = quote(f"{stock_name} 주식")
        url   = f"https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"
        feed  = feedparser.parse(url)
        cutoff = datetime.now(KST) - timedelta(minutes=minutes)

        results = []
        for entry in feed.entries[:15]:
            try:
                pub_struct = entry.get("published_parsed")
                if not pub_struct:
                    continue
                pub_utc = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                pub_kst = pub_utc.astimezone(KST)
                if pub_kst < cutoff:
                    continue

                title = entry.get("title", "")
                # Google News 제목에서 출처 제거 (예: "제목 - 매일경제" → "제목")
                if " - " in title:
                    title, source = title.rsplit(" - ", 1)
                else:
                    source = ""

                results.append({
                    "title":     title.strip(),
                    "source":    source.strip(),
                    "link":      entry.get("link", ""),
                    "published": pub_kst,
                })
            except Exception:
                continue

        return results

    except Exception as e:
        print(f"  뉴스 조회 실패 ({stock_name}): {e}")
        return []


def tg_send(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
    except Exception as e:
        print(f"  텔레그램 오류: {e}")


def run_news_check():
    now   = datetime.now(KST)
    found = 0

    print(f"\n{'='*50}")
    print(f"  뉴스 알림 체크  {now.strftime('%Y-%m-%d %H:%M')} KST")
    print(f"{'='*50}")

    for stock in WATCH_STOCKS:
        articles = get_recent_news(stock)
        if not articles:
            time.sleep(0.5)
            continue

        for art in articles:
            time_str = art["published"].strftime("%H:%M")
            src_str  = f" ({art['source']})" if art["source"] else ""
            msg = (
                f"📰 [{stock}] 뉴스 알림  {time_str}\n"
                f"\n"
                f"{art['title']}{src_str}\n"
                f"\n"
                f"{art['link']}"
            )
            tg_send(msg)
            print(f"  ✅ [{stock}] {art['title'][:30]}...")
            found += 1
            time.sleep(0.3)

        time.sleep(0.5)

    if found == 0:
        print(f"  최근 {LOOKBACK_MIN}분 내 신규 뉴스 없음")
    else:
        print(f"  완료 ({found}건 발송)")


if __name__ == "__main__":
    run_news_check()
