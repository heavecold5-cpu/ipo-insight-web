# -*- coding: utf-8 -*-
from __future__ import annotations
import json, re, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "ipo_data.json"
BACKUP_FILE = APP_DIR / "ipo_data.backup.json"
HEADERS = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36 Chrome/125 Safari/537.36", "Accept-Language": "ko-KR,ko;q=0.9", "Connection": "close"}

SOURCES = [
    {"name":"DART 청약달력","url":"https://dart.fss.or.kr/dsac008/main.do","base":"https://dart.fss.or.kr","type":"dart_calendar","timeout":6},
    {"name":"KRX KIND 공모일정","url":"https://kind.krx.co.kr/listinvstg/pubofrschdl.do?method=searchPubofrScholMain","base":"https://kind.krx.co.kr","type":"generic_table","timeout":6},
    {"name":"38커뮤니케이션","url":"https://www.38.co.kr/html/fund/?o=k","base":"https://www.38.co.kr","type":"generic_table","timeout":5},
]

def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()

def normalize_date_range(text: str, year: int) -> Optional[Tuple[str,str]]:
    raw = normalize_space(text).replace("/", ".").replace("-", ".").replace("년",".").replace("월",".").replace("일","")
    raw = re.sub(r"\s+", "", raw)
    m = re.search(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})\s*[~∼]\s*(?:(20\d{2})\.)?(?:(\d{1,2})\.)?(\d{1,2})", raw)
    if m:
        y1=int(m.group(1) or year); m1=int(m.group(2)); d1=int(m.group(3)); y2=int(m.group(4) or y1); m2=int(m.group(5) or m1); d2=int(m.group(6))
        return f"{y1:04d}-{m1:02d}-{d1:02d}", f"{y2:04d}-{m2:02d}-{d2:02d}"
    m = re.search(r"(?:(20\d{2})\.)?(\d{1,2})\.(\d{1,2})", raw)
    if m:
        y=int(m.group(1) or year); mo=int(m.group(2)); da=int(m.group(3))
        return f"{y:04d}-{mo:02d}-{da:02d}", f"{y:04d}-{mo:02d}-{da:02d}"
    return None

def infer_market(name: str, row_text: str = "") -> str:
    text=f"{name} {row_text}"
    if "스팩" in text or "기업인수목적" in text: return "SPAC"
    if "유가증권" in text or "코스피" in text: return "KOSPI"
    if "코스닥" in text: return "KOSDAQ"
    return "확인 필요"

def infer_overview(name: str) -> str:
    if "스팩" in name or "기업인수목적" in name: return "스팩은 비상장기업과의 합병을 목적으로 설립되는 기업인수목적회사입니다."
    if any(k in name for k in ["바이오","이뮨","메디","헬스"]): return "바이오·헬스케어 분야 기업으로 추정되며, 정확한 파이프라인과 재무정보는 공시 확인이 필요합니다."
    if any(k in name for k in ["락스","AI","로보","테크"]): return "AI·소프트웨어·기술 기반 기업으로 추정되며, 주요 제품과 매출 구조는 공시 확인이 필요합니다."
    if any(k in name for k in ["스튜디오","아트","콘텐츠"]): return "콘텐츠·플랫폼·브랜드 관련 기업으로 추정되며, 정확한 사업 내용은 공시 확인이 필요합니다."
    return "공개 공모주 일정에서 수집된 기업입니다. 정확한 업체개요와 매출액은 원본 공시 확인이 필요합니다."

def fetch_html(source: Dict[str,Any]) -> str:
    r = requests.get(source["url"], headers=HEADERS, timeout=int(source.get("timeout", 6)))
    r.raise_for_status()
    if r.apparent_encoding: r.encoding = r.apparent_encoding
    return r.text

def make_item(name, start, end, source, market="확인 필요", manager="확인 필요", price_band="확인 필요", source_url=""):
    return {
        "id": f"{source['name']}-{name}-{start}".replace(" ","-"),
        "name": name, "market": market, "subscription_start": start, "subscription_end": end,
        "manager": manager or "확인 필요", "price_band": price_band or "확인 필요",
        "final_price": "확인 필요", "institution_competition": "확인 필요", "listing_date": "확인 필요",
        "company_overview": infer_overview(name), "revenue": "확인 필요", "operating_profit": "확인 필요", "net_income": "확인 필요",
        "source": source["name"], "source_url": source_url or source["url"], "status": "수집"
    }

