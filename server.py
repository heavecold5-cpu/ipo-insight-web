# -*- coding: utf-8 -*-
from __future__ import annotations
import json, subprocess, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from flask import Flask, Response, jsonify

APP_DIR = Path(__file__).resolve().parent
DATA_FILE = APP_DIR / "ipo_data.json"
app = Flask(__name__)

def read_data() -> Dict[str, Any]:
    if not DATA_FILE.exists():
        return {"ok": True, "mode": "empty", "updated_at": "", "items": [], "message": "ipo_data.json 파일이 없습니다."}
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "mode": "read_error", "updated_at": "", "items": [], "message": f"ipo_data.json 읽기 실패: {exc}"}

HTML_PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<title>공모주 인사이트 Lite</title>
<style>
:root{--bg:#f5f7fb;--card:#fff;--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--dark:#020617;--blue:#2563eb;--yellow:#fee500;--shadow:0 12px 28px rgba(15,23,42,.08)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Noto Sans KR","Segoe UI",sans-serif}button,input,select,textarea{font-family:inherit}.app{max-width:520px;margin:0 auto;min-height:100vh;background:#f8fafc;padding-bottom:86px}.top{position:sticky;top:0;z-index:10;padding:14px 16px 12px;background:rgba(248,250,252,.94);backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}.top-row{display:flex;align-items:center;justify-content:space-between;gap:10px}h1{margin:0;font-size:22px;font-weight:950;letter-spacing:-.04em}.sub{margin-top:3px;color:var(--muted);font-size:12px;font-weight:700}.refresh{border:0;background:var(--dark);color:#fff;padding:10px 13px;border-radius:999px;font-size:13px;font-weight:900;cursor:pointer}.screen{display:none;padding:14px}.screen.active{display:block}.hero{background:linear-gradient(135deg,#020617,#172554);color:#fff;border-radius:24px;padding:20px;box-shadow:var(--shadow);margin-bottom:14px}.hero-label{color:#bfdbfe;font-size:13px;font-weight:800}.hero-main{margin-top:6px;font-size:34px;font-weight:1000;letter-spacing:-.06em}.hero-sub{margin-top:8px;color:#cbd5e1;font-size:13px;line-height:1.6;white-space:pre-wrap}.month-strip{display:flex;gap:8px;overflow-x:auto;padding:2px 0 14px;scrollbar-width:none}.month-strip::-webkit-scrollbar{display:none}.month-btn{flex:0 0 auto;min-width:62px;border:1px solid var(--line);background:#fff;color:var(--muted);border-radius:16px;padding:10px 12px;font-weight:900;cursor:pointer;box-shadow:0 4px 14px rgba(15,23,42,.04)}.month-btn.active{background:var(--dark);color:#fff;border-color:var(--dark)}.toolbar{display:grid;grid-template-columns:1fr 96px;gap:8px;margin-bottom:12px}input,select,textarea{width:100%;border:1px solid var(--line);background:#fff;border-radius:16px;outline:none;font-size:14px}input,select{height:44px;padding:0 12px}textarea{min-height:240px;padding:12px;resize:vertical;line-height:1.65}.list{display:grid;gap:12px}.card{background:#fff;border:1px solid var(--line);border-radius:22px;padding:16px;box-shadow:var(--shadow)}.card-head{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}.name{margin:0;font-size:20px;font-weight:1000;letter-spacing:-.04em;line-height:1.22}.meta{margin-top:5px;color:var(--muted);font-size:13px;line-height:1.45}.pill{flex:0 0 auto;border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:11px;font-weight:900;color:#475569;background:#f8fafc}.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.mini{background:#f8fafc;border-radius:16px;padding:11px;min-width:0}.mini span{display:block;color:var(--muted);font-size:11px;font-weight:800;margin-bottom:4px}.mini b{font-size:13px;word-break:keep-all}.overview{margin-top:12px;background:#f8fafc;border-radius:16px;padding:12px;color:var(--muted);font-size:13px;line-height:1.65}.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:14px}.btn{height:42px;border:1px solid var(--line);border-radius:14px;background:#fff;color:var(--ink);font-size:13px;font-weight:900;cursor:pointer;text-decoration:none;display:inline-flex;align-items:center;justify-content:center}.btn.dark{background:var(--dark);color:#fff;border-color:var(--dark)}.btn.kakao{background:var(--yellow);color:#191919;border-color:#e8d000}.btn.blue{background:var(--blue);color:#fff;border-color:var(--blue)}.detail-card{background:#fff;border:1px solid var(--line);border-radius:24px;padding:18px;box-shadow:var(--shadow)}.back{border:0;background:transparent;color:var(--blue);padding:0;margin-bottom:10px;font-weight:900;cursor:pointer}.detail-title{margin:0;font-size:30px;font-weight:1000;letter-spacing:-.06em;line-height:1.2}.section{margin-top:16px}.section h3{margin:0 0 7px;font-size:16px}.box{background:#f8fafc;border-radius:18px;padding:13px;color:var(--muted);line-height:1.7;font-size:14px;white-space:pre-wrap}.status-box{background:#fff;border:1px solid var(--line);border-radius:22px;padding:16px;box-shadow:var(--shadow);color:var(--muted);line-height:1.7;white-space:pre-wrap;font-size:13px}.empty{background:#fff;border:1px dashed var(--line);color:var(--muted);border-radius:22px;padding:22px;text-align:center;line-height:1.7;font-weight:800}.bottom{position:fixed;left:50%;bottom:0;transform:translateX(-50%);width:min(520px,100%);background:rgba(255,255,255,.94);backdrop-filter:blur(16px);border-top:1px solid var(--line);display:grid;grid-template-columns:repeat(4,1fr);padding:8px 8px max(8px,env(safe-area-inset-bottom));z-index:20}.nav{border:0;background:transparent;border-radius:14px;padding:8px 4px;color:var(--muted);font-size:12px;font-weight:900;cursor:pointer}.nav.active{background:#eff6ff;color:var(--blue)}
</style>
</head>
<body>
<div class="app">
<header class="top"><div class="top-row"><div><h1>공모주 인사이트 Lite</h1><div class="sub">JSON 데이터 기반 · 모바일 카드형</div></div><button class="refresh" onclick="refreshData()">데이터 갱신</button></div></header>
<section id="screenMonthly" class="screen active"><div class="hero"><div class="hero-label">선택월 공모주</div><div id="heroCount" class="hero-main">0개</div><div id="heroSub" class="hero-sub">데이터를 불러오는 중입니다.</div></div><div id="monthStrip" class="month-strip"></div><div class="toolbar"><input id="searchInput" placeholder="종목 검색"/><select id="sortSelect"><option value="date">청약일순</option><option value="name">이름순</option></select></div><div id="monthlyList" class="list"></div></section>
<section id="screenAll" class="screen"><div class="hero"><div class="hero-label">전체 공모주</div><div id="allCount" class="hero-main">0개</div><div id="allSub" class="hero-sub">저장된 전체 일정</div></div><div id="allList" class="list"></div></section>
<section id="screenStatus" class="screen"><div class="hero"><div class="hero-label">데이터 상태</div><div class="hero-main">상태</div><div class="hero-sub">크롤링 실패 시 기존 ipo_data.json을 유지합니다.</div></div><div id="statusBox" class="status-box">확인 중...</div></section>
<section id="screenShare" class="screen"><div class="hero"><div class="hero-label">선택월 공유</div><div class="hero-main">복사</div><div class="hero-sub">카카오톡에 붙여넣기 좋은 월간 요약입니다.</div></div><textarea id="monthShareText" readonly></textarea><div style="height:10px"></div><button class="btn kakao" style="width:100%" onclick="copyMonthShare()">선택월 일정 복사</button></section>
<section id="screenDetail" class="screen"><button class="back" onclick="goBack()">← 목록으로</button><div id="detailView" class="detail-card"></div></section>
<nav class="bottom"><button id="navMonthly" class="nav active" onclick="showScreen('monthly')">월별</button><button id="navAll" class="nav" onclick="showScreen('all')">전체</button><button id="navStatus" class="nav" onclick="showScreen('status')">상태</button><button id="navShare" class="nav" onclick="showScreen('share')">공유</button></nav>
</div>
<script>
let items=[],dataMeta={},selectedMonth=new Date().getMonth()+1,selectedId=null,prevScreen="monthly";const YEAR=new Date().getFullYear(),$=id=>document.getElementById(id);
function escapeHtml(v){return String(v??"").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;")}
function monthOf(d){return d?Number(String(d).slice(5,7)):0}
function md(d){return(!d||String(d).length<10)?"확인 필요":String(d).slice(5).replace("-",".")}
function desc(i){return i.company_overview||i.overview||"업체개요 확인 필요"}
function currentMonthItems(){return items.filter(i=>monthOf(i.subscription_start||i.subscriptionStart)===selectedMonth).sort((a,b)=>String(a.subscription_start||a.subscriptionStart).localeCompare(String(b.subscription_start||b.subscriptionStart)))}
function filteredMonthItems(){const q=$("searchInput").value.toLowerCase(),sort=$("sortSelect").value;return currentMonthItems().filter(i=>`${i.name} ${i.manager} ${desc(i)}`.toLowerCase().includes(q)).sort((a,b)=>sort==="name"?String(a.name).localeCompare(String(b.name),"ko"):String(a.subscription_start||a.subscriptionStart).localeCompare(String(b.subscription_start||b.subscriptionStart)))}
async function loadData(){try{const res=await fetch("/api/data");const data=await res.json();items=data.items||[];dataMeta=data;if(currentMonthItems().length===0&&items[0])selectedMonth=monthOf(items[0].subscription_start||items[0].subscriptionStart);selectedId=currentMonthItems()[0]?.id||items[0]?.id||null;renderAll()}catch(e){$("monthlyList").innerHTML=`<div class="empty">데이터 로드 실패<br>${escapeHtml(e.message)}</div>`;$("statusBox").textContent="데이터 로드 실패: "+e.message}}
async function refreshData(){$("statusBox").textContent="크롤러 실행 중입니다. 실패해도 기존 데이터는 유지됩니다.";try{const res=await fetch("/api/refresh",{method:"POST"});const data=await res.json();alert(data.refreshed?"데이터 갱신 완료":"갱신 실패, 기존 데이터 유지");await loadData();showScreen("status")}catch(e){alert("갱신 실패, 기존 데이터 유지");$("statusBox").textContent="갱신 실패: "+e.message}}
function renderAll(){renderMonths();renderSummary();renderMonthlyList();renderAllList();renderStatus();renderShare()}
function renderMonths(){$("monthStrip").innerHTML=Array.from({length:12},(_,i)=>{const m=i+1,c=items.filter(x=>monthOf(x.subscription_start||x.subscriptionStart)===m).length;return `<button class="month-btn ${m===selectedMonth?"active":""}" onclick="selectMonth(${m})">${m}월<br><small>${c}개</small></button>`}).join("")}
function renderSummary(){const m=currentMonthItems();$("heroCount").textContent=`${m.length}개`;$("heroSub").textContent=`${YEAR}년 ${selectedMonth}월 청약 일정\n마지막 갱신: ${dataMeta.updated_at||"확인 필요"}`;$("allCount").textContent=`${items.length}개`;$("allSub").textContent=`전체 저장 데이터 · ${dataMeta.mode||"json"}`}
function renderMonthlyList(){const list=filteredMonthItems();$("monthlyList").innerHTML=list.length?list.map(renderCard).join(""):`<div class="empty">선택월 일정이 없습니다.</div>`}
function renderAllList(){const all=[...items].sort((a,b)=>String(a.subscription_start||a.subscriptionStart).localeCompare(String(b.subscription_start||b.subscriptionStart)));$("allList").innerHTML=all.length?all.map(renderCard).join(""):`<div class="empty">저장된 공모주 데이터가 없습니다.</div>`}
function renderCard(i){const s=i.subscription_start||i.subscriptionStart,e=i.subscription_end||i.subscriptionEnd;return `<article class="card"><div class="card-head"><div><h2 class="name">${escapeHtml(i.name)}</h2><div class="meta">${escapeHtml(i.market||"시장 확인 필요")} · ${escapeHtml(i.manager||"주관사 확인 필요")}</div></div><span class="pill">${escapeHtml(i.status||"공모주")}</span></div><div class="grid"><div class="mini"><span>청약일</span><b>${md(s)} ~ ${md(e)}</b></div><div class="mini"><span>주관사</span><b>${escapeHtml(i.manager||"확인 필요")}</b></div><div class="mini"><span>공모가</span><b>${escapeHtml(i.final_price||i.price_band||"확인 필요")}</b></div><div class="mini"><span>기관경쟁률</span><b>${escapeHtml(i.institution_competition||"확인 필요")}</b></div></div><div class="overview">${escapeHtml(desc(i))}</div><div class="actions"><button class="btn dark" onclick="openDetail('${escapeHtml(i.id)}')">자세히</button><button class="btn kakao" onclick="copyItem('${escapeHtml(i.id)}')">카톡복사</button></div></article>`}
function renderStatus(){let lines=[];lines.push(`모드: ${dataMeta.mode||"-"}`);lines.push(`마지막 갱신: ${dataMeta.updated_at||"-"}`);lines.push(`총 종목 수: ${items.length}개`);lines.push(`메시지: ${dataMeta.message||"-"}`);if(dataMeta.errors&&dataMeta.errors.length){lines.push("");lines.push("수집 경고/오류:");dataMeta.errors.forEach(e=>lines.push("- "+e))}$("statusBox").textContent=lines.join("\n")}
function renderDetail(){const i=items.find(r=>r.id===selectedId);if(!i){$("detailView").innerHTML=`<div class="empty">종목을 찾을 수 없습니다.</div>`;return}const s=i.subscription_start||i.subscriptionStart,e=i.subscription_end||i.subscriptionEnd;$("detailView").innerHTML=`<h2 class="detail-title">${escapeHtml(i.name)}</h2><div class="meta">${escapeHtml(i.market||"시장 확인 필요")} · ${escapeHtml(i.manager||"주관사 확인 필요")}</div><div class="grid"><div class="mini"><span>청약일</span><b>${md(s)} ~ ${md(e)}</b></div><div class="mini"><span>주관사</span><b>${escapeHtml(i.manager||"확인 필요")}</b></div><div class="mini"><span>희망 공모가</span><b>${escapeHtml(i.price_band||"확인 필요")}</b></div><div class="mini"><span>확정 공모가</span><b>${escapeHtml(i.final_price||"확인 필요")}</b></div><div class="mini"><span>기관경쟁률</span><b>${escapeHtml(i.institution_competition||"확인 필요")}</b></div><div class="mini"><span>상장일</span><b>${escapeHtml(i.listing_date||"확인 필요")}</b></div><div class="mini"><span>매출액</span><b>${escapeHtml(i.revenue||"확인 필요")}</b></div><div class="mini"><span>영업이익</span><b>${escapeHtml(i.operating_profit||"확인 필요")}</b></div></div><div class="section"><h3>업체개요</h3><div class="box">${escapeHtml(desc(i))}</div></div><div class="section"><h3>재무정보</h3><div class="box">매출액: ${escapeHtml(i.revenue||"확인 필요")}\n영업이익: ${escapeHtml(i.operating_profit||"확인 필요")}\n순이익: ${escapeHtml(i.net_income||"확인 필요")}</div></div><div class="section"><h3>출처</h3><div class="box">${escapeHtml(i.source||"확인 필요")}</div></div><div class="actions"><button class="btn kakao" onclick="copyItem('${escapeHtml(i.id)}')">카카오톡 복사</button><a class="btn blue" href="${escapeHtml(i.source_url||"https://dart.fss.or.kr/dsac008/main.do")}" target="_blank">원본 확인</a></div>`}
function itemText(i){if(!i)return"공모주 데이터가 없습니다.";const s=i.subscription_start||i.subscriptionStart,e=i.subscription_end||i.subscriptionEnd;return `[공모주 일정]\n\n종목: ${i.name}\n청약일: ${md(s)} ~ ${md(e)}\n주관사: ${i.manager||"확인 필요"}\n희망 공모가: ${i.price_band||"확인 필요"}\n확정 공모가: ${i.final_price||"확인 필요"}\n기관경쟁률: ${i.institution_competition||"확인 필요"}\n\n업체개요:\n${desc(i)}\n\n매출액: ${i.revenue||"확인 필요"}\n영업이익: ${i.operating_profit||"확인 필요"}\n순이익: ${i.net_income||"확인 필요"}\n\n※ 투자 권유가 아닌 정보 정리용입니다.`}
function renderShare(){const list=currentMonthItems(),lines=[`[공모주 일정] ${YEAR}년 ${selectedMonth}월`,"",`총 ${list.length}개`,""];list.forEach(i=>{const s=i.subscription_start||i.subscriptionStart,e=i.subscription_end||i.subscriptionEnd;lines.push(`- ${i.name}: ${md(s)}~${md(e)} / ${i.manager||"주관사 확인"} / ${i.final_price||i.price_band||"공모가 확인"}`)});lines.push("","※ 투자 권유가 아닌 정보 정리용입니다.");$("monthShareText").value=lines.join("\n")}
async function copyText(t){try{await navigator.clipboard.writeText(t);alert("복사했습니다.")}catch(e){alert("복사 실패. 직접 선택해서 복사해주세요.")}}
function copyItem(id){copyText(itemText(items.find(r=>r.id===id)))}
function copyMonthShare(){copyText($("monthShareText").value)}
function selectMonth(m){selectedMonth=m;selectedId=currentMonthItems()[0]?.id||selectedId;renderAll()}
function openDetail(id){selectedId=id;prevScreen=document.querySelector(".screen.active")?.id==="screenAll"?"all":"monthly";renderDetail();showScreen("detail")}
function goBack(){showScreen(prevScreen||"monthly")}
function showScreen(n){["Monthly","All","Status","Share","Detail"].forEach(k=>{const e=$("screen"+k);if(e)e.classList.remove("active")});["Monthly","All","Status","Share"].forEach(k=>{const e=$("nav"+k);if(e)e.classList.remove("active")});if(n==="monthly"){$("screenMonthly").classList.add("active");$("navMonthly").classList.add("active")}if(n==="all"){$("screenAll").classList.add("active");$("navAll").classList.add("active")}if(n==="status"){$("screenStatus").classList.add("active");$("navStatus").classList.add("active")}if(n==="share"){$("screenShare").classList.add("active");$("navShare").classList.add("active")}if(n==="detail"){$("screenDetail").classList.add("active")}window.scrollTo({top:0,behavior:"smooth"})}
$("searchInput").addEventListener("input",renderMonthlyList);$("sortSelect").addEventListener("input",renderMonthlyList);loadData();
</script>
</body>
</html>"""

@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")

@app.route("/api/data")
def api_data():
    data = read_data()
    if not isinstance(data.get("items"), list):
        data["items"] = []
    return jsonify(data)

@app.route("/api/refresh", methods=["POST", "GET"])
def api_refresh():
    try:
        result = subprocess.run([sys.executable, str(APP_DIR / "crawler.py")], cwd=str(APP_DIR), capture_output=True, text=True, timeout=60)
        data = read_data()
        data["refreshed"] = result.returncode == 0
        data["crawler_stdout"] = result.stdout[-2000:]
        data["crawler_stderr"] = result.stderr[-2000:]
        if result.returncode != 0:
            data["message"] = "크롤러 실행 실패. 기존 ipo_data.json을 유지했습니다."
        return jsonify(data)
    except Exception as exc:
        data = read_data()
        data["refreshed"] = False
        data["message"] = f"크롤러 실행 실패. 기존 ipo_data.json 유지: {exc}"
        return jsonify(data)

@app.route("/api/health")
def health():
    return jsonify({"ok": True, "mode": "json-mobile-lite", "time": datetime.now().isoformat(), "data_exists": DATA_FILE.exists()})

@app.errorhandler(404)
def not_found(_error):
    return Response(HTML_PAGE, mimetype="text/html; charset=utf-8")

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5077, debug=True)
