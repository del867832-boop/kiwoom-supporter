#!/usr/bin/env python3
"""
키움증권 커뮤니티 서포터즈 2기 자동 게시글 생성기
- 매일 거래대금 상위 10종목 조회 (pykrx)
- 템플릿 기반 게시글 10개 생성
- 텔레그램 봇으로 전송 + supporter_posts/ 폴더 저장

실행법:
  python kiwoom_supporter.py --now    # 즉시 1회 실행
  python kiwoom_supporter.py          # 매일 08:30 자동 실행 (로컬)
"""

import os
import sys
import time
import random
import requests
import schedule
import pandas as pd
from datetime import datetime, timedelta
from pykrx import stock as krx
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


# ──────────────────────────────────────────
# 템플릿
# ──────────────────────────────────────────

TEMPLATES = {
    "분석/정보형": [
        "{name} 오늘 거래대금 {vol}억 터졌네요. 전일 대비 수급이 눈에 띄게 몰리는 중인데, 단순 테마인지 실적 기반인지 체크해볼 필요 있을 것 같습니다. #{name}",
        "오늘 {name}({ticker}) {sign}{change_rate}% 움직임. 거래대금 {vol}억으로 상위권 유지 중. 외국인/기관 수급 방향이 관건인 것 같습니다. 차트상 주요 지지선 지키는지 확인 중이에요. #{name}",
        "{name} 거래대금 {vol}억, 등락률 {sign}{change_rate}%. 최근 며칠간 거래대금이 꾸준히 늘고 있는데 이런 경우 방향성 나오기 전에 변동성 커지는 경우 많더라고요. 현재가 {price:,}원 구간 지지 여부 체크 중입니다. #{name}",
        "{name} 오늘 현재가 {price:,}원, {sign}{change_rate}% 마감. 거래대금 {vol}억이면 시장 관심도 꽤 높은 편. 단기 차트로 보면 거래량 실린 양봉/음봉 이후 방향이 나오는 패턴 많으니 내일 흐름도 같이 봐야 할 것 같아요. #{name}",
        "오늘 {name} 거래대금 기준 상위 종목에 이름 올렸네요. {sign}{change_rate}% 움직임에 {vol}억 거래대금. 수급 측면에서 기관·외국인 어느 쪽이 주도하는지가 포인트인 것 같습니다. #{name}",
    ],
    "관심유도형": [
        "{name} 지금 {price:,}원인데 여기서 추가 매수하는 게 맞는 건지 아직도 고민 중... 거래대금 {vol}억에 {sign}{change_rate}%면 이미 많이 올랐다고 봐야 할까요? 의견 궁금합니다. #{name}",
        "솔직히 {name} 지금 {sign}{change_rate}% 움직임 보면서 들어가야 하나 말아야 하나 고민되시는 분들 많을 것 같은데, 지금 구간 어떻게 보세요? 추가 상승 여력 있다고 보시나요? #{name}",
        "{name} 들고 계신 분들 지금 어떠세요? {sign}{change_rate}%에 거래대금 {vol}억이면 시장 관심은 확실한데 여기서 홀딩이 맞는지 익절이 맞는지 판단이 쉽지 않네요. #{name}",
        "{name} 오늘 {sign}{change_rate}% 움직인 거 보고 뒤늦게 들어가려고 했는데 망설여지네요. 이런 거래대금 터진 날 따라 들어가는 게 맞는지 아니면 눌림목 기다려야 하는지... 어떻게 생각하세요? #{name}",
        "{name} {price:,}원 지금 이 자리, 저점 매수 구간으로 보시는 분 있나요? 아니면 아직 더 빠질 수 있다고 보시는 건지. 거래대금 {vol}억이면 관심은 확실히 있는 것 같긴 한데요. #{name}",
    ],
    "TIP공유형": [
        "{name} 관심 있다면 제가 체크하는 포인트 공유합니다. ① 거래대금 연속성 (오늘 {vol}억) ② 현재가 {price:,}원 기준 직전 고점/저점 위치 ③ 외국인·기관 수급 방향. 이 세 가지 동시에 맞아야 진입 고려하는 편입니다. #{name}",
        "{name} 같은 거래대금 상위 종목 매매할 때 제가 쓰는 방법: 장 시작 첫 30분 거래량·방향 확인 후 진입 여부 결정. 오늘처럼 {sign}{change_rate}% 이미 움직인 날은 추격보다 다음날 눌림목 대기가 더 나은 경우 많았습니다. #{name}",
        "{name} 보기 전에 체크해야 할 리스크 포인트 몇 가지. ① 거래대금 {vol}억이 일시적인지 연속성 있는지 ② 현재가 {price:,}원이 52주 고점 대비 어느 위치인지 ③ 관련 뉴스·공시 유무. 뉴스 없는 상승은 단기 변동성에 주의가 필요합니다. #{name}",
        "거래대금 상위 종목 {name} 단타 치는 분들께 드리는 팁. {sign}{change_rate}% 움직인 날 다음날 패턴 보면: 갭상승 후 눌림, 갭하락 후 반등 두 가지 경우가 많아요. 진입 전 전일 종가 기준 갭 방향 먼저 확인하는 게 좋습니다. #{name}",
        "{name} 매매 시 손절 기준 잡는 방법 공유. 현재가 {price:,}원 기준으로 직전 저점 or -3% 중 더 가까운 쪽을 손절선으로 잡는 편입니다. 거래대금 {vol}억 터진 날은 변동성도 같이 커지니 진입 사이즈 평소보다 줄이는 게 안전합니다. #{name}",
    ],
}

