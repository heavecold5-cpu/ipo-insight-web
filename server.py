# -*- coding: utf-8 -*-
"""
공모주 인사이트 모바일 최종본

목표:
- 모바일 전용 카드형 공모주 일정 웹
- DART/KRX API 키 없이 공개 웹페이지 우선 수집
- DART 청약달력 → KRX KIND → 38커뮤니케이션 → IPO38 순서로 시도
- 한 소스가 실패해도 서버가 죽지 않도록 처리
- Render 502 방지를 위해 외부 요청 timeout을 짧게 설정
- public/Profile 폴더 없이 server.py 단일 파일로 첫 화면 제공

Render Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

Render Build Command:
pip install -r requirements.txt

로컬 실행:
pip install -r requirements.txt
python server.py
http://127.0.0.1:5077
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, Response, jsonify, request

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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "close",
}

SOURCES = [
    {
        "name": "DART 청약달력",
        "url": "https://dart.fss.or.kr/dsac008/main.do",
        "base": "https://dart.fss.or.kr",
        "type": "dart_calendar",
        "timeout": 5,
    },
    {
        "name": "KRX KIND 공모일정",
        "url": "https://kind.krx.co.kr/listinvstg/pubofrschdl.do?method=searchPubofrScholMain",
        "base": "https://kind.krx.co.kr",
        "type": "generic_table",
        "timeout": 5,
    },
    {
        "name": "38커뮤니케이션",
        "url": "https://www.38.co.kr/html/fund/?o=k",
        "base": "https://www.38.co.kr",
        "type": "generic_table",
        "timeout": 4,
    },
    {
        "name": "IPO38",
        "url": "https://www.ipo38.co.kr/ipo/?key=6",
        "base": "https://www.ipo38.co.kr",
        "type": "generic_table",
        "timeout": 4,
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
        .replace("%", "")
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
    if not text:
        return None

    raw = normalize_space(text)
    raw = raw.replace("/", ".").replace("-", ".")
    raw = raw.replace("년", ".").replace("월", ".").replace("일", "")
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


def fetch_html(source: Dict[str, Any]) -> str:
    response = requests.get(
        source["url"],
        headers=HEADERS,
        timeout=int(source.get("timeout", 5)),
    )
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
    if any(keyword in name for keyword in ["바이오", "헬스", "제약", "메디", "셀", "로직스", "이뮨"]):
        return "바이오 / 헬스케어"
    if any(keyword in name for keyword in ["로보", "비젼", "비전", "AI", "에이아이", "테크", "소프트", "락스"]):
        return "AI / 로봇 / 소프트웨어"
    if any(keyword in name for keyword in ["에너지", "배터리", "전지", "소재", "그린", "SKC"]):
        return "에너지 / 소재"
    if any(keyword in name for keyword in ["스튜디오", "콘텐츠", "엔터"]):
        return "콘텐츠 / 엔터테인먼트"
    if any(keyword in name for keyword in ["푸드", "식품", "피스피스"]):
        return "식품 / 소비재"
    return "확인 필요"


def infer_market(name: str, row_text: str = "") -> str:
    text = f"{name} {row_text}"

    if "스팩" in text or "기업인수목적" in text:
        return "SPAC"
    if "유가증권" in text or "코스피" in text:
        return "KOSPI"
    if "코스닥" in text or re.search(r"(^|\s)코\s", text):
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
            f"{item.name}은/는 공개 공모주 일정에서 수집된 종목입니다. "
            f"현재 자동 분류 업종은 '{item.sector}'입니다. "
            f"정확한 사업 내용은 DART 증권신고서와 주관사 투자설명서를 함께 확인하는 것이 좋습니다."
        )

    if not item.outlook:
        if "SPAC" in item.sector:
            item.outlook = (
                "스팩 종목은 합병 대상 기업이 확정되기 전까지 일반 사업회사와 평가 방식이 다르며, "
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


def parse_dart_calendar(html: str, source: Dict[str, Any], default_year: int) -> List[IPOItem]:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text("\n")
    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    joined = " ".join(lines)

    year_match = re.search(r"(20\d{2})\s*년", joined)
    month_match = re.search(r"(?:^|\s)(\d{1,2})\s*월", joined)

    year = int(year_match.group(1)) if year_match else default_year
    month = int(month_match.group(1)) if month_match else datetime.now().month

    events: Dict[str, Dict[str, Any]] = {}
    current_day: Optional[int] = None

    market_map = {
        "코": "KOSDAQ",
        "유": "KOSPI",
        "기": "기타",
    }

    for line in lines:
        if re.fullmatch(r"\d{1,2}", line):
            day = int(line)

            if 1 <= day <= 31:
                current_day = day

            continue

        inline_date = normalize_date_range(line, year)

        if inline_date:
            current_day = int(inline_date[0][-2:])
            month = int(inline_date[0][5:7])

        if current_day is None:
            continue

        for match in re.finditer(r"([코유기])\s*([^\[\]\n\r]+?)\s*\[(시작|종료)\]", line):
            market_code = match.group(1).strip()
            name = normalize_space(match.group(2))
            event_type = match.group(3).strip()

            if not name:
                continue

            date_value = f"{year:04d}-{month:02d}-{current_day:02d}"

            if name not in events:
                market = market_map.get(market_code, "확인 필요")

                if "스팩" in name or "기업인수목적" in name:
                    market = "SPAC"

                events[name] = {
                    "name": name,
                    "market": market,
                    "start": "",
                    "end": "",
                }

            if event_type == "시작":
                events[name]["start"] = date_value
            elif event_type == "종료":
                events[name]["end"] = date_value

    items: List[IPOItem] = []

    for name, info in events.items():
        start_date = info.get("start") or info.get("end")
        end_date = info.get("end") or info.get("start")

        if not start_date:
            continue

        item = IPOItem(
            id=f"{name}-{start_date}".replace(" ", "-"),
            name=name,
            market=info.get("market", "확인 필요"),
            sector=infer_sector(name),
            subscriptionStart=start_date,
            subscriptionEnd=end_date,
            manager="DART 확인 필요",
            priceBand="DART/주관사 확인 필요",
            finalPrice="미정",
            competitionRate="예정",
            source=source["name"],
            sourceUrl=source["url"],
            detailUrl=source["url"],
        )

        items.append(make_analysis(item))

    return sorted(items, key=lambda item: (item.subscriptionStart, item.name))


def parse_generic_table(html: str, source: Dict[str, Any], default_year: int) -> List[IPOItem]:
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
    source_counts: Dict[str, int] = {}

    for source in SOURCES:
        try:
            html = fetch_html(source)

            if source["type"] == "dart_calendar":
                parsed = parse_dart_calendar(html, source, year)
            else:
                parsed = parse_generic_table(html, source, year)

            source_counts[source["name"]] = len(parsed)
            all_items.extend(parsed)

        except Exception as exc:
            errors.append(f"{source['name']}: {exc}")

    items = dedupe_items(all_items)

    payload = {
        "ok": True,
        "cachedAt": time.time(),
        "cachedAtText": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "items": [item.to_dict() for item in items],
        "errors": errors,
        "sourceCounts": source_counts,
        "sources": SOURCES,
        "note": "API 인증키 없이 공개 웹페이지를 읽어온 결과입니다. 외부 사이트 구조 변경 또는 접속 차단 시 일부 누락될 수 있습니다.",
    }

    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    return payload


HTML_PAGE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>공모주 인사이트 모바일</title>
  <style>
    :root {
      --bg: #f5f7fb;
      --card: #ffffff;
      --ink: #111827;
      --muted: #6b7280;
      --line: #e5e7eb;
      --dark: #020617;
      --blue: #2563eb;
      --green: #047857;
      --amber: #b45309;
      --red: #b91c1c;
      --yellow: #fee500;
      --shadow: 0 12px 28px rgba(15, 23, 42, .08);
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", "Segoe UI", sans-serif;
    }

    button,
    input,
    select,
    textarea {
      font-family: inherit;
    }

    .app {
      max-width: 520px;
      margin: 0 auto;
      min-height: 100vh;
      padding-bottom: 84px;
      background: #f8fafc;
    }

    .top {
      position: sticky;
      top: 0;
      z-index: 20;
      background: rgba(248, 250, 252, .92);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--line);
      padding: 14px 16px 12px;
    }

    .top-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
    }

    .title {
      margin: 0;
      font-size: 22px;
      font-weight: 900;
      letter-spacing: -.04em;
    }

    .subtitle {
      margin-top: 3px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    .refresh {
      border: 0;
      background: var(--dark);
      color: #fff;
      border-radius: 999px;
      padding: 10px 13px;
      font-weight: 900;
      font-size: 13px;
      cursor: pointer;
    }

    .screen {
      display: none;
      padding: 14px 14px 0;
    }

    .screen.active {
      display: block;
    }

    .summary-card {
      background: linear-gradient(135deg, #020617, #172554);
      color: #fff;
      border-radius: 24px;
      padding: 20px;
      box-shadow: var(--shadow);
      margin-bottom: 14px;
    }

    .summary-label {
      font-size: 13px;
      color: #bfdbfe;
      font-weight: 800;
    }

    .summary-main {
      margin-top: 6px;
      font-size: 34px;
      font-weight: 1000;
      letter-spacing: -.06em;
    }

    .summary-sub {
      margin-top: 8px;
      color: #cbd5e1;
      font-size: 13px;
      line-height: 1.6;
      white-space: pre-wrap;
    }

    .month-strip {
      display: flex;
      gap: 8px;
      overflow-x: auto;
      padding: 2px 0 14px;
      scrollbar-width: none;
    }

    .month-strip::-webkit-scrollbar {
      display: none;
    }

    .month-btn {
      flex: 0 0 auto;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--muted);
      border-radius: 16px;
      padding: 10px 12px;
      min-width: 62px;
      font-weight: 900;
      cursor: pointer;
      box-shadow: 0 4px 14px rgba(15, 23, 42, .04);
    }

    .month-btn.active {
      background: var(--dark);
      color: #fff;
      border-color: var(--dark);
    }

    .toolbar {
      display: grid;
      grid-template-columns: 1fr 96px;
      gap: 8px;
      margin-bottom: 12px;
    }

    input,
    select,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 16px;
      outline: none;
      font-size: 14px;
    }

    input,
    select {
      height: 44px;
      padding: 0 12px;
    }

    textarea {
      min-height: 220px;
      padding: 12px;
      resize: vertical;
      line-height: 1.65;
    }

    .ipo-list {
      display: grid;
      gap: 12px;
    }

    .ipo-card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 16px;
      box-shadow: var(--shadow);
    }

    .ipo-head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 12px;
    }

    .ipo-name {
      font-size: 20px;
      font-weight: 1000;
      letter-spacing: -.04em;
      margin: 0;
    }

    .ipo-meta {
      margin-top: 5px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.45;
    }

    .pill {
      border-radius: 999px;
      padding: 6px 9px;
      font-size: 11px;
      font-weight: 900;
      white-space: nowrap;
      border: 1px solid var(--line);
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

    .card-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 14px;
    }

    .mini {
      background: #f8fafc;
      border-radius: 16px;
      padding: 11px;
    }

    .mini span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      margin-bottom: 4px;
    }

    .mini b {
      font-size: 13px;
    }

    .card-actions {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-top: 14px;
    }

    .btn {
      height: 42px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
      color: var(--ink);
      font-size: 13px;
      font-weight: 900;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }

    .btn.dark {
      background: var(--dark);
      color: #fff;
      border-color: var(--dark);
    }

    .btn.kakao {
      background: var(--yellow);
      color: #191919;
      border-color: #e8d000;
    }

    .btn.blue {
      background: var(--blue);
      color: white;
      border-color: var(--blue);
    }

    .detail-card {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 24px;
      padding: 18px;
      box-shadow: var(--shadow);
    }

    .back {
      display: inline-flex;
      border: 0;
      background: transparent;
      color: var(--blue);
      font-weight: 900;
      margin-bottom: 10px;
      padding: 0;
      cursor: pointer;
    }

    .detail-title {
      margin: 0;
      font-size: 30px;
      font-weight: 1000;
      letter-spacing: -.06em;
    }

    .score-box {
      margin: 14px 0;
      background: #020617;
      color: white;
      border-radius: 20px;
      padding: 16px;
    }

    .score-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      font-size: 13px;
      margin-bottom: 8px;
    }

    .bar {
      height: 10px;
      background: rgba(255, 255, 255, .18);
      border-radius: 999px;
      overflow: hidden;
    }

    .bar span {
      display: block;
      height: 100%;
      border-radius: 999px;
      background: white;
    }

    .section {
      margin-top: 16px;
    }

    .section h3 {
      margin: 0 0 7px;
      font-size: 16px;
    }

    .section .box {
      background: #f8fafc;
      border-radius: 18px;
      padding: 13px;
      color: var(--muted);
      line-height: 1.7;
      font-size: 14px;
      white-space: pre-wrap;
    }

    .risk {
      margin: 0;
      padding-left: 18px;
    }

    .risk li {
      margin: 5px 0;
    }

    .status-box {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 16px;
      box-shadow: var(--shadow);
      color: var(--muted);
      line-height: 1.7;
      white-space: pre-wrap;
      font-size: 13px;
    }

    .bottom-nav {
      position: fixed;
      left: 50%;
      bottom: 0;
      transform: translateX(-50%);
      width: min(520px, 100%);
      background: rgba(255, 255, 255, .94);
      backdrop-filter: blur(16px);
      border-top: 1px solid var(--line);
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      padding: 8px 8px max(8px, env(safe-area-inset-bottom));
      z-index: 30;
    }

    .nav-btn {
      border: 0;
      background: transparent;
      border-radius: 14px;
      padding: 8px 4px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 900;
      cursor: pointer;
    }

    .nav-btn.active {
      background: #eff6ff;
      color: var(--blue);
    }

    .empty {
      background: #fff;
      border: 1px dashed var(--line);
      color: var(--muted);
      border-radius: 22px;
      padding: 22px;
      text-align: center;
      line-height: 1.7;
      font-weight: 800;
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="top">
      <div class="top-row">
        <div>
          <h1 class="title">공모주 인사이트</h1>
          <div class="subtitle">모바일 전용 자동수집 웹</div>
        </div>
        <button class="refresh" onclick="loadData(true)">새로고침</button>
      </div>
    </header>

    <section id="screenMonthly" class="screen active">
      <div class="summary-card">
        <div class="summary-label">이번 달 청약 일정</div>
        <div id="summaryMain" class="summary-main">0개</div>
        <div id="summarySub" class="summary-sub">데이터를 불러오는 중입니다.</div>
      </div>

      <div id="monthStrip" class="month-strip"></div>

      <div class="toolbar">
        <input id="searchInput" placeholder="종목 검색" />
        <select id="sortSelect">
          <option value="date">청약일순</option>
          <option value="score">흥행순</option>
          <option value="name">이름순</option>
        </select>
      </div>

      <div id="monthlyList" class="ipo-list"></div>
    </section>

    <section id="screenAll" class="screen">
      <div class="summary-card">
        <div class="summary-label">전체 수집 종목</div>
        <div id="allSummaryMain" class="summary-main">0개</div>
        <div id="allSummarySub" class="summary-sub">DART/KRX 공개 페이지 기준 자동 수집</div>
      </div>
      <div id="allList" class="ipo-list"></div>
    </section>

    <section id="screenStatus" class="screen">
      <div class="summary-card">
        <div class="summary-label">자동 수집 상태</div>
        <div class="summary-main">상태</div>
        <div class="summary-sub">각 소스별 성공/실패를 확인합니다.</div>
      </div>
      <div id="statusBox" class="status-box">수집 상태 확인 중...</div>
    </section>

    <section id="screenShare" class="screen">
      <div class="summary-card">
        <div class="summary-label">이번 달 공유 문구</div>
        <div class="summary-main">공유</div>
        <div class="summary-sub">이번 달 청약 일정을 카카오톡에 붙여넣기 좋게 정리합니다.</div>
      </div>
      <textarea id="monthShareText" readonly></textarea>
      <div style="height:10px;"></div>
      <button class="btn kakao" style="width:100%;" onclick="copyMonthShare()">이번 달 일정 복사</button>
    </section>

    <section id="screenDetail" class="screen">
      <button class="back" onclick="goBack()">← 목록으로</button>
      <div id="detailView" class="detail-card"></div>
    </section>

    <nav class="bottom-nav">
      <button id="navMonthly" class="nav-btn active" onclick="showScreen('monthly')">월별</button>
      <button id="navAll" class="nav-btn" onclick="showScreen('all')">전체</button>
      <button id="navStatus" class="nav-btn" onclick="showScreen('status')">수집상태</button>
      <button id="navShare" class="nav-btn" onclick="showScreen('share')">공유</button>
    </nav>
  </div>

  <script>
    let ipos = [];
    let statusData = {};
    const YEAR = new Date().getFullYear();
    let selectedMonth = new Date().getMonth() + 1;
    let selectedId = null;
    let prevScreen = "monthly";

    const $ = id => document.getElementById(id);

    function escapeHtml(value) {
      return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
    }

    function monthOf(dateString) {
      return dateString ? Number(String(dateString).slice(5, 7)) : 0;
    }

    function md(dateString) {
      if (!dateString || dateString.length < 10) return "미정";
      return dateString.slice(5).replace("-", ".");
    }

    function getStatus(score) {
      score = Number(score || 0);
      if (score >= 80) return ["hot", "흥행 강함"];
      if (score >= 65) return ["good", "관심 높음"];
      if (score >= 50) return ["watch", "관망 필요"];
      return ["weak", "주의"];
    }

    function currentMonthItems() {
      return ipos
        .filter(item => monthOf(item.subscriptionStart) === selectedMonth)
        .sort((a, b) => new Date(a.subscriptionStart) - new Date(b.subscriptionStart));
    }

    function filteredMonthlyItems() {
      const query = $("searchInput").value.toLowerCase();
      const sort = $("sortSelect").value;
      return currentMonthItems()
        .filter(item => `${item.name} ${item.sector} ${item.manager}`.toLowerCase().includes(query))
        .sort(sortItems(sort));
    }

    function sortItems(sort) {
      return function(a, b) {
        if (sort === "score") return Number(b.score || 0) - Number(a.score || 0);
        if (sort === "name") return String(a.name).localeCompare(String(b.name), "ko");
        return new Date(a.subscriptionStart) - new Date(b.subscriptionStart);
      };
    }

    async function loadData(force = false) {
      $("statusBox").textContent = "수집 중...";
      $("monthlyList").innerHTML = `<div class="empty">인터넷 공모주 데이터를 불러오는 중입니다.</div>`;

      try {
        const response = await fetch(`/api/ipos?year=${YEAR}&force=${force ? 1 : 0}`);
        const data = await response.json();

        ipos = data.items || [];
        statusData = data || {};

        const current = currentMonthItems()[0];
        if (current) {
          selectedId = current.id;
        } else if (ipos[0]) {
          selectedMonth = monthOf(ipos[0].subscriptionStart);
          selectedId = ipos[0].id;
        }

        renderAll();
      } catch (error) {
        $("statusBox").textContent = "수집 실패: " + error.message;
        $("monthlyList").innerHTML = `<div class="empty">수집 실패<br>${escapeHtml(error.message)}</div>`;
      }
    }

    function renderAll() {
      renderMonths();
      renderSummary();
      renderMonthlyList();
      renderAllList();
      renderStatus();
      renderMonthShare();
    }

    function renderMonths() {
      $("monthStrip").innerHTML = Array.from({ length: 12 }, (_, index) => {
        const month = index + 1;
        const count = ipos.filter(item => monthOf(item.subscriptionStart) === month).length;
        return `
          <button class="month-btn ${month === selectedMonth ? "active" : ""}" onclick="selectMonth(${month})">
            ${month}월<br><small>${count}개</small>
          </button>
        `;
      }).join("");
    }

    function renderSummary() {
      const items = currentMonthItems();
      const avg = items.length
        ? Math.round(items.reduce((sum, item) => sum + Number(item.score || 0), 0) / items.length)
        : 0;
      const good = items.filter(item => Number(item.score || 0) >= 65).length;

      $("summaryMain").textContent = `${items.length}개`;
      $("summarySub").textContent = items.length
        ? `${YEAR}년 ${selectedMonth}월 · 평균 흥행점수 ${avg}점\n관심 높은 종목 ${good}개`
        : `${YEAR}년 ${selectedMonth}월 수집 일정 없음`;

      $("allSummaryMain").textContent = `${ipos.length}개`;
      $("allSummarySub").textContent = statusData.cachedAtText
        ? `마지막 갱신: ${statusData.cachedAtText}`
        : "DART/KRX 공개 페이지 기준 자동 수집";
    }

    function renderMonthlyList() {
      const items = filteredMonthlyItems();
      $("monthlyList").innerHTML = items.length
        ? items.map(renderCard).join("")
        : `<div class="empty">이 월에는 수집된 청약 일정이 없습니다.<br>수집상태 탭에서 실패 원인을 확인하세요.</div>`;
    }

    function renderAllList() {
      const all = [...ipos].sort((a, b) => new Date(a.subscriptionStart) - new Date(b.subscriptionStart));
      $("allList").innerHTML = all.length
        ? all.map(renderCard).join("")
        : `<div class="empty">수집된 공모주가 없습니다.</div>`;
    }

    function renderCard(item) {
      const [key, label] = getStatus(item.score);
      return `
        <article class="ipo-card">
          <div class="ipo-head">
            <div>
              <h2 class="ipo-name">${escapeHtml(item.name)}</h2>
              <div class="ipo-meta">${escapeHtml(item.market)} · ${escapeHtml(item.sector)}</div>
            </div>
            <span class="pill ${key}">${label}</span>
          </div>

          <div class="card-grid">
            <div class="mini"><span>청약일</span><b>${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}</b></div>
            <div class="mini"><span>흥행점수</span><b>${Number(item.score || 0)}점</b></div>
            <div class="mini"><span>주관사</span><b>${escapeHtml(item.manager || "확인 필요")}</b></div>
            <div class="mini"><span>공모가</span><b>${escapeHtml(item.priceBand || "확인 필요")}</b></div>
          </div>

          <div class="card-actions">
            <button class="btn dark" onclick="openDetail('${escapeHtml(item.id)}')">자세히</button>
            <button class="btn kakao" onclick="copyItemShare('${escapeHtml(item.id)}')">카톡복사</button>
          </div>
        </article>
      `;
    }

    function renderStatus() {
      const lines = [];
      lines.push(`전체 수집 종목: ${statusData.count || 0}개`);
      lines.push(`마지막 갱신: ${statusData.cachedAtText || "-"}`);
      lines.push("");
      lines.push("소스별 수집:");
      lines.push(JSON.stringify(statusData.sourceCounts || {}, null, 2));

      if (statusData.errors && statusData.errors.length) {
        lines.push("");
        lines.push("실패/경고:");
        statusData.errors.forEach(error => lines.push("- " + error));
      }

      if (statusData.note) {
        lines.push("");
        lines.push(statusData.note);
      }

      $("statusBox").textContent = lines.join("\n");
    }

    function renderDetail() {
      const item = ipos.find(row => row.id === selectedId);

      if (!item) {
        $("detailView").innerHTML = `<div class="empty">종목을 찾을 수 없습니다.</div>`;
        return;
      }

      const [key, label] = getStatus(item.score);

      $("detailView").innerHTML = `
        <div class="ipo-head">
          <div>
            <h2 class="detail-title">${escapeHtml(item.name)}</h2>
            <div class="ipo-meta">${escapeHtml(item.market)} · ${escapeHtml(item.sector)}</div>
          </div>
          <span class="pill ${key}">${label}</span>
        </div>

        <div class="score-box">
          <div class="score-row"><span>흥행 점수</span><b>${Number(item.score || 0)}/100</b></div>
          <div class="bar"><span style="width:${Math.max(0, Math.min(100, Number(item.score || 0)))}%"></span></div>
        </div>

        <div class="card-grid">
          <div class="mini"><span>청약일</span><b>${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}</b></div>
          <div class="mini"><span>주관사</span><b>${escapeHtml(item.manager || "확인 필요")}</b></div>
          <div class="mini"><span>희망 공모가</span><b>${escapeHtml(item.priceBand || "확인 필요")}</b></div>
          <div class="mini"><span>청약경쟁률</span><b>${escapeHtml(item.competitionRate || "예정")}</b></div>
        </div>

        <div class="section"><h3>회사개요</h3><div class="box">${escapeHtml(item.overview || "확인 필요")}</div></div>
        <div class="section"><h3>전망</h3><div class="box">${escapeHtml(item.outlook || "확인 필요")}</div></div>
        <div class="section"><h3>리스크</h3><div class="box"><ul class="risk">${(item.risks || []).map(risk => `<li>${escapeHtml(risk)}</li>`).join("")}</ul></div></div>
        <div class="section"><h3>출처</h3><div class="box">${escapeHtml(item.source || "확인 필요")}</div></div>

        <div class="card-actions">
          <button class="btn kakao" onclick="copyItemShare('${escapeHtml(item.id)}')">카카오톡 문구 복사</button>
          <a class="btn blue" href="${escapeHtml(item.detailUrl || item.sourceUrl || "https://dart.fss.or.kr/dsac008/main.do")}" target="_blank">원본 확인</a>
        </div>
      `;
    }

    function itemShareText(item) {
      if (!item) return "공모주 데이터가 없습니다.";

      return `[공모주 인사이트]

종목: ${item.name}
청약일: ${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}
시장/업종: ${item.market} / ${item.sector}
주관사: ${item.manager}
희망 공모가: ${item.priceBand}
청약경쟁률: ${item.competitionRate}
흥행판단: ${getStatus(item.score)[1]} (${item.score}/100)

회사개요:
${item.overview}

전망:
${item.outlook}

체크 리스크:
${(item.risks || []).map(risk => "- " + risk).join("\n")}

※ 투자 권유가 아닌 정보 정리용 요약입니다.`;
    }

    function renderMonthShare() {
      const items = currentMonthItems();
      const lines = [];

      lines.push(`[공모주 인사이트] ${YEAR}년 ${selectedMonth}월 청약 일정`);
      lines.push("");
      lines.push(`총 ${items.length}개 수집`);
      lines.push("");

      if (items.length) {
        items.forEach(item => {
          lines.push(`- ${item.name}: ${md(item.subscriptionStart)}~${md(item.subscriptionEnd)} / ${getStatus(item.score)[1]} / ${item.manager}`);
        });
      } else {
        lines.push("- 수집된 일정 없음");
      }

      lines.push("");
      lines.push("※ 투자 권유가 아닌 정보 정리용 요약입니다.");

      $("monthShareText").value = lines.join("\n");
    }

    async function copyText(text) {
      try {
        await navigator.clipboard.writeText(text);
        alert("카카오톡에 붙여넣을 문구를 복사했습니다.");
      } catch (error) {
        alert("복사에 실패했습니다. 텍스트를 직접 선택해 복사해 주세요.");
      }
    }

    function copyItemShare(id) {
      const item = ipos.find(row => row.id === id);
      copyText(itemShareText(item));
    }

    function copyMonthShare() {
      copyText($("monthShareText").value);
    }

    function selectMonth(month) {
      selectedMonth = month;
      const first = currentMonthItems()[0];
      if (first) selectedId = first.id;
      renderAll();
    }

    function openDetail(id) {
      selectedId = id;
      prevScreen = document.querySelector(".screen.active")?.id === "screenAll" ? "all" : "monthly";
      renderDetail();
      showScreen("detail");
    }

    function goBack() {
      showScreen(prevScreen || "monthly");
    }

    function showScreen(name) {
      ["Monthly", "All", "Status", "Share", "Detail"].forEach(key => {
        const screen = $("screen" + key);
        if (screen) screen.classList.remove("active");
      });

      ["Monthly", "All", "Status", "Share"].forEach(key => {
        const nav = $("nav" + key);
        if (nav) nav.classList.remove("active");
      });

      if (name === "monthly") {
        $("screenMonthly").classList.add("active");
        $("navMonthly").classList.add("active");
      } else if (name === "all") {
        $("screenAll").classList.add("active");
        $("navAll").classList.add("active");
      } else if (name === "status") {
        $("screenStatus").classList.add("active");
        $("navStatus").classList.add("active");
      } else if (name === "share") {
        $("screenShare").classList.add("active");
        $("navShare").classList.add("active");
      } else if (name === "detail") {
        $("screenDetail").classList.add("active");
      }

      window.scrollTo({ top: 0, behavior: "smooth" });
    }

    $("searchInput").addEventListener("input", renderMonthlyList);
    $("sortSelect").addEventListener("input", renderMonthlyList);

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
            "mode": "mobile-final-single-file",
            "sources": SOURCES,
            "message": "모바일 전용 UI와 공개 웹페이지 자동수집을 사용합니다.",
        }
    )


@app.errorhandler(404)
def not_found(_error):
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")


if __name__ == "__main__":
    print("공모주 인사이트 모바일 최종본")
    print("브라우저에서 http://127.0.0.1:5077 로 접속하세요.")
    app.run(host="127.0.0.1", port=5077, debug=True)
