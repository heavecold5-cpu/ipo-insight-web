# 공모주 인사이트 모바일 최종본 MAYFIX

## 핵심
- 모바일 전용 카드형 UI
- DART/KRX API 키 없이 공개 웹페이지 자동수집
- DART 청약달력 → KRX KIND → 38커뮤니케이션 → IPO38 순서로 시도
- 수집 실패 시 수집상태 화면에 원인 표시
- server.py 단일 파일로 첫 화면 제공
- public/Profile 폴더 불필요
- DART 페이지 월 선택 목록 때문에 1월로 잘못 잡히던 문제 수정

## Render 설정

Build Command:
pip install -r requirements.txt

Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

## 확인 주소

웹:
https://ipo-insight-web.onrender.com

상태:
https://ipo-insight-web.onrender.com/api/health

강제 새로고침:
https://ipo-insight-web.onrender.com/api/ipos?year=2026&force=1