POST_TYPES = ["분석/정보형", "관심유도형", "TIP공유형"]
_used: dict = {t: [] for t in POST_TYPES}


# ──────────────────────────────────────────
# 1. 데이터 수집
# ──────────────────────────────────────────

def get_recent_trading_day() -> str:
    for i in range(7):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = krx.get_market_trading_value_by_ticker(d, market="KOSPI")
            if not df.empty and df["거래대금"].sum() > 0:
                return d
        except Exception:
            continue
    return datetime.now().strftime("%Y%m%d")


def get_top10_stocks(date_str: str) -> list:
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            df = krx.get_market_trading_value_by_ticker(date_str, market=market)
            if not df.empty:
                frames.append(df)
        except Exception:
            pass

    if not frames:
        return []

    combined   = pd.concat(frames)
    top10      = combined.nlargest(10, "거래대금").reset_index()
    ticker_col = "티커" if "티커" in top10.columns else top10.columns[0]

    results = []
    for _, row in top10.iterrows():
        ticker = str(row[ticker_col])
        try:
            name = krx.get_market_ticker_name(ticker)
        except Exception:
            name = ticker

        try:
            ohlcv       = krx.get_market_ohlcv_by_date(date_str, date_str, ticker)
            price       = int(ohlcv["종가"].iloc[-1])               if not ohlcv.empty else 0
            change_rate = round(float(ohlcv["등락률"].iloc[-1]), 2) if not ohlcv.empty else 0.0
        except Exception:
            price, change_rate = 0, 0.0

        results.append({
            "ticker":      ticker,
            "name":        name,
            "price":       price,
            "change_rate": change_rate,
            "volume_100m": int(row["거래대금"] / 1e8),
        })
    return results


# ──────────────────────────────────────────
# 2. 게시글 생성
# ──────────────────────────────────────────

def pick_template(post_type: str) -> str:
    pool      = TEMPLATES[post_type]
    used      = _used[post_type]
    available = [i for i in range(len(pool)) if i not in used]
    if not available:
        _used[post_type] = []
        available = list(range(len(pool)))
    idx = random.choice(available)
    _used[post_type].append(idx)
    return pool[idx]


