# -*- coding: utf-8 -*-
"""
공모주 인사이트 자동수집 웹 - Render FIX FULL VERSION

Render Start Command:
  gunicorn server:app --bind 0.0.0.0:$PORT

로컬 실행:
  pip install -r requirements.txt
  python server.py
  http://127.0.0.1:5077 접속

핵심 수정:
  - public/Profile 폴더가 없어도 첫 화면이 열리도록 HTML을 server.py 안에 포함
  - Render 404 Not Found 문제 방지
  - /api/ipos 자동수집 유지
  - /api/health 상태 확인 가능
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, Response

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
CACHE_FILE = DATA_DIR / "ipo_cache.json"

DATA_DIR.mkdir(exist_ok=True)

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

SOURCES = [
    {
        "name": "38커뮤니케이션",
        "url": "https://www.38.co.kr/html/fund/?o=k",
        "base": "https://www.38.co.kr",
        "type": "38",
    },
    {
        "name": "IPO38",
        "url": "https://www.ipo38.co.kr/ipo/?key=6",
        "base": "https://www.ipo38.co.kr",
        "type": "38",
    },
]


@dataclass
class IPOItem:
    id: str
    name: str
    market: str = "확인 필요"
    sector: str = "확인 필요"
    subscriptionStart: str = ""
    subscriptionEnd: str = ""
    listingDate: str = "미정"
    manager: str = "확인 필요"
    priceBand: str = "확인 필요"
    finalPrice: str = "미정"
    competitionRate: str = "예정"
    overview: str = ""
    outlook: str = ""
    risks: List[str] = field(default_factory=list)
    score: int = 50
    source: str = ""
    sourceUrl: str = ""
    detailUrl: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_korean_number(text: str) -> Optional[float]:
    if not text:
        return None

    cleaned = (
        text.replace(",", "")
        .replace(":", "")
        .replace("대1", "")
        .replace("원", "")
        .strip()
    )

    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)

    if not match:
        return None

    try:
        return float(match.group(1))
    except Exception:
        return None


def normalize_date_range(text: str, default_year: int) -> Optional[Tuple[str, str]]:
    """
    2026.07.01~07.02
    05.11 ~ 05.12
    2026/05/11~2026/05/12
    같은 문자열을 YYYY-MM-DD 두 개로 변환한다.
    """
    if not text:
        return None

    raw = normalize_space(text)
    raw = raw.replace("/", ".").replace("-", ".")
    raw = re.sub(r"\s+", "", raw)

    match = re.search(
        r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})\s*[~∼]\s*(?:(20\d{2})\.)?(?:(\d{1,2})\.)?(\d{1,2})",
        raw,
    )

    if match:
        y1 = int(match.group(1) or default_year)
        m1 = int(match.group(2))
        d1 = int(match.group(3))
        y2 = int(match.group(4) or y1)
        m2 = int(match.group(5) or m1)
        d2 = int(match.group(6))

        return f"{y1:04d}-{m1:02d}-{d1:02d}", f"{y2:04d}-{m2:02d}-{d2:02d}"

    match = re.search(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})", raw)

    if match:
        y = int(match.group(1) or default_year)
        m = int(match.group(2))
        d = int(match.group(3))

        return f"{y:04d}-{m:02d}-{d:02d}", f"{y:04d}-{m:02d}-{d:02d}"

    return None


def fetch_html(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=15)
    response.raise_for_status()

    apparent_encoding = response.apparent_encoding or ""

    if apparent_encoding:
        response.encoding = apparent_encoding
    elif not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = "euc-kr"

    return response.text


def infer_sector(name: str) -> str:
    if "스팩" in name or "기업인수목적" in name:
        return "SPAC"

    if any(keyword in name for keyword in ["바이오", "헬스", "제약", "메디", "셀", "로직스"]):
        return "바이오 / 헬스케어"

    if any(keyword in name for keyword in ["로보", "비젼", "비전", "AI", "에이아이", "테크", "소프트", "락스"]):
        return "AI / 로봇 / 소프트웨어"

    if any(keyword in name for keyword in ["에너지", "배터리", "전지", "소재", "그린"]):
        return "에너지 / 소재"

    if any(keyword in name for keyword in ["스튜디오", "콘텐츠", "엔터"]):
        return "콘텐츠 / 엔터테인먼트"

    if any(keyword in name for keyword in ["푸드", "식품"]):
        return "식품 / 소비재"

    return "확인 필요"


def infer_market(name: str, row_text: str) -> str:
    if "스팩" in name or "기업인수목적" in name:
        return "SPAC"

    if "코스피" in row_text or "유가증권" in row_text:
        return "KOSPI"

    if "코스닥" in row_text:
        return "KOSDAQ"

    return "확인 필요"


def calc_score(final_price: str, price_band: str, competition_rate: str, sector: str) -> int:
    score = 50

    competition = parse_korean_number(competition_rate)

    if competition is not None:
        if competition >= 2500:
            score += 28
        elif competition >= 1500:
            score += 23
        elif competition >= 1000:
            score += 18
        elif competition >= 500:
            score += 12
        elif competition >= 100:
            score += 6
        elif competition < 20:
            score -= 8

    final_price_number = parse_korean_number(final_price)
    band_numbers = [
        float(number.replace(",", ""))
        for number in re.findall(r"\d[\d,]*", price_band or "")
    ]

    if final_price_number and band_numbers:
        high = max(band_numbers)
        low = min(band_numbers)

        if final_price_number >= high:
            score += 12
        elif final_price_number <= low:
            score -= 8

    if any(keyword in sector for keyword in ["AI", "로봇", "바이오", "헬스케어", "소프트웨어"]):
        score += 3

    if "SPAC" in sector:
        score -= 5

    return max(15, min(95, score))


def make_analysis(item: IPOItem) -> IPOItem:
    item.sector = item.sector if item.sector != "확인 필요" else infer_sector(item.name)
    item.score = calc_score(item.finalPrice, item.priceBand, item.competitionRate, item.sector)

    if not item.overview:
        item.overview = (
            f"{item.name}은/는 공개 공모주 일정에 등록된 종목입니다. "
            f"현재 자동 분류 업종은 '{item.sector}'이며, 정확한 사업 내용은 증권신고서와 주관사 투자설명서를 통해 추가 확인하는 것이 좋습니다."
        )

    if not item.outlook:
        if "SPAC" in item.sector:
            item.outlook = (
                "스팩 종목은 합병 대상 기업이 확정되기 전까지 일반 사업회사와 평가 방식이 다릅니다. "
                "합병 대상의 질과 합병 성공 가능성이 핵심입니다."
            )
        elif item.score >= 70:
            item.outlook = (
                "청약경쟁률, 공모가 흐름, 시장 관심도를 기준으로 볼 때 단기 흥행 관심도는 높은 편입니다. "
                "다만 상장일 유통가능물량과 의무보유확약 비율은 반드시 확인해야 합니다."
            )
        elif item.score >= 55:
            item.outlook = (
                "관심을 둘 만한 종목이지만, 수요예측 결과와 공모가 확정 위치를 본 뒤 판단하는 것이 좋습니다. "
                "업종 성장성보다 실제 실적과 상장일 매물 부담 확인이 중요합니다."
            )
        else:
            item.outlook = (
                "현재 공개 정보만으로는 흥행 강도를 높게 판단하기 어렵습니다. "
                "수요예측, 청약경쟁률, 공모가, 보호예수 조건을 추가 확인해야 합니다."
            )

    if not item.risks:
        item.risks = [
            "상장일 유통가능물량 확인 필요",
            "의무보유확약 비율 확인 필요",
            "공모가 적정성 및 실적 지속성 확인 필요",
        ]

    return item


def parse_38_table(html: str, source: Dict[str, str], default_year: int) -> List[IPOItem]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[IPOItem] = []

    for tr in soup.find_all("tr"):
        cells = [
            normalize_space(td.get_text(" ", strip=True))
            for td in tr.find_all(["td", "th"])
        ]

        if len(cells) < 3:
            continue

        row_text = " ".join(cells)

        if not re.search(r"\d{1,4}[./]\d{1,2}[./]\d{1,2}|[01]?\d\.[0-3]?\d", row_text):
            continue

        if not any(keyword in row_text for keyword in ["~", "∼", "청약", "공모", "증권", "투자", "상장"]):
            continue

        name = cells[0].replace("분석보기", "").replace("기업개요", "").strip()
        name = re.sub(r"\s+", " ", name)

        if not name or name in ("종목명", "기업명", "회사명") or len(name) > 40:
            continue

        date_range = None

        for cell in cells:
            date_range = normalize_date_range(cell, default_year)

            if date_range:
                break

        if not date_range:
            continue

        detail_url = ""
        link = tr.find("a", href=True)

        if link:
            detail_url = urljoin(source["base"], link["href"])

        final_price = "미정"
        price_band = "확인 필요"
        competition = "예정"
        manager = "확인 필요"

        for cell in cells:
            if re.search(r"\d[\d,]*\s*[~∼-]\s*\d[\d,]*", cell):
                price_band = cell

            if ":1" in cell or "대1" in cell:
                competition = cell

            if "증권" in cell or "투자" in cell:
                manager = cell

        for cell in cells:
            if cell in (name, price_band, competition, manager):
                continue

            if re.fullmatch(r"[\d,]+", cell):
                final_price = cell + "원"
                break

            if re.fullmatch(r"-", cell):
                final_price = "미정"

        item = IPOItem(
            id=f"{name}-{date_range[0]}".replace(" ", "-"),
            name=name,
            market=infer_market(name, row_text),
            sector=infer_sector(name),
            subscriptionStart=date_range[0],
            subscriptionEnd=date_range[1],
            manager=manager,
            priceBand=price_band,
            finalPrice=final_price,
            competitionRate=competition,
            source=source["name"],
            sourceUrl=source["url"],
            detailUrl=detail_url,
        )

        items.append(make_analysis(item))

    return items


def dedupe_items(items: List[IPOItem]) -> List[IPOItem]:
    result: Dict[str, IPOItem] = {}

    for item in items:
        key = f"{item.name}-{item.subscriptionStart}-{item.subscriptionEnd}"
        existing = result.get(key)

        if existing is None:
            result[key] = item
            continue

        current_size = len(json.dumps(item.to_dict(), ensure_ascii=False))
        existing_size = len(json.dumps(existing.to_dict(), ensure_ascii=False))

        if current_size > existing_size:
            result[key] = item

    return sorted(result.values(), key=lambda item: (item.subscriptionStart, item.name))


def fallback_items(year: int) -> List[IPOItem]:
    return [
        IPOItem(
            id=f"fallback-{year}",
            name="자동수집 대기",
            market="확인 필요",
            sector="확인 필요",
            subscriptionStart=f"{year}-01-01",
            subscriptionEnd=f"{year}-01-01",
            manager="확인 필요",
            priceBand="확인 필요",
            finalPrice="미정",
            competitionRate="예정",
            overview="외부 공모주 페이지 수집이 일시적으로 실패했습니다. 인터넷에서 새로고침을 다시 눌러 주세요.",
            outlook="Render 무료 서버 첫 실행, 외부 사이트 응답 지연, 또는 사이트 구조 변경일 수 있습니다.",
            risks=[
                "외부 사이트 구조 변경 가능성",
                "일시적인 네트워크 오류 가능성",
                "청약 전 DART/KRX/주관사 공지 재확인 필요",
            ],
            score=50,
            source="fallback",
            sourceUrl="",
            detailUrl="",
        )
    ]


def collect_ipos(force: bool = False, year: Optional[int] = None) -> Dict[str, Any]:
    year = year or datetime.now().year

    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))

            if time.time() - cached.get("cachedAt", 0) < 60 * 60 * 4:
                return cached
        except Exception:
            pass

    all_items: List[IPOItem] = []
    errors: List[str] = []

    for source in SOURCES:
        try:
            html = fetch_html(source["url"])
            parsed = parse_38_table(html, source, year)
            all_items.extend(parsed)
        except Exception as exc:
            errors.append(f"{source['name']}: {exc}")

    items = dedupe_items(all_items)

    if not items:
        items = fallback_items(year)

    payload = {
        "ok": True,
        "cachedAt": time.time(),
        "cachedAtText": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "items": [item.to_dict() for item in items],
        "errors": errors,
        "sources": SOURCES,
        "note": "API 인증키 없이 공개 페이지를 읽어온 결과입니다. 외부 사이트 구조 변경 시 일부 누락될 수 있습니다.",
    }

    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


HTML_PAGE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <meta name="theme-color" content="#020617" />
  <title>공모주 인사이트 자동수집</title>
  <style>
    :root {
      --ink: #0f172a;
      --muted: #64748b;
      --line: #e2e8f0;
      --soft: #f1f5f9;
      --dark: #020617;
      --green: #047857;
      --amber: #b45309;
      --red: #b91c1c;
      --yellow: #fee500;
      --blue: #2563eb;
      --shadow: 0 18px 45px rgba(15, 23, 42, 0.08);
      --radius: 26px;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
      color: var(--ink);
      background: linear-gradient(180deg, #f8fafc 0%, #ffffff 45%, #eef2f7 100%);
    }

    button,
    input,
    select,
    textarea {
      font-family: inherit;
    }

    .wrap {
      max-width: 1400px;
      margin: 0 auto;
      padding: 24px 16px 32px;
    }

    .hero {
      background: radial-gradient(circle at top right, rgba(96, 165, 250, .35), transparent 30%),
                  radial-gradient(circle at bottom left, rgba(45, 212, 191, .22), transparent 34%),
                  var(--dark);
      color: white;
      border-radius: 34px;
      padding: 42px;
      box-shadow: var(--shadow);
    }

    .hero-grid {
      display: grid;
      grid-template-columns: 1fr 350px;
      gap: 28px;
      align-items: end;
    }

    .badge {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      background: rgba(255, 255, 255, .1);
      border: 1px solid rgba(255, 255, 255, .12);
      padding: 9px 14px;
      border-radius: 999px;
      color: #dbeafe;
      font-size: 14px;
      font-weight: 800;
      margin-bottom: 18px;
    }

    h1 {
      margin: 0;
      max-width: 980px;
      font-size: clamp(34px, 5vw, 64px);
      letter-spacing: -.055em;
      line-height: 1.02;
    }

    .hero p {
      max-width: 850px;
      color: #cbd5e1;
      font-size: 18px;
      line-height: 1.75;
      margin: 18px 0 0;
    }

    .statbox {
      display: grid;
      gap: 12px;
    }

    .stat {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, .1);
      border: 1px solid rgba(255, 255, 255, .1);
      border-radius: 19px;
      padding: 16px;
    }

    .stat span {
      color: #cbd5e1;
      font-size: 14px;
    }

    .stat strong {
      font-size: 20px;
    }

    .filters,
    .month-area,
    .detail,
    .ipo-card,
    .share-panel,
    .source-panel {
      background: rgba(255, 255, 255, .92);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
    }

    .source-panel,
    .month-area,
    .share-panel {
      margin-top: 22px;
      padding: 22px;
    }

    .source-panel h2,
    .month-area h2,
    .share-panel h2 {
      margin: 0 0 16px;
      font-size: 20px;
    }

    .notice {
      background: #ecfeff;
      border: 1px solid #a5f3fc;
      color: #155e75;
      border-radius: 18px;
      padding: 14px 16px;
      line-height: 1.65;
      font-size: 14px;
      font-weight: 800;
    }

    .top-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 16px;
    }

    .btn {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 7px;
      min-height: 44px;
      padding: 0 16px;
      border-radius: 14px;
      border: 1px solid var(--line);
      text-decoration: none;
      font-weight: 900;
      font-size: 14px;
      cursor: pointer;
      background: white;
      color: var(--ink);
    }

    .btn.primary {
      background: #0f172a;
      color: white;
      border-color: #0f172a;
    }

    .btn.blue {
      background: var(--blue);
      color: white;
      border-color: var(--blue);
    }

    .btn.kakao {
      background: var(--yellow);
      border-color: #e8d000;
      color: #191919;
    }

    .month-tabs {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 8px;
      margin-bottom: 18px;
    }

    .month-btn {
      border: 1px solid var(--line);
      background: var(--soft);
      color: var(--muted);
      border-radius: 15px;
      min-height: 52px;
      cursor: pointer;
      font-weight: 900;
      transition: .16s ease;
    }

    .month-btn:hover {
      transform: translateY(-1px);
      background: white;
    }

    .month-btn.active {
      background: #0f172a;
      color: white;
      border-color: #0f172a;
    }

    .month-summary {
      display: grid;
      grid-template-columns: 315px 1fr;
      gap: 16px;
    }

    .month-kpi {
      background: #0f172a;
      color: white;
      border-radius: 24px;
      padding: 20px;
    }

    .month-kpi .big {
      font-size: 44px;
      font-weight: 1000;
      letter-spacing: -.05em;
      margin: 4px 0;
    }

    .month-kpi p {
      color: #cbd5e1;
      line-height: 1.7;
      margin: 10px 0 0;
      font-size: 14px;
    }

    .schedule-grid {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
    }

    .schedule-mini {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 18px;
      padding: 15px;
      cursor: pointer;
      transition: .16s ease;
      min-height: 152px;
    }

    .schedule-mini:hover {
      transform: translateY(-2px);
      box-shadow: 0 14px 35px rgba(15, 23, 42, .09);
    }

    .schedule-mini.active {
      border-color: #0f172a;
      outline: 3px solid rgba(15, 23, 42, .08);
    }

    .schedule-mini .date {
      color: var(--muted);
      font-size: 13px;
      font-weight: 800;
    }

    .schedule-mini h3 {
      margin: 8px 0 4px;
      font-size: 18px;
      letter-spacing: -.03em;
    }

    .schedule-mini .sub {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }

    .filters {
      margin: 22px 0;
      padding: 15px;
      display: grid;
      grid-template-columns: 1fr 170px 170px 170px;
      gap: 12px;
    }

    input,
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: var(--soft);
      border-radius: 18px;
      padding: 0 16px;
      outline: none;
      color: var(--ink);
      font-size: 15px;
    }

    input,
    select {
      height: 52px;
    }

    textarea {
      min-height: 170px;
      padding: 14px 16px;
      line-height: 1.7;
      resize: vertical;
    }

    input:focus,
    select:focus,
    textarea:focus {
      background: white;
      border-color: #94a3b8;
    }

    .main {
      display: grid;
      grid-template-columns: 440px 1fr;
      gap: 22px;
      align-items: start;
    }

    .list {
      display: grid;
      gap: 15px;
    }

    .ipo-card {
      padding: 19px;
      cursor: pointer;
      transition: .18s ease;
    }

    .ipo-card:hover {
      transform: translateY(-2px);
      box-shadow: 0 22px 55px rgba(15, 23, 42, .12);
    }

    .ipo-card.active {
      border-color: #0f172a;
      outline: 3px solid rgba(15, 23, 42, .08);
    }

    .card-top {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 14px;
    }

    .ipo-card h3 {
      margin: 0;
      font-size: 21px;
      letter-spacing: -.03em;
    }

    .sector {
      margin-top: 5px;
      color: var(--muted);
      font-size: 14px;
    }

    .pill {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 12px;
      font-weight: 900;
      border: 1px solid var(--line);
      white-space: nowrap;
    }

    .pill.hot {
      color: var(--red);
      background: #fef2f2;
      border-color: #fecaca;
    }

    .pill.good {
      color: var(--green);
      background: #ecfdf5;
      border-color: #a7f3d0;
    }

    .pill.watch {
      color: var(--amber);
      background: #fffbeb;
      border-color: #fde68a;
    }

    .pill.weak {
      color: #475569;
      background: #f8fafc;
      border-color: #e2e8f0;
    }

    .meta {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 9px;
      margin: 15px 0;
    }

    .meta div {
      background: var(--soft);
      padding: 13px;
      border-radius: 16px;
    }

    .meta small {
      display: block;
      color: var(--muted);
      margin-bottom: 4px;
    }

    .meta b {
      font-size: 14px;
    }

    .score-row {
      display: flex;
      justify-content: space-between;
      font-size: 14px;
      margin-bottom: 8px;
    }

    .bar {
      height: 10px;
      border-radius: 999px;
      background: #e2e8f0;
      overflow: hidden;
    }

    .bar span {
      display: block;
      height: 100%;
      background: #0f172a;
      border-radius: 999px;
    }

    .detail {
      position: sticky;
      top: 22px;
      padding: 28px;
    }

    .detail-head {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: flex-start;
    }

    .eyebrow {
      color: var(--muted);
      font-weight: 900;
      font-size: 13px;
      margin-bottom: 8px;
    }

    .detail h2 {
      margin: 0;
      font-size: 40px;
      letter-spacing: -.05em;
    }

    .detail-sub {
      color: var(--muted);
      margin-top: 7px;
    }

    .info-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 12px;
      margin: 24px 0;
    }

    .info {
      background: var(--soft);
      border-radius: 19px;
      padding: 17px;
    }

    .info small {
      color: var(--muted);
      font-weight: 800;
    }

    .info b {
      display: block;
      margin-top: 6px;
      font-size: 15px;
    }

    .heat {
      background: #020617;
      color: white;
      border-radius: 24px;
      padding: 22px;
      margin-bottom: 22px;
    }

    .heat .bar {
      background: rgba(255, 255, 255, .18);
    }

    .heat .bar span {
      background: white;
    }

    .heat p {
      color: #cbd5e1;
      line-height: 1.7;
      font-size: 14px;
      margin: 13px 0 0;
    }

    .section {
      margin: 18px 0;
    }

    .section h3 {
      margin: 0 0 8px;
      font-size: 17px;
    }

    .section .box {
      border: 1px solid #eef2f7;
      border-radius: 19px;
      padding: 17px;
      color: var(--muted);
      line-height: 1.8;
      font-size: 15px;
    }

    .risk {
      margin: 0;
      padding-left: 19px;
    }

    .risk li {
      margin: 6px 0;
    }

    .buttons {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 22px;
    }

    .loading {
      padding: 25px;
      text-align: center;
      color: var(--muted);
      font-weight: 900;
    }

    @media(max-width:1120px) {
      .hero-grid,
      .main,
      .month-summary {
        grid-template-columns: 1fr;
      }

      .detail {
        position: static;
      }

      .filters {
        grid-template-columns: 1fr;
      }

      .schedule-grid {
        grid-template-columns: 1fr;
      }

      .month-tabs {
        grid-template-columns: repeat(4, 1fr);
      }
    }

    @media(max-width:640px) {
      .hero {
        padding: 26px 20px;
        border-radius: 26px;
      }

      h1 {
        font-size: 34px;
        line-height: 1.08;
      }

      .hero p {
        font-size: 15px;
      }

      .stat {
        padding: 13px;
      }

      .source-panel,
      .month-area,
      .share-panel,
      .detail {
        padding: 18px;
        border-radius: 22px;
      }

      .month-tabs {
        grid-template-columns: repeat(3, 1fr);
      }

      .month-btn {
        min-height: 48px;
      }

      .detail h2 {
        font-size: 32px;
      }

      .info-grid,
      .meta {
        grid-template-columns: 1fr;
      }

      .btn {
        width: 100%;
      }

      .top-actions,
      .buttons {
        display: grid;
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <div class="hero-grid">
        <div>
          <div class="badge">✓ Auto Fetch · Monthly IPO Calendar · Demand Heat</div>
          <h1>공모주 청약일정을 자동으로 읽어오는 분석 웹</h1>
          <p>서버가 공개 공모주 페이지를 읽어오고, 웹은 월별 청약 일정·회사개요·전망·흥행 판단·카카오톡 공유 문구를 자동으로 구성합니다.</p>
        </div>
        <div class="statbox">
          <div class="stat"><span>선택월 공모주</span><strong id="statMonthCount">0개</strong></div>
          <div class="stat"><span>전체 수집 종목</span><strong id="statAllCount">0개</strong></div>
          <div class="stat"><span>마지막 갱신</span><strong id="statUpdated">-</strong></div>
        </div>
      </div>
    </section>

    <section class="source-panel">
      <h2>자동 수집 방식</h2>
      <div class="notice">
        이 웹은 손으로 종목을 추가하는 구조가 아니라, 서버가 공개 공모주 페이지를 읽어와 자동 표시합니다.
        외부 사이트 HTML 구조가 바뀌면 일부 항목이 누락될 수 있어, 청약 전에는 원본 페이지와 DART/KRX도 함께 확인하는 것이 좋습니다.
      </div>
      <div class="top-actions">
        <button class="btn primary" onclick="loadData(true)">인터넷에서 새로고침</button>
        <button class="btn blue" onclick="loadData(false)">캐시 불러오기</button>
        <a class="btn" href="https://www.38.co.kr/html/fund/?o=k" target="_blank">38 원본 확인 ↗</a>
        <a class="btn" href="https://kind.krx.co.kr/listinvstg/pubofrschdl.do?method=searchPubofrScholMain" target="_blank">KRX KIND 확인 ↗</a>
      </div>
    </section>

    <section class="month-area">
      <h2>월별 공모주 청약 일정 요약</h2>
      <div id="monthTabs" class="month-tabs"></div>
      <div class="month-summary">
        <div class="month-kpi">
          <div id="monthTitle">월 선택</div>
          <div class="big" id="monthCount">0개</div>
          <p id="monthComment">인터넷에서 데이터를 불러오는 중입니다.</p>
        </div>
        <div id="scheduleGrid" class="schedule-grid"><div class="loading">데이터 로딩 중...</div></div>
      </div>
    </section>

    <section class="filters">
      <input id="search" placeholder="회사명, 업종, 주관사 검색" />
      <select id="market">
        <option>전체</option>
        <option>KOSDAQ</option>
        <option>KOSPI</option>
        <option>SPAC</option>
        <option>확인 필요</option>
      </select>
      <select id="sort">
        <option value="date">청약일순</option>
        <option value="score">흥행점수순</option>
        <option value="name">가나다순</option>
      </select>
      <select id="viewMode">
        <option value="month">선택월만 보기</option>
        <option value="all">전체 보기</option>
      </select>
    </section>

    <main class="main">
      <div>
        <div id="list" class="list"><div class="ipo-card">데이터 로딩 중...</div></div>
        <section class="share-panel">
          <h2>카카오톡으로 보낼 내용</h2>
          <textarea id="shareText" readonly></textarea>
          <div class="buttons">
            <button class="btn kakao" onclick="copyShareText()">카카오톡 문구 복사</button>
          </div>
        </section>
      </div>
      <aside id="detail" class="detail"></aside>
    </main>
  </div>

  <script>
    let ipos = [];
    const YEAR = new Date().getFullYear();
    let selectedMonth = new Date().getMonth() + 1;
    let selectedId = null;
    const $ = id => document.getElementById(id);

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function getMonth(dateString) {
      return dateString ? Number(String(dateString).slice(5, 7)) : 0;
    }

    function getStatus(score) {
      score = Number(score || 0);

      if (score >= 80) return ["hot", "흥행 강함"];
      if (score >= 65) return ["good", "관심 높음"];
      if (score >= 50) return ["watch", "관망 필요"];

      return ["weak", "주의"];
    }

    function scoreBar(score) {
      score = Number(score || 0);

      return `
        <div class="score-row"><span>흥행 점수</span><b>${score}/100</b></div>
        <div class="bar"><span style="width:${Math.max(0, Math.min(100, score))}%"></span></div>
      `;
    }

    function monthItems() {
      return ipos
        .filter(item => getMonth(item.subscriptionStart) === selectedMonth)
        .sort((a, b) => new Date(a.subscriptionStart) - new Date(b.subscriptionStart));
    }

    function targetItems() {
      return $("viewMode").value === "all" ? ipos : monthItems();
    }

    function currentIpo() {
      return ipos.find(item => item.id === selectedId) || monthItems()[0] || ipos[0];
    }

    async function loadData(force = false) {
      $("list").innerHTML = `<div class="ipo-card">인터넷 공모주 데이터를 불러오는 중...</div>`;

      try {
        const response = await fetch(`/api/ipos?year=${YEAR}&force=${force ? 1 : 0}`);
        const data = await response.json();

        ipos = data.items || [];
        $("statUpdated").textContent = data.cachedAtText ? data.cachedAtText.slice(5, 16) : "-";

        const currentMonthFirst = monthItems()[0];

        if (currentMonthFirst) {
          selectedId = currentMonthFirst.id;
        } else if (ipos[0]) {
          selectedMonth = getMonth(ipos[0].subscriptionStart);
          selectedId = ipos[0].id;
        }

        renderAll();

        if (data.errors && data.errors.length) {
          console.warn("수집 경고:", data.errors);
        }
      } catch (error) {
        $("list").innerHTML = `
          <div class="ipo-card">
            <h3>수집 실패</h3>
            <div class="sector">Render 서버가 켜져 있는지 확인하세요. 잠시 후 새로고침을 다시 눌러도 됩니다.</div>
          </div>
        `;
        $("scheduleGrid").innerHTML = `<div class="loading">수집 실패: ${escapeHtml(error.message)}</div>`;
      }
    }

    function filteredList() {
      const query = $("search").value.toLowerCase();
      const market = $("market").value;
      const sort = $("sort").value;

      return targetItems()
        .filter(item => {
          const text = `${item.name} ${item.sector} ${item.manager}`.toLowerCase();

          return text.includes(query) && (market === "전체" || item.market === market);
        })
        .sort((a, b) => {
          if (sort === "score") return Number(b.score || 0) - Number(a.score || 0);
          if (sort === "name") return a.name.localeCompare(b.name, "ko");

          return new Date(a.subscriptionStart) - new Date(b.subscriptionStart);
        });
    }

    function renderMonthTabs() {
      $("monthTabs").innerHTML = Array.from({ length: 12 }, (_, index) => {
        const month = index + 1;
        const count = ipos.filter(item => getMonth(item.subscriptionStart) === month).length;

        return `
          <button class="month-btn ${month === selectedMonth ? "active" : ""}" onclick="selectMonth(${month})">
            ${month}월<br><small>${count}개</small>
          </button>
        `;
      }).join("");
    }

    function renderMonthSummary() {
      const items = monthItems();
      const avg = items.length
        ? Math.round(items.reduce((sum, item) => sum + Number(item.score || 0), 0) / items.length)
        : 0;

      $("monthTitle").textContent = `${YEAR}년 ${selectedMonth}월`;
      $("monthCount").textContent = `${items.length}개`;
      $("monthComment").textContent = items.length
        ? `선택월 평균 흥행점수는 ${avg}점입니다. 종목을 누르면 상세 분석이 표시됩니다.`
        : "이 월에는 수집된 청약 일정이 없습니다.";

      $("scheduleGrid").innerHTML = items.length
        ? items.map(item => {
            const [key, label] = getStatus(item.score);

            return `
              <article class="schedule-mini ${item.id === selectedId ? "active" : ""}" onclick="selectIpo('${escapeHtml(item.id)}')">
                <div class="date">${escapeHtml(item.subscriptionStart)} ~ ${escapeHtml(String(item.subscriptionEnd || "").slice(5))}</div>
                <h3>${escapeHtml(item.name)}</h3>
                <div class="sub">${escapeHtml(item.market)} · ${escapeHtml(item.sector)}<br>${escapeHtml(item.manager || "주관사 확인 필요")}</div>
                <div style="margin-top:10px;"><span class="pill ${key}">${label}</span></div>
              </article>
            `;
          }).join("")
        : `<div class="loading">수집된 일정이 없습니다.</div>`;
    }

    function renderStats() {
      $("statMonthCount").textContent = `${monthItems().length}개`;
      $("statAllCount").textContent = `${ipos.length}개`;
    }

    function renderList() {
      const list = filteredList();

      $("list").innerHTML = list.length
        ? list.map(item => {
            const [key, label] = getStatus(item.score);

            return `
              <article class="ipo-card ${item.id === selectedId ? "active" : ""}" onclick="selectIpo('${escapeHtml(item.id)}')">
                <div class="card-top">
                  <div>
                    <h3>${escapeHtml(item.name)} <span class="pill weak">${escapeHtml(item.market)}</span></h3>
                    <div class="sector">${escapeHtml(item.sector || "")}</div>
                  </div>
                  <span class="pill ${key}">${label}</span>
                </div>
                <div class="meta">
                  <div><small>청약일</small><b>${escapeHtml(item.subscriptionStart)} ~ ${escapeHtml(String(item.subscriptionEnd || "").slice(5))}</b></div>
                  <div><small>주관사</small><b>${escapeHtml(item.manager || "확인 필요")}</b></div>
                </div>
                ${scoreBar(item.score)}
              </article>
            `;
          }).join("")
        : `
          <article class="ipo-card">
            <h3>검색 결과 없음</h3>
            <div class="sector">조건에 맞는 종목이 없습니다.</div>
          </article>
        `;

      renderDetail();
      renderShareText();
    }

    function renderDetail() {
      const item = currentIpo();

      if (!item) {
        $("detail").innerHTML = `<div class="eyebrow">NO DATA</div><h2>데이터 없음</h2>`;

        return;
      }

      const [key, label] = getStatus(item.score);

      $("detail").innerHTML = `
        <div class="detail-head">
          <div>
            <div class="eyebrow">IPO ANALYSIS</div>
            <h2>${escapeHtml(item.name)}</h2>
            <div class="detail-sub">${escapeHtml(item.market)} · ${escapeHtml(item.sector || "")}</div>
          </div>
          <span class="pill ${key}">${label}</span>
        </div>

        <div class="info-grid">
          <div class="info"><small>청약 일정</small><b>${escapeHtml(item.subscriptionStart)} ~ ${escapeHtml(item.subscriptionEnd)}</b></div>
          <div class="info"><small>주관사</small><b>${escapeHtml(item.manager || "확인 필요")}</b></div>
          <div class="info"><small>희망 공모가</small><b>${escapeHtml(item.priceBand || "확인 필요")}</b></div>
          <div class="info"><small>청약경쟁률</small><b>${escapeHtml(item.competitionRate || "예정")}</b></div>
        </div>

        <div class="heat">
          <b>흥행 판단</b>
          <div style="margin-top:14px;">${scoreBar(item.score)}</div>
          <p>수집된 공모가, 청약경쟁률, 업종 키워드를 바탕으로 자동 산정한 참고 점수입니다. 수요예측과 보호예수 정보가 추가되면 더 정확해집니다.</p>
        </div>

        <div class="section"><h3>회사 개요</h3><div class="box">${escapeHtml(item.overview || "회사개요 확인 필요")}</div></div>
        <div class="section"><h3>회사 전망</h3><div class="box">${escapeHtml(item.outlook || "전망 확인 필요")}</div></div>
        <div class="section"><h3>체크해야 할 리스크</h3><div class="box"><ul class="risk">${(item.risks || []).map(risk => `<li>${escapeHtml(risk)}</li>`).join("")}</ul></div></div>
        <div class="section"><h3>출처</h3><div class="box">${escapeHtml(item.source || "")} ${item.detailUrl ? `<br><a href="${escapeHtml(item.detailUrl)}" target="_blank">상세 원본 보기 ↗</a>` : ""}</div></div>

        <div class="buttons">
          ${item.detailUrl ? `<a class="btn primary" href="${escapeHtml(item.detailUrl)}" target="_blank">원본 상세 ↗</a>` : ""}
          <a class="btn" href="${escapeHtml(item.sourceUrl || "https://www.38.co.kr/html/fund/?o=k")}" target="_blank">출처 페이지 ↗</a>
          <button class="btn kakao" onclick="copyShareText()">카카오톡 문구 복사</button>
        </div>
      `;
    }

    function makeShareText() {
      const item = currentIpo();
      const items = monthItems();

      if (!item) {
        return "공모주 데이터가 없습니다.";
      }

      const monthLine = items
        .map(row => `- ${row.name}: ${row.subscriptionStart}~${String(row.subscriptionEnd || "").slice(5)} / ${row.manager || "주관사 확인"} / ${getStatus(row.score)[1]}`)
        .join("\n");

      return `[공모주 인사이트] ${YEAR}년 ${selectedMonth}월 청약 일정

월간 요약: 총 ${items.length}개
${monthLine || "- 수집된 일정 없음"}

선택 종목: ${item.name}
시장/업종: ${item.market} / ${item.sector}
청약일: ${item.subscriptionStart} ~ ${item.subscriptionEnd}
주관사: ${item.manager}
희망 공모가: ${item.priceBand}
청약경쟁률: ${item.competitionRate}
흥행 판단: ${getStatus(item.score)[1]} (${item.score}/100)

회사개요:
${item.overview}

전망:
${item.outlook}

체크 리스크:
${(item.risks || []).map(risk => "- " + risk).join("\n")}

※ 투자 권유가 아닌 정보 정리용 요약입니다.`;
    }

    function renderShareText() {
      $("shareText").value = makeShareText();
    }

    async function copyShareText() {
      try {
        await navigator.clipboard.writeText($("shareText").value);
        alert("카카오톡에 붙여넣을 문구를 복사했습니다.");
      } catch (error) {
        $("shareText").select();
        document.execCommand("copy");
        alert("복사했습니다.");
      }
    }

    function selectMonth(month) {
      selectedMonth = month;

      const first = monthItems()[0];

      if (first) {
        selectedId = first.id;
      }

      renderAll();
    }

    function selectIpo(id) {
      selectedId = id;

      const item = ipos.find(row => row.id === id);

      if (item) {
        selectedMonth = getMonth(item.subscriptionStart);
      }

      renderAll();
    }

    function renderAll() {
      renderMonthTabs();
      renderStats();
      renderMonthSummary();
      renderList();
    }

    ["search", "market", "sort", "viewMode"].forEach(id => {
      $(id).addEventListener("input", renderList);
    });

    loadData(false);
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")


@app.route("/api/ipos")
def api_ipos():
    force = request.args.get("force") == "1"
    year = request.args.get("year", type=int) or datetime.now().year

    return jsonify(collect_ipos(force=force, year=year))


@app.route("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "time": datetime.now().isoformat(),
            "appDir": str(APP_DIR),
            "mode": "single-file-server",
            "message": "server.py 자체에서 첫 화면 HTML을 제공합니다.",
        }
    )


@app.errorhandler(404)
def not_found(_error):
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")


if __name__ == "__main__":
    print("공모주 인사이트 자동수집 웹 - Render FIX FULL VERSION")
    print("브라우저에서 http://127.0.0.1:5077 로 접속하세요.")
    app.run(host="127.0.0.1", port=5077, debug=True)
