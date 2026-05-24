공모주 인사이트 Lite - JSON 기반 모바일 최종본

server.py: 모바일 웹
crawler.py: 공모주 공개 페이지 크롤링 후 ipo_data.json 갱신
ipo_data.json: 웹이 읽는 데이터

Render Start Command:
gunicorn server:app --bind 0.0.0.0:$PORT --timeout 120 --workers 1
