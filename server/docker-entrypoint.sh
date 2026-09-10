#!/bin/sh
# 볼륨이 비어 있으면 씨드 DB 를 한 번 깔고 시작한다.
#
# 왜 필요한가: DB 는 볼륨에 두고 이미지에 넣지 않는다(아동 발화가 쌓이는 곳이다).
# 그래서 팀원이 처음 띄우면 시나리오도 기록도 없는 빈 화면을 본다. 씨드가 있으면
# 시나리오 7개와 지난 역할극 50개가 처음부터 보인다.
#
# 이미 DB 가 있으면 절대 덮지 않는다 — 남이 돌린 기록을 지우는 일이 되면 안 된다.
set -e

DB="${ET_DB_PATH:-/data/neuringo_poc.db}"
SEED=/app/seed/neuringo_poc.seed.db

if [ ! -f "$DB" ] && [ -f "$SEED" ]; then
    echo "[entrypoint] 빈 볼륨이다 — 씨드 DB 를 깐다: $SEED -> $DB"
    mkdir -p "$(dirname "$DB")"
    cp "$SEED" "$DB"
fi

exec "$@"
