# 느링고 역할극 PoC — API
#
# 로컬 개발은 server/.venv 로 하고, 이 이미지는 팀원이 같은 것을 돌려 볼 때 쓴다.
# 파이썬 3.14 를 쓴다 — state/models.py 가 StrictEnum 대신 StrEnum 을 쓰고
# app/config.py 가 `str | None` 문법을 쓰므로 3.11 미만에서는 돌지 않는다.
FROM python:3.14-slim

# 파이썬이 .pyc 를 컨테이너 안에 쌓지 않게 하고, 로그를 버퍼링 없이 흘린다.
# 버퍼링을 두면 docker logs 에 아무것도 안 보이다가 한꺼번에 쏟아진다.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성을 먼저 넣어 레이어를 나눈다 — 소스만 고칠 때 재설치를 하지 않는다.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 처음 띄웠을 때 빈 화면이 아니게 씨드 DB 를 넣어 둔다. entrypoint 가 볼륨이
# 비어 있을 때만 한 번 깐다 — 이미 기록이 있으면 절대 덮지 않는다.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# DB 는 아동 발화가 쌓이는 곳이라 이미지에 넣지 않고 볼륨으로 뺀다.
# 이 경로를 compose 가 볼륨으로 잡는다. 안 잡으면 컨테이너를 지울 때 기록이 사라진다.
ENV ET_DB_PATH=/data/neuringo_poc.db
RUN mkdir -p /data

EXPOSE 8000

# 헬스 체크는 /health 를 본다 — 라우터 prefix 밖이라 /api 를 붙이지 않는다.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health').read()"

# --reload 를 넣지 않는다. 세션 진행 상태가 프로세스 메모리에만 있어서
# 재시작이 곧 진행 중인 역할극 전부를 잃는 일이다(실제로 그렇게 한 번 날렸다).
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
