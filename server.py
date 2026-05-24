# -*- coding: utf-8 -*-
"""
공모주 인사이트 - 모바일 안정형 최종본

핵심 방향:
- 앱은 절대 외부 수집 실패 때문에 죽지 않음
- 기본 저장 데이터/캐시를 먼저 보여줌
- 새로고침 버튼을 눌렀을 때만 DART/KRX 공개 페이지 수집을 시도함
- 수집 실패 시 기존 데이터 유지
- public/Profile 폴더 없이 server.py 단일 파일로 화면 제공

Render Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

Build Command:
pip install -r requirements.txt
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
    "User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/125 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Connection": "close",
}

SOURCES = [
    {
        "name": "DART 청약달력",
        "url": "https://dart.fss.or.kr/dsac008/main.do",
        "base": "https://dart.fss.or.kr",
        "type": "dart_calendar",
        "timeout": 3,
    },
    {
        "name": "KRX KIND 공모일정",
        "url": "https://kind.krx.co.kr/listinvstg/pubofrschdl.do?method=searchPubofrScholMain",
        "base": "https://kind.krx.co.kr",
        "type": "generic_table",
        "timeout": 3,
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


def default_items(year: int) -> List[Dict[str, Any]]:
    """
    외부 수집 실패 시에도 앱 화면이 비지 않도록 제공하는 기본 저장 데이터.
    실제 청약 전에는 DART/KRX/주관사 공지로 반드시 재확인해야 한다.
    """
    raw = [
        ("폴레드", "KOSDAQ", "확인 필요", f"{year}-05-26", f"{year}-05-27", 58),
        ("마키나락스", "KOSDAQ", "AI / 로봇 / 소프트웨어", f"{year}-05-26", f"{year}-05-27", 61),
        ("이뮨온시아", "KOSDAQ", "바이오 / 헬스케어", f"{year}-05-27", f"{year}-05-28", 59),
        ("투게더아트", "KOSDAQ", "콘텐츠 / 엔터테인먼트", f"{year}-05-28", f"{year}-05-29", 55),
        ("피스피스스튜디오", "KOSDAQ", "식품 / 소비재", f"{year}-06-02", f"{year}-06-03", 55),
        ("대신밸런스제20호기업인수목적", "SPAC", "SPAC", f"{year}-06-02", f"{year}-06-03", 45),
    ]

    items: List[Dict[str, Any]] = []

    for name, market, sector, start, end, score in raw:
        item = IPOItem(
            id=f"default-{name}-{start}".replace(" ", "-"),
            name=name,
            market=market,
            sector=sector,
            subscriptionStart=start,
            subscriptionEnd=end,
            manager="원본 확인 필요",
            priceBand="DART/KRX/주관사 확인 필요",
            finalPrice="미정",
            competitionRate="예정",
            overview=(
                f"{name}은/는 기본 저장 데이터에 등록된 공모주 일정입니다. "
                "외부 자동수집이 실패해도 앱 화면을 유지하기 위한 데이터이며, "
                "청약 전 DART, KRX KIND, 주관사 공지를 통해 최신 정보를 확인해야 합니다."
            ),
            outlook=(
                "현재 화면의 흥행 판단은 공모가, 경쟁률, 수요예측 결과가 모두 반영된 확정 판단이 아닙니다. "
                "상장일 유통가능물량, 의무보유확약, 확정 공모가, 청약경쟁률을 확인한 뒤 판단하는 것이 좋습니다."
            ),
            risks=[
                "외부 자동수집 실패 시 기본 저장 데이터가 표시될 수 있음",
                "청약 전 DART/KRX/주관사 공지 재확인 필요",
                "상장일 유통가능물량과 의무보유확약 비율 확인 필요",
            ],
            score=score,
            source="기본 저장 데이터",
            sourceUrl="https://dart.fss.or.kr/dsac008/main.do",
            detailUrl="https://dart.fss.or.kr/dsac008/main.do",
        )
        items.append(item.to_dict())

    return items


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


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


def infer_sector(name: str) -> str:
    if "스팩" in name or "기업인수목적" in name:
        return "SPAC"
    if any(k in name for k in ["바이오", "헬스", "제약", "메디", "셀", "이뮨"]):
        return "바이오 / 헬스케어"
    if any(k in name for k in ["로보", "AI", "에이아이", "테크", "소프트", "락스"]):
        return "AI / 로봇 / 소프트웨어"
    if any(k in name for k in ["스튜디오", "콘텐츠", "엔터"]):
        return "콘텐츠 / 엔터테인먼트"
    if any(k in name for k in ["푸드", "식품", "피스피스"]):
        return "식품 / 소비재"
    return "확인 필요"


def infer_market(name: str, row_text: str = "") -> str:
    text = f"{name} {row_text}"
    if "스팩" in text or "기업인수목적" in text:
        return "SPAC"
    if "유가증권" in text or "코스피" in text:
        return "KOSPI"
    if "코스닥" in text:
        return "KOSDAQ"
    return "확인 필요"


def score_for(name: str, sector: str) -> int:
    if "SPAC" in sector:
        return 45
    if any(k in sector for k in ["AI", "로봇", "바이오", "헬스케어"]):
        return 61
    return 55


def make_item(
    name: str,
    market: str,
    start: str,
    end: str,
    source: Dict[str, Any],
    manager: str = "확인 필요",
    price_band: str = "확인 필요",
    detail_url: str = "",
) -> Dict[str, Any]:
    sector = infer_sector(name)
    score = score_for(name, sector)

    item = IPOItem(
        id=f"{source['name']}-{name}-{start}".replace(" ", "-"),
        name=name,
        market=market,
        sector=sector,
        subscriptionStart=start,
        subscriptionEnd=end,
        manager=manager,
        priceBand=price_band,
        finalPrice="미정",
        competitionRate="예정",
        overview=(
            f"{name}은/는 {source['name']} 공개 페이지에서 자동 수집된 공모주 일정입니다. "
            "정확한 회사개요와 공모 조건은 DART 증권신고서와 주관사 투자설명서를 함께 확인하는 것이 좋습니다."
        ),
        outlook=(
            "현재는 공개 일정 정보 중심의 1차 판단입니다. 수요예측 결과, 확정 공모가, 청약경쟁률, "
            "의무보유확약, 상장일 유통가능물량을 확인하면 흥행 판단의 정확도가 높아집니다."
        ),
        risks=[
            "공개 웹페이지 구조 변경 시 일부 정보가 누락될 수 있음",
            "청약 전 DART/KRX/주관사 공지 재확인 필요",
            "상장일 유통가능물량과 의무보유확약 비율 확인 필요",
        ],
        score=score,
        source=source["name"],
        sourceUrl=source["url"],
        detailUrl=detail_url or source["url"],
    )
    return item.to_dict()


def fetch_html(source: Dict[str, Any]) -> str:
    response = requests.get(
        source["url"],
        headers=HEADERS,
        timeout=int(source.get("timeout", 3)),
    )
    response.raise_for_status()

    apparent = response.apparent_encoding or ""
    if apparent:
        response.encoding = apparent
    elif not response.encoding or response.encoding.lower() in ("iso-8859-1", "ascii"):
        response.encoding = "euc-kr"

    return response.text


def parse_dart_calendar(html: str, source: Dict[str, Any], year: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    lines = [normalize_space(line) for line in soup.get_text("\n").splitlines() if normalize_space(line)]

    items: List[Dict[str, Any]] = []
    events: Dict[str, Dict[str, str]] = {}
    current_day: Optional[int] = None
    month = datetime.now().month

    market_map = {"코": "KOSDAQ", "유": "KOSPI", "기": "기타"}

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

        for match in re.finditer(r"(?:^|\s)([코유기])\s+([^\[\]\n\r]+?)\s*\[(시작|종료)\]", line):
            market_code = match.group(1).strip()
            name = normalize_space(match.group(2))
            event_type = match.group(3).strip()

            if not name or len(name) < 2:
                continue

            date_value = f"{year:04d}-{month:02d}-{current_day:02d}"

            if name not in events:
                market = market_map.get(market_code, "확인 필요")
                if "스팩" in name or "기업인수목적" in name:
                    market = "SPAC"
                events[name] = {"market": market, "start": "", "end": ""}

            if event_type == "시작":
                events[name]["start"] = date_value
            elif event_type == "종료":
                events[name]["end"] = date_value

    for name, info in events.items():
        start = info.get("start") or info.get("end")
        end = info.get("end") or info.get("start")
        if not start:
            continue
        items.append(make_item(name, info.get("market", "확인 필요"), start, end, source, manager="DART 확인 필요", price_band="DART/주관사 확인 필요"))

    return items


def parse_generic_table(html: str, source: Dict[str, Any], year: int) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []

    for tr in soup.find_all("tr"):
        cells = [normalize_space(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        row_text = " ".join(cells)
        if not re.search(r"\d{1,4}[./]\d{1,2}[./]\d{1,2}|[01]?\d\.[0-3]?\d", row_text):
            continue
        if not any(k in row_text for k in ["~", "∼", "청약", "공모", "증권", "투자", "상장"]):
            continue

        name = cells[0].replace("분석보기", "").replace("기업개요", "").strip()
        name = re.sub(r"\s+", " ", name)

        if not name or name in ("종목명", "기업명", "회사명") or len(name) > 40:
            continue

        date_range = None
        for cell in cells:
            date_range = normalize_date_range(cell, year)
            if date_range:
                break

        if not date_range:
            continue

        detail_url = source["url"]
        link = tr.find("a", href=True)
        if link:
            detail_url = urljoin(source["base"], link["href"])

        manager = "확인 필요"
        price_band = "확인 필요"

        for cell in cells:
            if "증권" in cell or "투자" in cell:
                manager = cell
            if re.search(r"\d[\d,]*\s*[~∼-]\s*\d[\d,]*", cell):
                price_band = cell

        items.append(
            make_item(
                name=name,
                market=infer_market(name, row_text),
                start=date_range[0],
                end=date_range[1],
                source=source,
                manager=manager,
                price_band=price_band,
                detail_url=detail_url,
            )
        )

    return items


def dedupe_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}

    for item in items:
        key = f"{item.get('name')}-{item.get('subscriptionStart')}-{item.get('subscriptionEnd')}"
        if key not in result:
            result[key] = item
        else:
            old = result[key]
            if len(json.dumps(item, ensure_ascii=False)) > len(json.dumps(old, ensure_ascii=False)):
                result[key] = item

    return sorted(result.values(), key=lambda item: (item.get("subscriptionStart", ""), item.get("name", "")))


def read_cache_or_default(year: int) -> Dict[str, Any]:
    if CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if cached.get("items"):
                cached["fromCache"] = True
                return cached
        except Exception:
            pass

    return {
        "ok": True,
        "mode": "default",
        "fromCache": False,
        "cachedAt": time.time(),
        "cachedAtText": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(default_items(year)),
        "items": default_items(year),
        "errors": [],
        "sourceCounts": {"기본 저장 데이터": len(default_items(year))},
        "note": "외부 수집 전 기본 저장 데이터를 표시합니다. 청약 전 원본 확인이 필요합니다.",
    }


def try_refresh(year: int) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []
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

    if not items:
        old = read_cache_or_default(year)
        old["ok"] = True
        old["refreshFailed"] = True
        old["errors"] = errors
        old["sourceCounts"] = source_counts or old.get("sourceCounts", {})
        old["note"] = "외부 자동수집이 실패하여 기존 캐시 또는 기본 저장 데이터를 유지했습니다."
        return old

    payload = {
        "ok": True,
        "mode": "live",
        "fromCache": False,
        "refreshFailed": False,
        "cachedAt": time.time(),
        "cachedAtText": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(items),
        "items": items,
        "errors": errors,
        "sourceCounts": source_counts,
        "note": "공개 웹페이지 자동수집 결과입니다. 청약 전 원본 확인이 필요합니다.",
    }

    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


HTML_PAGE = r"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>공모주 인사이트</title>
  <style>
    :root { --bg: #f5f7fb; --card: #ffffff; --ink: #111827; --muted: #6b7280; --line: #e5e7eb; --dark: #020617; --blue: #2563eb; --green: #047857; --amber: #b45309; --red: #b91c1c; --yellow: #fee500; --shadow: 0 12px 28px rgba(15, 23, 42, .08); }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--ink); font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo", "Noto Sans KR", "Segoe UI", sans-serif; }
    button, input, select, textarea { font-family: inherit; }
    .app { max-width: 520px; margin: 0 auto; min-height: 100vh; padding-bottom: 84px; background: #f8fafc; }
    .top { position: sticky; top: 0; z-index: 20; background: rgba(248, 250, 252, .94); backdrop-filter: blur(14px); border-bottom: 1px solid var(--line); padding: 14px 16px 12px; }
    .top-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .title { margin: 0; font-size: 22px; font-weight: 900; letter-spacing: -.04em; }
    .subtitle { margin-top: 3px; color: var(--muted); font-size: 12px; font-weight: 700; }
    .refresh { border: 0; background: var(--dark); color: #fff; border-radius: 999px; padding: 10px 13px; font-weight: 900; font-size: 13px; cursor: pointer; }
    .screen { display: none; padding: 14px 14px 0; }
    .screen.active { display: block; }
    .summary-card { background: linear-gradient(135deg, #020617, #172554); color: #fff; border-radius: 24px; padding: 20px; box-shadow: var(--shadow); margin-bottom: 14px; }
    .summary-label { font-size: 13px; color: #bfdbfe; font-weight: 800; }
    .summary-main { margin-top: 6px; font-size: 34px; font-weight: 1000; letter-spacing: -.06em; }
    .summary-sub { margin-top: 8px; color: #cbd5e1; font-size: 13px; line-height: 1.6; white-space: pre-wrap; }
    .month-strip { display: flex; gap: 8px; overflow-x: auto; padding: 2px 0 14px; scrollbar-width: none; }
    .month-strip::-webkit-scrollbar { display: none; }
    .month-btn { flex: 0 0 auto; border: 1px solid var(--line); background: #fff; color: var(--muted); border-radius: 16px; padding: 10px 12px; min-width: 62px; font-weight: 900; cursor: pointer; box-shadow: 0 4px 14px rgba(15, 23, 42, .04); }
    .month-btn.active { background: var(--dark); color: #fff; border-color: var(--dark); }
    .toolbar { display: grid; grid-template-columns: 1fr 96px; gap: 8px; margin-bottom: 12px; }
    input, select, textarea { width: 100%; border: 1px solid var(--line); background: #fff; border-radius: 16px; outline: none; font-size: 14px; }
    input, select { height: 44px; padding: 0 12px; }
    textarea { min-height: 220px; padding: 12px; resize: vertical; line-height: 1.65; }
    .ipo-list { display: grid; gap: 12px; }
    .ipo-card, .detail-card, .status-box { background: var(--card); border: 1px solid var(--line); border-radius: 22px; padding: 16px; box-shadow: var(--shadow); }
    .ipo-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
    .ipo-name { font-size: 20px; font-weight: 1000; letter-spacing: -.04em; margin: 0; }
    .ipo-meta { margin-top: 5px; color: var(--muted); font-size: 13px; line-height: 1.45; }
    .pill { border-radius: 999px; padding: 6px 9px; font-size: 11px; font-weight: 900; white-space: nowrap; border: 1px solid var(--line); }
    .pill.hot { color: var(--red); background: #fef2f2; border-color: #fecaca; }
    .pill.good { color: var(--green); background: #ecfdf5; border-color: #a7f3d0; }
    .pill.watch { color: var(--amber); background: #fffbeb; border-color: #fde68a; }
    .pill.weak { color: #475569; background: #f8fafc; border-color: #e2e8f0; }
    .card-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
    .mini { background: #f8fafc; border-radius: 16px; padding: 11px; }
    .mini span { display: block; color: var(--muted); font-size: 11px; font-weight: 800; margin-bottom: 4px; }
    .mini b { font-size: 13px; }
    .card-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 14px; }
    .btn { height: 42px; border: 1px solid var(--line); border-radius: 14px; background: #fff; color: var(--ink); font-size: 13px; font-weight: 900; cursor: pointer; text-decoration: none; display: inline-flex; align-items: center; justify-content: center; }
    .btn.dark { background: var(--dark); color: #fff; border-color: var(--dark); }
    .btn.kakao { background: var(--yellow); color: #191919; border-color: #e8d000; }
    .btn.blue { background: var(--blue); color: white; border-color: var(--blue); }
    .back { display: inline-flex; border: 0; background: transparent; color: var(--blue); font-weight: 900; margin-bottom: 10px; padding: 0; cursor: pointer; }
    .detail-title { margin: 0; font-size: 30px; font-weight: 1000; letter-spacing: -.06em; }
    .score-box { margin: 14px 0; background: #020617; color: white; border-radius: 20px; padding: 16px; }
    .score-row { display: flex; align-items: center; justify-content: space-between; font-size: 13px; margin-bottom: 8px; }
    .bar { height: 10px; background: rgba(255, 255, 255, .18); border-radius: 999px; overflow: hidden; }
    .bar span { display: block; height: 100%; border-radius: 999px; background: white; }
    .section { margin-top: 16px; }
    .section h3 { margin: 0 0 7px; font-size: 16px; }
    .section .box { background: #f8fafc; border-radius: 18px; padding: 13px; color: var(--muted); line-height: 1.7; font-size: 14px; white-space: pre-wrap; }
    .risk { margin: 0; padding-left: 18px; }
    .risk li { margin: 5px 0; }
    .status-box { color: var(--muted); line-height: 1.7; white-space: pre-wrap; font-size: 13px; }
    .bottom-nav { position: fixed; left: 50%; bottom: 0; transform: translateX(-50%); width: min(520px, 100%); background: rgba(255, 255, 255, .94); backdrop-filter: blur(16px); border-top: 1px solid var(--line); display: grid; grid-template-columns: repeat(4, 1fr); padding: 8px 8px max(8px, env(safe-area-inset-bottom)); z-index: 30; }
    .nav-btn { border: 0; background: transparent; border-radius: 14px; padding: 8px 4px; color: var(--muted); font-size: 12px; font-weight: 900; cursor: pointer; }
    .nav-btn.active { background: #eff6ff; color: var(--blue); }
    .empty { background: #fff; border: 1px dashed var(--line); color: var(--muted); border-radius: 22px; padding: 22px; text-align: center; line-height: 1.7; font-weight: 800; }
  </style>
</head>
<body>
  <div class="app">
    <header class="top"><div class="top-row"><div><h1 class="title">공모주 인사이트</h1><div class="subtitle">기본 데이터 먼저 표시 · 수집 실패해도 화면 유지</div></div><button class="refresh" onclick="refreshData()">새로고침</button></div></header>
    <section id="screenMonthly" class="screen active"><div class="summary-card"><div class="summary-label">선택월 청약 일정</div><div id="summaryMain" class="summary-main">0개</div><div id="summarySub" class="summary-sub">데이터를 불러오는 중입니다.</div></div><div id="monthStrip" class="month-strip"></div><div class="toolbar"><input id="searchInput" placeholder="종목 검색" /><select id="sortSelect"><option value="date">청약일순</option><option value="score">흥행순</option><option value="name">이름순</option></select></div><div id="monthlyList" class="ipo-list"></div></section>
    <section id="screenAll" class="screen"><div class="summary-card"><div class="summary-label">전체 종목</div><div id="allSummaryMain" class="summary-main">0개</div><div id="allSummarySub" class="summary-sub">기본 데이터 또는 자동수집 결과</div></div><div id="allList" class="ipo-list"></div></section>
    <section id="screenStatus" class="screen"><div class="summary-card"><div class="summary-label">자동 수집 상태</div><div class="summary-main">상태</div><div class="summary-sub">외부 수집 실패 시 기존 데이터가 유지됩니다.</div></div><div id="statusBox" class="status-box">수집 상태 확인 중...</div></section>
    <section id="screenShare" class="screen"><div class="summary-card"><div class="summary-label">선택월 공유 문구</div><div class="summary-main">공유</div><div class="summary-sub">카카오톡에 붙여넣기 좋은 형태로 정리합니다.</div></div><textarea id="monthShareText" readonly></textarea><div style="height:10px;"></div><button class="btn kakao" style="width:100%;" onclick="copyMonthShare()">선택월 일정 복사</button></section>
    <section id="screenDetail" class="screen"><button class="back" onclick="goBack()">← 목록으로</button><div id="detailView" class="detail-card"></div></section>
    <nav class="bottom-nav"><button id="navMonthly" class="nav-btn active" onclick="showScreen('monthly')">월별</button><button id="navAll" class="nav-btn" onclick="showScreen('all')">전체</button><button id="navStatus" class="nav-btn" onclick="showScreen('status')">수집상태</button><button id="navShare" class="nav-btn" onclick="showScreen('share')">공유</button></nav>
  </div>
  <script>
    let ipos = []; let statusData = {}; const YEAR = new Date().getFullYear(); let selectedMonth = new Date().getMonth() + 1; let selectedId = null; let prevScreen = "monthly"; const $ = id => document.getElementById(id);
    function escapeHtml(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");}
    function monthOf(d){return d ? Number(String(d).slice(5,7)) : 0;} function md(d){return (!d||d.length<10)?"미정":d.slice(5).replace("-",".");}
    function getStatus(score){score=Number(score||0); if(score>=80)return["hot","흥행 강함"]; if(score>=65)return["good","관심 높음"]; if(score>=50)return["watch","관망 필요"]; return["weak","주의"];}
    function currentMonthItems(){return ipos.filter(i=>monthOf(i.subscriptionStart)===selectedMonth).sort((a,b)=>new Date(a.subscriptionStart)-new Date(b.subscriptionStart));}
    function sortItems(sort){return function(a,b){if(sort==="score")return Number(b.score||0)-Number(a.score||0); if(sort==="name")return String(a.name).localeCompare(String(b.name),"ko"); return new Date(a.subscriptionStart)-new Date(b.subscriptionStart);};}
    function filteredMonthlyItems(){const q=$("searchInput").value.toLowerCase(); const sort=$("sortSelect").value; return currentMonthItems().filter(i=>`${i.name} ${i.sector} ${i.manager}`.toLowerCase().includes(q)).sort(sortItems(sort));}
    async function loadData(){try{const r=await fetch(`/api/ipos?year=${YEAR}`); applyData(await r.json());}catch(e){$("monthlyList").innerHTML=`<div class="empty">데이터 로드 실패<br>${escapeHtml(e.message)}</div>`;}}
    async function refreshData(){try{const r=await fetch(`/api/refresh?year=${YEAR}`); const data=await r.json(); applyData(data); alert(data.refreshFailed?"외부 수집 실패: 기존 데이터를 유지했습니다.":"새로고침 완료");}catch(e){$("statusBox").textContent="새로고침 실패: "+e.message+"\n기존 화면 데이터는 유지됩니다."; alert("새로고침 실패. 기존 데이터를 유지합니다.");}}
    function applyData(data){ipos=data.items||[]; statusData=data||{}; if(currentMonthItems().length===0&&ipos[0]) selectedMonth=monthOf(ipos[0].subscriptionStart); const cur=currentMonthItems()[0]; if(cur)selectedId=cur.id; else if(ipos[0])selectedId=ipos[0].id; renderAll();}
    function renderAll(){renderMonths(); renderSummary(); renderMonthlyList(); renderAllList(); renderStatus(); renderMonthShare();}
    function renderMonths(){$("monthStrip").innerHTML=Array.from({length:12},(_,idx)=>{const m=idx+1; const c=ipos.filter(i=>monthOf(i.subscriptionStart)===m).length; return `<button class="month-btn ${m===selectedMonth?"active":""}" onclick="selectMonth(${m})">${m}월<br><small>${c}개</small></button>`;}).join("");}
    function renderSummary(){const items=currentMonthItems(); const avg=items.length?Math.round(items.reduce((s,i)=>s+Number(i.score||0),0)/items.length):0; const good=items.filter(i=>Number(i.score||0)>=65).length; $("summaryMain").textContent=`${items.length}개`; $("summarySub").textContent=items.length?`${YEAR}년 ${selectedMonth}월 · 평균 흥행점수 ${avg}점\n관심 높은 종목 ${good}개`:`${YEAR}년 ${selectedMonth}월 수집 일정 없음`; $("allSummaryMain").textContent=`${ipos.length}개`; $("allSummarySub").textContent=statusData.cachedAtText?`마지막 갱신: ${statusData.cachedAtText}`:"기본 데이터 또는 자동수집 결과";}
    function renderMonthlyList(){const items=filteredMonthlyItems(); $("monthlyList").innerHTML=items.length?items.map(renderCard).join(""):`<div class="empty">이 월에는 표시할 일정이 없습니다.</div>`;}
    function renderAllList(){const all=[...ipos].sort((a,b)=>new Date(a.subscriptionStart)-new Date(b.subscriptionStart)); $("allList").innerHTML=all.length?all.map(renderCard).join(""):`<div class="empty">표시할 공모주가 없습니다.</div>`;}
    function renderCard(item){const [key,label]=getStatus(item.score); return `<article class="ipo-card"><div class="ipo-head"><div><h2 class="ipo-name">${escapeHtml(item.name)}</h2><div class="ipo-meta">${escapeHtml(item.market)} · ${escapeHtml(item.sector)}</div></div><span class="pill ${key}">${label}</span></div><div class="card-grid"><div class="mini"><span>청약일</span><b>${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}</b></div><div class="mini"><span>흥행점수</span><b>${Number(item.score||0)}점</b></div><div class="mini"><span>주관사</span><b>${escapeHtml(item.manager||"확인 필요")}</b></div><div class="mini"><span>공모가</span><b>${escapeHtml(item.priceBand||"확인 필요")}</b></div></div><div class="card-actions"><button class="btn dark" onclick="openDetail('${escapeHtml(item.id)}')">자세히</button><button class="btn kakao" onclick="copyItemShare('${escapeHtml(item.id)}')">카톡복사</button></div></article>`;}
    function renderStatus(){const lines=[]; lines.push(`모드: ${statusData.mode||"-"}`); lines.push(`전체 표시 종목: ${statusData.count||0}개`); lines.push(`마지막 갱신: ${statusData.cachedAtText||"-"}`); lines.push(`캐시 사용: ${statusData.fromCache?"예":"아니오"}`); lines.push(`외부 수집 실패: ${statusData.refreshFailed?"예":"아니오"}`); lines.push(""); lines.push("소스별 수집:"); lines.push(JSON.stringify(statusData.sourceCounts||{},null,2)); if(statusData.errors&&statusData.errors.length){lines.push(""); lines.push("실패/경고:"); statusData.errors.forEach(e=>lines.push("- "+e));} if(statusData.note){lines.push(""); lines.push(statusData.note);} $("statusBox").textContent=lines.join("\n");}
    function renderDetail(){const item=ipos.find(r=>r.id===selectedId); if(!item){$("detailView").innerHTML=`<div class="empty">종목을 찾을 수 없습니다.</div>`; return;} const [key,label]=getStatus(item.score); $("detailView").innerHTML=`<div class="ipo-head"><div><h2 class="detail-title">${escapeHtml(item.name)}</h2><div class="ipo-meta">${escapeHtml(item.market)} · ${escapeHtml(item.sector)}</div></div><span class="pill ${key}">${label}</span></div><div class="score-box"><div class="score-row"><span>흥행 점수</span><b>${Number(item.score||0)}/100</b></div><div class="bar"><span style="width:${Math.max(0,Math.min(100,Number(item.score||0)))}%"></span></div></div><div class="card-grid"><div class="mini"><span>청약일</span><b>${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}</b></div><div class="mini"><span>주관사</span><b>${escapeHtml(item.manager||"확인 필요")}</b></div><div class="mini"><span>희망 공모가</span><b>${escapeHtml(item.priceBand||"확인 필요")}</b></div><div class="mini"><span>청약경쟁률</span><b>${escapeHtml(item.competitionRate||"예정")}</b></div></div><div class="section"><h3>회사개요</h3><div class="box">${escapeHtml(item.overview||"확인 필요")}</div></div><div class="section"><h3>전망</h3><div class="box">${escapeHtml(item.outlook||"확인 필요")}</div></div><div class="section"><h3>리스크</h3><div class="box"><ul class="risk">${(item.risks||[]).map(r=>`<li>${escapeHtml(r)}</li>`).join("")}</ul></div></div><div class="section"><h3>출처</h3><div class="box">${escapeHtml(item.source||"확인 필요")}</div></div><div class="card-actions"><button class="btn kakao" onclick="copyItemShare('${escapeHtml(item.id)}')">카카오톡 문구 복사</button><a class="btn blue" href="${escapeHtml(item.detailUrl||item.sourceUrl||"https://dart.fss.or.kr/dsac008/main.do")}" target="_blank">원본 확인</a></div>`;}
    function itemShareText(item){if(!item)return "공모주 데이터가 없습니다."; return `[공모주 인사이트]\n\n종목: ${item.name}\n청약일: ${md(item.subscriptionStart)} ~ ${md(item.subscriptionEnd)}\n시장/업종: ${item.market} / ${item.sector}\n주관사: ${item.manager}\n희망 공모가: ${item.priceBand}\n청약경쟁률: ${item.competitionRate}\n흥행판단: ${getStatus(item.score)[1]} (${item.score}/100)\n\n회사개요:\n${item.overview}\n\n전망:\n${item.outlook}\n\n체크 리스크:\n${(item.risks||[]).map(r=>"- "+r).join("\n")}\n\n※ 투자 권유가 아닌 정보 정리용 요약입니다.`;}
    function renderMonthShare(){const items=currentMonthItems(); const lines=[]; lines.push(`[공모주 인사이트] ${YEAR}년 ${selectedMonth}월 청약 일정`); lines.push(""); lines.push(`총 ${items.length}개 표시`); lines.push(""); if(items.length){items.forEach(i=>lines.push(`- ${i.name}: ${md(i.subscriptionStart)}~${md(i.subscriptionEnd)} / ${getStatus(i.score)[1]} / ${i.manager}`));}else{lines.push("- 표시할 일정 없음");} lines.push(""); lines.push("※ 투자 권유가 아닌 정보 정리용 요약입니다."); $("monthShareText").value=lines.join("\n");}
    async function copyText(t){try{await navigator.clipboard.writeText(t); alert("카카오톡에 붙여넣을 문구를 복사했습니다.");}catch(e){alert("복사에 실패했습니다. 텍스트를 직접 선택해 복사해 주세요.");}}
    function copyItemShare(id){const item=ipos.find(r=>r.id===id); copyText(itemShareText(item));} function copyMonthShare(){copyText($("monthShareText").value);} function selectMonth(m){selectedMonth=m; const first=currentMonthItems()[0]; if(first) selectedId=first.id; renderAll();}
    function openDetail(id){selectedId=id; prevScreen=document.querySelector(".screen.active")?.id==="screenAll"?"all":"monthly"; renderDetail(); showScreen("detail");} function goBack(){showScreen(prevScreen||"monthly");}
    function showScreen(name){["Monthly","All","Status","Share","Detail"].forEach(k=>{const s=$("screen"+k); if(s)s.classList.remove("active");}); ["Monthly","All","Status","Share"].forEach(k=>{const n=$("nav"+k); if(n)n.classList.remove("active");}); if(name==="monthly"){$("screenMonthly").classList.add("active");$("navMonthly").classList.add("active");}else if(name==="all"){$("screenAll").classList.add("active");$("navAll").classList.add("active");}else if(name==="status"){$("screenStatus").classList.add("active");$("navStatus").classList.add("active");}else if(name==="share"){$("screenShare").classList.add("active");$("navShare").classList.add("active");}else if(name==="detail"){$("screenDetail").classList.add("active");} window.scrollTo({top:0,behavior:"smooth"});}
    $("searchInput").addEventListener("input",renderMonthlyList); $("sortSelect").addEventListener("input",renderMonthlyList); loadData();
  </script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")


@app.route("/api/ipos")
def api_ipos():
    year = request.args.get("year", type=int) or datetime.now().year
    return jsonify(read_cache_or_default(year))


@app.route("/api/refresh")
def api_refresh():
    year = request.args.get("year", type=int) or datetime.now().year
    return jsonify(try_refresh(year))


@app.route("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "time": datetime.now().isoformat(),
            "mode": "simple-mobile-stable",
            "message": "기본 데이터/캐시를 먼저 보여주고, 외부 수집 실패 시에도 앱은 유지됩니다.",
        }
    )


@app.errorhandler(404)
def not_found(_error):
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")


if __name__ == "__main__":
    print("공모주 인사이트 - 모바일 안정형 최종본")
    print("브라우저에서 http://127.0.0.1:5077 로 접속하세요.")
    app.run(host="127.0.0.1", port=5077, debug=True)
