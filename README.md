# 느링고 역할극 PoC

아이가 캐릭터와 대화하면서 친구의 마음을 읽어 보는 역할극이다. 한 턴마다 입력 검열 →
발화 판정 → 마스코트 개입 결정 → 대사 생성 → 출력 검열 → 응답 판정 → 목표 확정이
돌고, 그 과정 전부가 화면과 DB에 남는다.

콘솔에는 세 화면이 있다. **역할극 콘솔**에서 직접 해 보고, **파이프라인 구조**에서 한 턴에
어떤 모델이 몇 번 불리는지 보고, **지난 역할극**에서 지금까지의 기록을 읽는다.

---

## 화면

### 역할극 콘솔 — 시나리오 고르기

시나리오는 전부 AI가 짓는다. 마이크로 목표와 인정 근거는 아이에게 보이지 않고, 시나리오를
만드는 데 걸린 시간도 같이 저장해 둔다.

![시나리오 상세](docs/screenshots/01-console-scenario.png)

### 역할극 진행

마스코트가 장면을 소개하고 캐릭터는 1인칭으로만 말한다. 오른쪽에서 목표 달성 현황과
도움 집계를 실시간으로 본다.

![턴 진행](docs/screenshots/03-console-turn.png)

### 턴 상세 — 무엇을 왜 안 내보냈나

한 턴에 후보를 최대 3개까지 만들고, 판정을 통과한 것만 아이에게 간다. 떨어진 후보와 그
이유가 전부 남는다. 아래쪽 간트에서 겹치는 막대가 실제로 병렬로 돈 구간이다.

![턴 상세](docs/screenshots/04-console-turn-detail.png)

### 파이프라인 구조

세로선 하나가 참여자 하나, 가로 화살표 하나가 실제 호출 한 번이다. 코드 조각은 실제
서버 소스에서 잘라 온다 — 손으로 베껴 두면 코드를 고쳤을 때 이 페이지만 옛 내용으로 남는다.

![파이프라인](docs/screenshots/05-pipeline.png)

### 지난 역할극

서버를 재시작해도 DB에 남은 기록은 여기서 읽힌다. 세션을 고르면 대화 전문과 턴별 판정,
단계별 소요가 펼쳐진다.

![지난 역할극 목록](docs/screenshots/06-archive-list.png)

![지난 역할극 턴 상세](docs/screenshots/08-archive-turn-detail.png)

> 이어 갈 수는 없다. 세션 진행 상태는 서버 프로세스 메모리에만 있어서, 재시작하면
> 기록은 남지만 대화를 다시 이어붙일 수는 없다. 화면에 `읽기 전용`으로 표시된다.

나머지 화면(`02`, `07`)까지 [docs/screenshots/](docs/screenshots/)에 있다.

---

## Docker로 돌리기

### 1. Docker Desktop 실행

Windows·macOS는 **Docker Desktop**을 먼저 켜야 한다. 앱을 실행하고 고래 아이콘이
"Engine running" 이 될 때까지 기다린다. 켜졌는지는 이걸로 확인한다.

```bash
docker info
```

`Cannot connect to the Docker daemon` 이 나오면 아직 안 켜진 것이다. Docker Desktop을
켜지 않고 `docker compose` 를 치면 이 오류가 난다.

리눅스는 데몬이 이미 돌고 있으면 그냥 다음으로 넘어가면 된다.

### 2. API 키 넣기

`server/.env` 를 만들어야 한다. 이 파일은 `.gitignore` 에 있어서 저장소에 올라가지 않는다.

```bash
cp server/.env.example server/.env
```

그리고 값을 채운다.