def parse_dart_calendar(html, source, year):
    soup=BeautifulSoup(html,"html.parser")
    lines=[normalize_space(x) for x in soup.get_text("\n").splitlines() if normalize_space(x)]
    events={}; current_day=None; month=datetime.now().month
    market_map={"코":"KOSDAQ","유":"KOSPI","기":"기타"}
    for line in lines:
        if re.fullmatch(r"\d{1,2}", line):
            day=int(line)
            if 1<=day<=31: current_day=day
            continue
        inline=normalize_date_range(line,year)
        if inline:
            current_day=int(inline[0][-2:]); month=int(inline[0][5:7])
        if current_day is None: continue
        for m in re.finditer(r"(?:^|\s)([코유기])\s+([^\[\]\n\r]+?)\s*\[(시작|종료)\]", line):
            code,name,kind=m.group(1),normalize_space(m.group(2)),m.group(3)
            date=f"{year:04d}-{month:02d}-{current_day:02d}"
            if name not in events:
                market=market_map.get(code,"확인 필요")
                if "기업인수목적" in name or "스팩" in name: market="SPAC"
                events[name]={"market":market,"start":"","end":""}
            if kind=="시작": events[name]["start"]=date
            else: events[name]["end"]=date
    return [make_item(n, info.get("start") or info.get("end"), info.get("end") or info.get("start"), source, market=info.get("market","확인 필요"), manager="DART 확인 필요", price_band="원본 확인 필요") for n,info in events.items() if (info.get("start") or info.get("end"))]

def parse_generic_table(html, source, year):
    soup=BeautifulSoup(html,"html.parser"); items=[]
    for tr in soup.find_all("tr"):
        cells=[normalize_space(td.get_text(" ",strip=True)) for td in tr.find_all(["td","th"])]
        if len(cells)<3: continue
        row=" ".join(cells)
        if not re.search(r"\d{1,4}[./]\d{1,2}[./]\d{1,2}|[01]?\d\.[0-3]?\d", row): continue
        if not any(k in row for k in ["~","∼","청약","공모","증권","투자","상장"]): continue
        name=re.sub(r"\s+"," ",cells[0].replace("분석보기","").replace("기업개요","").strip())
        if not name or name in ("종목명","기업명","회사명") or len(name)>40: continue
        dr=None
        for c in cells:
            dr=normalize_date_range(c,year)
            if dr: break
        if not dr: continue
        manager="확인 필요"; price_band="확인 필요"
        for c in cells:
            if "증권" in c or "투자" in c: manager=c
            if re.search(r"\d[\d,]*\s*[~∼-]\s*\d[\d,]*", c): price_band=c
        link=tr.find("a",href=True); url=urljoin(source["base"], link["href"]) if link else source["url"]
        items.append(make_item(name,dr[0],dr[1],source,market=infer_market(name,row),manager=manager,price_band=price_band,source_url=url))
    return items

def dedupe(items):
    result={}
    for item in items:
        key=f"{item.get('name')}-{item.get('subscription_start')}-{item.get('subscription_end')}"
        if key not in result or len(json.dumps(item,ensure_ascii=False))>len(json.dumps(result[key],ensure_ascii=False)):
            result[key]=item
    return sorted(result.values(), key=lambda x:(x.get("subscription_start",""), x.get("name","")))

def load_existing():
    if DATA_FILE.exists():
        try: return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception: pass
    return {"ok":True,"mode":"empty","updated_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"items":[],"errors":[],"message":"기존 데이터 없음"}

def save(payload):
    if DATA_FILE.exists():
        try: BACKUP_FILE.write_text(DATA_FILE.read_text(encoding="utf-8"), encoding="utf-8")
        except Exception: pass
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

def run():
    year=datetime.now().year; errors=[]; counts={}; all_items=[]
    for source in SOURCES:
        try:
            html=fetch_html(source)
            parsed=parse_dart_calendar(html,source,year) if source["type"]=="dart_calendar" else parse_generic_table(html,source,year)
            counts[source["name"]]=len(parsed); all_items.extend(parsed)
        except Exception as exc:
            errors.append(f"{source['name']}: {exc}")
    items=dedupe(all_items)
    if not items:
        old=load_existing(); old["errors"]=errors; old["source_counts"]=counts; old["message"]="자동수집 실패. 기존 데이터를 유지했습니다."; save(old); print(old["message"]); return 1
    payload={"ok":True,"mode":"crawled","updated_at":datetime.now().strftime("%Y-%m-%d %H:%M:%S"),"items":items,"errors":errors,"source_counts":counts,"message":"공개 페이지 자동수집 결과입니다. 청약 전 원본 확인이 필요합니다."}
    save(payload); print(f"수집 완료: {len(items)}개"); return 0

if __name__=="__main__":
    sys.exit(run())