def generate_post(stock: dict, post_type: str) -> str:
    sign = "+" if stock["change_rate"] >= 0 else ""
    return pick_template(post_type).format(
        name=stock["name"],
        ticker=stock["ticker"],
        price=stock["price"],
        change_rate=stock["change_rate"],
        sign=sign,
        vol=stock["volume_100m"],
        abs_cr=abs(stock["change_rate"]),
    )


# ──────────────────────────────────────────
# 3. 텔레그램 전송
# ──────────────────────────────────────────

def tg_send(text: str):
    """텔레그램 봇으로 메시지 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text}, timeout=10)
    except Exception as e:
        print(f"  텔레그램 전송 오류: {e}")


def send_posts_to_telegram(posts: list, date_str: str):
    """게시글 전체를 텔레그램으로 전송"""
    # 헤더
    tg_send(f"📊 키움 서포터즈 게시글 | {date_str}\n오늘 거래대금 상위 10종목 게시글 {len(posts)}개")
    time.sleep(0.5)

    for p in posts:
        msg = f"[{p['num']:02d}/{len(posts)}] {p['stock']} · {p['type']}\n\n{p['body']}"
        tg_send(msg)
        time.sleep(0.3)   # 텔레그램 rate limit 방지

    tg_send("✅ 전송 완료! 위 게시글 복붙해서 키움 커뮤니티에 올려주세요 🚀")


# ──────────────────────────────────────────
# 4. 메인 실행
# ──────────────────────────────────────────

def daily_generate():
    now = datetime.now()
    print(f"\n{'='*60}")
    print(f"  키움 서포터즈 게시글 생성  {now.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    date_str = get_recent_trading_day()
    print(f"📊 기준일: {date_str}  |  거래대금 상위 10종목 조회 중...")

    stocks = get_top10_stocks(date_str)
    if not stocks:
        print("❌ 종목 데이터 조회 실패")
        tg_send("❌ 키움 서포터즈: 오늘 종목 데이터 조회 실패")
        return

    print(f"✅ {len(stocks)}종목 조회 완료\n")

    for k in _used:
        _used[k] = []

    posts = []
    for i, s in enumerate(stocks):
        post_type = POST_TYPES[i % 3]
        try:
            body = generate_post(s, post_type)
            posts.append({"num": i+1, "stock": s["name"], "type": post_type, "body": body})
            print(f"  [{i+1:02d}/10] {s['name']:<12} ✅")
        except Exception as e:
            print(f"  [{i+1:02d}/10] {s['name']:<12} ❌  {e}")

    # 파일 저장
    save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supporter_posts")
    os.makedirs(save_dir, exist_ok=True)
    fname = os.path.join(save_dir, f"posts_{now.strftime('%Y%m%d_%H%M')}.txt")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(f"키움 서포터즈 게시글  |  {now.strftime('%Y년 %m월 %d일')}\n")
        f.write(f"기준일: {date_str}\n{'='*60}\n\n")
        for p in posts:
            f.write(f"[{p['num']:02d}] {p['stock']}  |  {p['type']}\n{'-'*40}\n{p['body']}\n\n")

    print(f"\n✅ {len(posts)}개 생성 완료  →  {fname}")

    # 텔레그램 전송
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        print("📱 텔레그램 전송 중...")
        send_posts_to_telegram(posts, date_str)
        print("✅ 텔레그램 전송 완료")
    else:
        print("⚠️  텔레그램 미설정 - 파일만 저장됨")

    return posts


if __name__ == "__main__":
    if "--now" in sys.argv or "-n" in sys.argv:
        daily_generate()
    else:
        print("🚀 키움 서포터즈 자동 생성기  |  매일 08:30 실행")
        print("   즉시 실행: python kiwoom_supporter.py --now\n")
        schedule.every().day.at("08:30").do(daily_generate)
        while True:
            schedule.run_pending()
            time.sleep(30)