| 항목 | 필수 | 설명 |
|---|---|---|
| `ELICE_MLAPI_BASE_URL` | 필수 | `https://mlapi.run/<엔드포인트-id>/v1` — **끝의 `/v1` 이 없으면 전부 404 다** |
| `ELICE_MLAPI_API_KEY` | 필수 | Elice MLAPI 키. 발화 판정·대사 생성·응답 판정·목표 스캔을 다 이 모델이 한다 |
| `ELICE_MODEL` | 필수 | 모델 ID (예: `claude-haiku-4-5`) |
| `OPENAI_MODERATION_API_KEY` | 선택 | **진짜 OpenAI 키**. Elice에는 `/v1/moderations` 가 없어서 따로 필요하다 |
| `OPENAI_MODERATION_MODEL` | 선택 | 기본 `omni-moderation-latest` |
| `MAX_OUTPUT_TOKENS` | 선택 | 기본 2048 |

세 개(`BASE_URL`, `API_KEY`, `MODEL`)가 비어 있으면 컨테이너가 뜨자마자 **무엇이 비었는지
말하고 멈춘다.** 조용히 이상하게 도는 일은 없다.

`OPENAI_MODERATION_API_KEY` 는 없어도 돌아가지만, 그러면 안전 검열 한 겹이 빠진 상태다.
로그에 경고가 찍히고, 화면에도 `Moderation 미설정` 으로 나온다. 아동 발화를 다루는
서비스이므로 실제 테스트에는 넣는 게 맞다.

Elice 키와 OpenAI 키를 **같은 값으로 넣지 않는다.** Moderation 클라이언트는 호출 주소를
`api.openai.com` 으로 고정해 두었다 — 환경변수로 Elice 쪽으로 돌릴 수 없게 막은 것이고,
아동 발화가 엉뚱한 엔드포인트로 나가는 일을 방지하려는 것이다.

`verify_connection.py` 로 키가 실제로 먹는지 미리 확인할 수 있다.

```bash
cd server && python scripts/verify_connection.py
```

### 3. 띄우기

```bash
docker compose up --build
```

빌드에 몇 분 걸린다. 끝나면 콘솔이 열린다.

**http://localhost:8080**

처음 띄우면 **시나리오 7개와 지난 역할극 50개가 이미 들어 있다.** 볼륨이 비어 있을 때
씨드 DB를 한 번 깔기 때문이다. 이미 기록이 있으면 절대 덮지 않으니, 각자 돌린 대화가
지워질 걱정은 없다.

지난 기록을 읽는 데는 API 키가 필요 없다. DB만 읽기 때문이다 — 키는 새 역할극을 돌릴
때만 쓴다. 그래서 키를 받기 전에도 지금까지의 테스트 결과를 먼저 훑어볼 수 있다.

내릴 때는 이렇게 한다.

```bash
docker compose down
```

`docker compose down -v` 는 볼륨까지 지운다. **그동안 돌린 역할극 기록이 전부 사라지므로**
정말 초기화하고 싶을 때만 쓴다.

---

## 구성

| | |
|---|---|
| API | FastAPI + uvicorn (`server/`) · 포트 8000, 컨테이너 밖으로 안 내보낸다 |
| 콘솔 | React + Vite, nginx로 서빙 (`web/`) · 포트 8080 |
| 저장 | SQLite. Docker 볼륨 `roleplay-db` 에 둔다 |

nginx가 `/api` 를 API 컨테이너로 넘겨서 브라우저는 전부 같은 주소로 부른다. 그래서
CORS가 아예 생기지 않는다.

DB는 이미지에 넣지 않고 볼륨으로 뺀다 — 아동 발화가 쌓이는 곳이라서 이미지·저장소에
섞지 않는다. 저장소에 올라가는 것은 `server/seed/neuringo_poc.seed.db` 하나뿐이고,
그 안의 발화는 전부 테스트용으로 지어낸 문장이다.

---

## Docker 없이 (개발용)

```bash
# API
cd server
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# 콘솔
cd web && npm install && npm run dev     # http://localhost:5173
```

`--reload` 는 붙이지 않는 게 좋다. 세션 진행 상태가 프로세스 메모리에만 있어서, 파일
하나만 고쳐도 재시작하면서 진행 중인 역할극이 전부 날아간다.

테스트는 LLM을 호출하지 않아서 키 없이도 돈다.

```bash
cd server && .venv/Scripts/python -m pytest
```
