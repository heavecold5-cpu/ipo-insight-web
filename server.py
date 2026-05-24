# -*- coding: utf-8 -*-
"""
공모주 인사이트 자동수집 웹 v4

실행:
  pip install -r requirements.txt
  python server.py
  브라우저에서 http://127.0.0.1:5077 접속

주의:
  - API 인증키 없이 공개 페이지를 읽는 방식입니다.
  - 외부 사이트 HTML 구조가 바뀌면 파서 수정이 필요할 수 있습니다.
  - 투자 권유가 아니라 정보 정리용 도구입니다.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, send_from_directory, request

APP_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = APP_DIR /  "Profile"
DATA_DIR = APP_DIR / "data"
CACHE_FILE = DATA_DIR / "ipo_cache.json"

DATA_DIR.mkdir(exist_ok=True)

app = Flask(__name__, static_folder=str(PUBLIC_DIR), static_url_path="")

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
    risks: List[str] = None
    score: int = 50
    source: str = ""
    sourceUrl: str = ""
    detailUrl: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if d["risks"] is None:
            d["risks"] = []
        return d


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def parse_korean_number(text: str) -> Optional[float]:
    if not text:
        return None
    cleaned = text.replace(",", "").replace(":", "").strip()
    m = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def normalize_date_range(text: str, default_year: int) -> Optional[tuple[str, str]]:
    """
    2026.07.01~07.02, 05.11 ~ 05.12 같은 문자열을 YYYY-MM-DD 두 개로 변환
    """
    if not text:
        return None

    raw = normalize_space(text)
    raw = raw.replace("/", ".").replace("-", ".")
    raw = re.sub(r"\s+", "", raw)

    # 2026.07.01~07.02
    m = re.search(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})\s*[~∼\-]\s*(?:(20\d{2})\.)?(\d{1,2})?\.(\d{1,2})", raw)
    if m:
        y1 = int(m.group(1) or default_year)
        m1 = int(m.group(2))
        d1 = int(m.group(3))
        y2 = int(m.group(4) or y1)
        m2 = int(m.group(5) or m1)
        d2 = int(m.group(6))
        return f"{y1:04d}-{m1:02d}-{d1:02d}", f"{y2:04d}-{m2:02d}-{d2:02d}"

    # 05.26 예정 같은 단일 날짜
    m = re.search(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})", raw)
    if m:
        y = int(m.group(1) or default_year)
        mo = int(m.group(2))
        da = int(m.group(3))
        return f"{y:04d}-{mo:02d}-{da:02d}", f"{y:04d}-{mo:02d}-{da:02d}"

    return None


def fetch_html(url: str) -> str:
    res = requests.get(url, headers=HEADERS, timeout=12)
    # 38 계열은 EUC-KR/CP949인 경우가 많음
    if not res.encoding or res.encoding.lower() in ("iso-8859-1", "ascii"):
        res.encoding = res.apparent_encoding or "euc-kr"
    return res.text


def infer_sector(name: str) -> str:
    lower = name.lower()
    if "스팩" in name or "기업인수목적" in name:
        return "SPAC"
    if any(x in name for x in ["바이오", "헬스", "제약", "메디", "셀", "로직스"]):
        return "바이오 / 헬스케어"
    if any(x in name for x in ["로보", "비젼", "비전", "AI", "에이아이", "테크", "소프트"]):
        return "AI / 로봇 / 소프트웨어"
    if any(x in name for x in ["에너지", "배터리", "전지", "소재"]):
        return "에너지 / 소재"
    if any(x in name for x in ["스튜디오", "콘텐츠", "엔터"]):
        return "콘텐츠 / 엔터테인먼트"
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

    comp = parse_korean_number(competition_rate)
    if comp is not None:
        if comp >= 2000:
            score += 25
        elif comp >= 1000:
            score += 18
        elif comp >= 500:
            score += 12
        elif comp >= 100:
            score += 6

    # 확정공모가가 밴드 상단 이상인지 간단 판정
    fp = parse_korean_number(final_price)
    nums = [float(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", price_band or "")]
    if fp and nums:
        high = max(nums)
        low = min(nums)
        if fp >= high:
            score += 12
        elif fp <= low:
            score -= 8

    if "AI" in sector or "로봇" in sector or "바이오" in sector:
        score += 3
    if "SPAC" in sector:
        score -= 5

    return max(15, min(95, score))


def make_analysis(item: IPOItem) -> IPOItem:
    sector = item.sector if item.sector != "확인 필요" else infer_sector(item.name)
    item.sector = sector
    item.score = calc_score(item.finalPrice, item.priceBand, item.competitionRate, sector)

    if not item.overview:
        item.overview = (
            f"{item.name}은/는 현재 공개 공모주 일정에 등록된 종목입니다. "
            f"업종은 '{sector}'로 분류했으며, 정확한 사업 내용은 증권신고서와 주관사 투자설명서를 통해 추가 확인하는 것이 좋습니다."
        )

    if not item.outlook:
        if "SPAC" in sector:
            item.outlook = (
                "스팩 종목은 합병 대상 기업이 확정되기 전까지 일반 사업회사와 평가 방식이 다릅니다. "
                "공모가 안정성은 상대적으로 단순하지만, 합병 성공 가능성과 향후 합병 대상의 질이 핵심입니다."
            )
        elif item.score >= 70:
            item.outlook = (
                "청약경쟁률, 공모가 흐름, 시장 관심도를 기준으로 볼 때 단기 흥행 관심도는 높은 편입니다. "
                "다만 상장일 유통가능물량과 의무보유확약 비율을 반드시 확인해야 합니다."
            )
        elif item.score >= 55:
            item.outlook = (
                "일정상 관심을 둘 만한 종목이지만, 수요예측 결과와 공모가 확정 위치를 본 뒤 판단하는 것이 좋습니다. "
                "업종 성장성보다 실제 실적과 상장일 매물 부담 확인이 중요합니다."
            )
        else:
            item.outlook = (
                "현재 입력된 공개 정보만으로는 흥행 강도를 높게 판단하기 어렵습니다. "
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
        cells = [normalize_space(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cells) < 3:
            continue

        row_text = " ".join(cells)
        if not re.search(r"\d{1,4}[./]\d{1,2}[./]\d{1,2}|[01]?\d\.[0-3]?\d", row_text):
            continue
        if not any(k in row_text for k in ["~", "∼", "청약", "공모", "증권", "투자"]):
            continue

        # 가장 종목명처럼 보이는 첫 칸
        name = cells[0].replace("분석보기", "").strip()
        if not name or name in ("종목명", "기업명") or len(name) > 30:
            continue

        # 날짜 칸 찾기
        date_range = None
        for c in cells:
            date_range = normalize_date_range(c, default_year)
            if date_range:
                break
        if not date_range:
            continue

        # 링크 찾기
        detail_url = ""
        a = tr.find("a", href=True)
        if a:
            detail_url = urljoin(source["base"], a["href"])

        # 가격/경쟁률/주관사 추정
        final_price = "미정"
        price_band = "확인 필요"
        competition = "예정"
        manager = "확인 필요"

        for c in cells:
            if re.search(r"\d[\d,]*\s*[~∼-]\s*\d[\d,]*", c):
                price_band = c
            if ":1" in c or "대1" in c:
                competition = c
            if "증권" in c or "투자" in c:
                manager = c

        # 확정공모가는 가격 범위가 아닌 단일 숫자 칸 중 하나로 추정
        for c in cells:
            if c in (name, price_band, competition, manager):
                continue
            if re.fullmatch(r"[\d,]+", c):
                final_price = c + "원"
                break
            if re.fullmatch(r"-", c):
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
        if key not in result:
            result[key] = item
        else:
            # 더 정보가 많은 쪽 우선
            old = result[key]
            if len(json.dumps(item.to_dict(), ensure_ascii=False)) > len(json.dumps(old.to_dict(), ensure_ascii=False)):
                result[key] = item
    return sorted(result.values(), key=lambda x: (x.subscriptionStart, x.name))


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
        except Exception as e:
            errors.append(f"{source['name']}: {e}")

    items = dedupe_items(all_items)

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


@app.route("/")
def index():
    return send_from_directory(PUBLIC_DIR, "index.html")


@app.route("/api/ipos")
def api_ipos():
    force = request.args.get("force") == "1"
    year = request.args.get("year", type=int) or datetime.now().year
    return jsonify(collect_ipos(force=force, year=year))


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "time": datetime.now().isoformat()})


if __name__ == "__main__":
    print("공모주 인사이트 자동수집 웹 v4")
    print("브라우저에서 http://127.0.0.1:5077 로 접속하세요.")
    app.run(host="127.0.0.1", port=5077, debug=True)
