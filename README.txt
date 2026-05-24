# 공모주 인사이트 모바일 최종본

## 핵심
- 모바일 전용 카드형 UI
- DART/KRX API 키 없이 공개 웹페이지 자동수집
- DART 청약달력 → KRX KIND → 38커뮤니케이션 → IPO38 순서로 시도
- 수집 실패 시 수집상태 화면에 원인 표시
- server.py 단일 파일로 첫 화면 제공
- public/Profile 폴더 불필요

## Render 설정

Build Command:
pip install -r requirements.txt

Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1

## 파일 구조
server.py
requirements.txt
Procfile
runtime.txt
render.yaml
README.txt
