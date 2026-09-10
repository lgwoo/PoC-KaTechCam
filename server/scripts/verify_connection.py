"""Step 0 연결 검증.

계획서가 미검증으로 표시한 네 가지를 실제 호출로 확인한다.
  1. Elice MLAPI 엔드포인트가 응답하는가 + 어떤 모델 ID를 쓸 수 있는가
  2. response_format={"type":"json_object"} 가 동작하는가
  3. temperature 를 명시하면 400 이 나는가 (형제 프로젝트에서 Luna 가 그랬다)
  4. OpenAI Moderation 이 별도 키로 동작하는가

실행: python scripts/verify_connection.py
"""

import json
import os
import sys
import time

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

PASS = "PASS"
FAIL = "FAIL"
results: list[tuple[str, str, str]] = []


def record(name: str, status: str, detail: str) -> None:
    results.append((name, status, detail))
    mark = "OK  " if status == PASS else "FAIL"
    print(f"[{mark}] {name}: {detail}")


def elice_client() -> OpenAI:
    base_url = os.environ["ELICE_MLAPI_BASE_URL"]
    if not base_url.rstrip("/").endswith("/v1"):
        raise SystemExit(f"ELICE_MLAPI_BASE_URL 은 /v1 로 끝나야 한다: {base_url}")
    return OpenAI(api_key=os.environ["ELICE_MLAPI_API_KEY"], base_url=base_url)


def check_models(client: OpenAI) -> str | None:
    """사용 가능한 모델 ID 목록. Haiku 로 보이는 것을 골라 돌려준다."""
    try:
        listed = client.models.list()
    except Exception as exc:
        record("models.list", FAIL, f"{type(exc).__name__}: {exc}")
        return None

    ids = [m.id for m in listed.data]
    record("models.list", PASS, f"{len(ids)}개 — {ids}")
    for candidate in ids:
        if "haiku" in candidate.lower():
            return candidate
    return ids[0] if ids else None


def check_chat(client: OpenAI, model: str) -> bool:
    start = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=64,
            messages=[{"role": "user", "content": "한 단어로만 대답해라: 안녕?"}],
        )
    except Exception as exc:
        record("chat.completions", FAIL, f"{type(exc).__name__}: {exc}")
        return False
    latency_ms = int((time.monotonic() - start) * 1000)
    text = (response.choices[0].message.content or "").strip()
    record("chat.completions", PASS, f"{latency_ms}ms — {text!r}")
    return True


def check_json_object(client: OpenAI, model: str) -> bool:
    """계획서의 구조화 출력 전략 전체가 이 기능에 걸려 있다."""
    try:
        response = client.chat.completions.create(
            model=model,
            max_tokens=128,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": '반드시 이 JSON 형식으로만 답하라: {"category": string, "ok": boolean}',
                },
                {"role": "user", "content": "category 는 NORMAL, ok 는 true 로 채워라."},
            ],
        )
    except Exception as exc:
        record("response_format=json_object", FAIL, f"{type(exc).__name__}: {exc}")
        return False

    raw = (response.choices[0].message.content or "").strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        record("response_format=json_object", FAIL, f"200 이지만 JSON 파싱 실패({exc}) — {raw!r}")
        return False
    record("response_format=json_object", PASS, f"{parsed}")
    return True


def check_temperature(client: OpenAI, model: str) -> None:
    """temperature 를 받아주는지 확인한다.

    2026-09-09 실측: claude-haiku-4-5 는 temperature 를 명시하면 502(Cloudflare
    origin_bad_gateway)를 뱉는다. 같은 연결의 통제 호출은 성공하므로 일시 장애가
    아니라 확정적 거부다. 형제 프로젝트에서 Luna 도 같았다(그쪽은 400).
    거부는 '알려진 제약'이므로 전체 검증을 실패로 만들지 않는다 — 다만 허용으로
    바뀌면 그때는 알아야 하므로 계속 확인한다.
    """
    try:
        client.chat.completions.create(
            model=model,
            max_tokens=16,
            temperature=0,
            messages=[{"role": "user", "content": "hi"}],
        )
    except Exception as exc:
        record(
            "temperature",
            PASS,
            f"예상대로 거부됨 — LlmClient 는 절대 보내지 않는다 ({type(exc).__name__})",
        )
        return
    record("temperature", PASS, "허용됨 — 예상과 다르다. 판정 콜에 temperature=0 을 쓸 수 있다")


def check_moderation() -> None:
    api_key = os.environ.get("OPENAI_MODERATION_API_KEY")
    if not api_key:
        record("moderation", FAIL, "OPENAI_MODERATION_API_KEY 미설정")
        return
    # baseURL 을 명시하지 않으면 SDK 가 환경변수 OPENAI_BASE_URL 을 주워 Elice 로 보낸다.
    client = OpenAI(api_key=api_key, base_url="https://api.openai.com/v1")
    model = os.environ.get("OPENAI_MODERATION_MODEL", "omni-moderation-latest")
    start = time.monotonic()
    try:
        response = client.moderations.create(model=model, input="죽어버려")
    except Exception as exc:
        record("moderation", FAIL, f"{type(exc).__name__}: {exc}")
        return
    latency_ms = int((time.monotonic() - start) * 1000)
    result = response.results[0]
    flagged = [name for name, hit in result.categories.model_dump().items() if hit]
    record("moderation", PASS, f"{latency_ms}ms — flagged={result.flagged}, categories={flagged}")


def main() -> int:
    missing = [
        name
        for name in ("ELICE_MLAPI_BASE_URL", "ELICE_MLAPI_API_KEY")
        if not os.environ.get(name)
    ]
    if missing:
        raise SystemExit(f".env 에 다음이 비어 있다: {', '.join(missing)}")

    client = elice_client()
    print(f"base_url = {os.environ['ELICE_MLAPI_BASE_URL']}\n")

    discovered = check_models(client)
    model = os.environ.get("ELICE_MODEL") or discovered
    if model:
        print(f"\n사용할 모델: {model}\n")
        if check_chat(client, model):
            check_json_object(client, model)
            check_temperature(client, model)
    else:
        print("\n모델 ID 미확정 — Elice 검사를 건너뛴다. 콘솔에서 엔드포인트 URL 을 확인할 것.\n")

    # Elice 가 실패해도 Moderation 은 독립적이므로 항상 확인한다.
    check_moderation()

    failed = [name for name, status, _ in results if status == FAIL]
    print(f"\n{'-' * 60}")
    if not os.environ.get("ELICE_MODEL"):
        print(f".env 의 ELICE_MODEL 을 채울 것: ELICE_MODEL={model}")
    print(f"{len(results) - len(failed)}/{len(results)} 통과" + (f" — 실패: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
