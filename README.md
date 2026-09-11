# 느링고 역할극 PoC

![](docs/screenshots/01-console-scenario.png)

![](docs/screenshots/03-console-turn.png)

![](docs/screenshots/04-console-turn-detail.png)

![](docs/screenshots/05-pipeline.png)

![](docs/screenshots/06-archive-list.png)

![](docs/screenshots/08-archive-turn-detail.png)

## 실행

Docker Desktop 실행 후:

```bash
cp server/.env.example server/.env
```

`server/.env`:

| 항목 | | 값 |
|---|---|---|
| `ELICE_MLAPI_BASE_URL` | 필수 | `https://mlapi.run/<엔드포인트-id>/v1` (끝에 `/v1` 필수) |
| `ELICE_MLAPI_API_KEY` | 필수 | Elice MLAPI 키 |
| `ELICE_MODEL` | 필수 | 예: `claude-haiku-4-5` |
| `OPENAI_MODERATION_API_KEY` | 선택 | OpenAI 키 (Elice 키와 다름) |
| `OPENAI_MODERATION_MODEL` | 선택 | 기본 `omni-moderation-latest` |
| `MAX_OUTPUT_TOKENS` | 선택 | 기본 `2048` |

```bash
docker compose up --build
```

http://localhost:8080
