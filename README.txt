# 공모주 인사이트 - 모바일 안정형 최종본

## 핵심 변경
- 외부 자동수집에 실패해도 앱이 502로 죽지 않도록 구조 변경
- /api/ipos는 항상 캐시 또는 기본 저장 데이터를 반환
- 외부 수집은 /api/refresh에서만 시도
- 수집 실패 시 기존 데이터 유지
- 모바일 전용 카드형 UI
- 카카오톡 공유 문구 복사

## 파일 구조
server.py
requirements.txt
Procfile
runtime.txt
render.yaml
README.txt

## Render 설정
Build Command:
pip install -r requirements.txt

Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1
