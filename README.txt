# 공모주 인사이트 자동수집 웹 Mobile v5

이 버전은 휴대폰에서 주소만 눌러 사용할 수 있도록 배포 준비가 된 버전입니다.

## 핵심 구조

- `server.py`가 인터넷 공모주 공개 페이지를 자동 수집합니다.
- `public/index.html`이 모바일 화면에 맞게 월별 일정과 상세 분석을 보여줍니다.
- PWA 설정이 들어 있어 휴대폰 홈 화면에 앱처럼 추가할 수 있습니다.
- 로컬 PC에서만 쓰는 것이 아니라 Render/Replit/Railway 같은 곳에 배포하면 휴대폰에서 바로 접속할 수 있습니다.

## 로컬 실행

```bash
pip install -r requirements.txt
python server.py
```

접속 주소:

```text
http://127.0.0.1:5077
```

단, 이 주소는 실행 중인 PC에서만 바로 열립니다. 휴대폰에서 쓰려면 아래 배포 방식이 필요합니다.

## 휴대폰에서 바로 쓰는 방법

### 방법 1. Render에 배포

1. 이 폴더를 GitHub 저장소에 올립니다.
2. Render에서 New Web Service를 선택합니다.
3. GitHub 저장소를 연결합니다.
4. Build Command:
   `pip install -r requirements.txt`
5. Start Command:
   `gunicorn server:app --bind 0.0.0.0:$PORT`
6. 배포가 끝나면 Render가 만들어주는 주소로 휴대폰에서 접속합니다.
7. 아이폰은 Safari 공유 버튼 → 홈 화면에 추가.
8. 안드로이드는 Chrome 메뉴 → 홈 화면에 추가 또는 앱 설치.

### 방법 2. Replit에 올리기

1. Replit에서 Python 프로젝트를 만듭니다.
2. 파일 전체를 업로드합니다.
3. `pip install -r requirements.txt` 실행 후 `python server.py` 또는 배포 설정을 합니다.
4. Replit이 제공하는 URL로 휴대폰에서 접속합니다.

## 주의

- 휴대폰 자체에서 `server.py`를 실행하는 방식이 아닙니다.
- 휴대폰은 접속만 하고, 데이터 수집은 서버가 수행합니다.
- API 인증키 없이 공개 HTML을 읽는 구조라 사이트 구조 변경 시 일부 데이터가 누락될 수 있습니다.
