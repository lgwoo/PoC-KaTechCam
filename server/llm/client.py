"""Elice MLAPI 클라이언트와 단계 계측.

두 가지 불변식을 코드로 강제한다.

1. **계측 안 된 호출을 만들 수 없다.** 모든 메서드가 `stage` 와 `recorder` 를 필수로
   받는다. TS 판은 provider 가 latencyMs 를 반환했는데 호출부가 그냥 버렸다 —
   입력 안전검사·Moderation·PII 마스킹·후보별 생성/판단 시간이 전부 유실됐다.

2. **temperature 를 절대 보내지 않는다.** 2026-09-09 실측: claude-haiku-4-5 에
   temperature 를 명시하면 502(Cloudflare origin_bad_gateway)가 난다. 같은 연결의
   통제 호출은 성공하므로 일시 장애가 아니라 확정적 거부다. 형제 프로젝트의 Luna 도
   같았다. 기본값에 맡긴다.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Iterator
from typing import NamedTuple, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from llm.contracts import schema_hint
from llm.errors import LlmCallError, LlmContractError
from state.models import StageTiming

T = TypeVar("T", bound=BaseModel)

logger = logging.getLogger(__name__)


class CallResult(NamedTuple):
    """한 번의 LLM 호출에서 건져낸 것 전부.

    본문만 돌려주면 usage 가 함수를 벗어나지 못하고 사라진다. 그 상태로는 단계별
    소요 시간이 있어도 프롬프트가 길어서 느린 건지 출력이 길어서 느린 건지 나눌 수
    없고, 토큰 단가를 곱할 수 없으니 세션당 원가도 못 낸다.
    """

    text: str
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    finish_reason: str | None
    response_model: str | None
    request_chars: int


class SpanSlot:
    """호출이 끝나야 알 수 있는 값을 span 이 닫히기 전에 받아 두는 자리.

    토큰과 finish_reason 은 응답이 와야 정해지는데 span 은 진입 시점에 열린다.
    아무도 채우지 않으면 전부 None 으로 남는다 — Moderation 처럼 usage 가 없는
    호출이 그렇다.
    """

    __slots__ = ("fields",)

    def __init__(self) -> None:
        self.fields: dict[str, object] = {}

    def attach(self, result: CallResult) -> None:
        self.fields = {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
            "finish_reason": result.finish_reason,
            "response_model": result.response_model,
            "request_chars": result.request_chars,
        }


class StageRecorder:
    """한 턴 안의 단계별 타이밍을 모은다.

    duration 만으로는 부족하다 — 목표 스캔과 생성 루프가 병렬로 도는 구간이 있어서
    duration 을 다 더하면 실제 체감 시간보다 훨씬 커진다. 턴 시작 기준 오프셋을 같이
    남겨야 콘솔에서 간트로 그릴 수 있고, 무엇이 실제로 병렬이었는지 보인다.
    """

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self.timings: list[StageTiming] = []

    def total_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    @contextlib.contextmanager
    def span(self, stage: str, attempt_no: int | None = None) -> Iterator[SpanSlot]:
        slot = SpanSlot()
        start = time.monotonic()
        offset_ms = int((start - self._t0) * 1000)
        ok = True
        try:
            yield slot
        except Exception:
            ok = False
            raise
        finally:
            self.timings.append(
                StageTiming(
                    stage=stage,
                    start_offset_ms=offset_ms,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    ok=ok,
                    attempt_no=attempt_no,
                    **slot.fields,
                )
            )


_JSON_INSTRUCTION = """
반드시 아래 JSON 형식으로만 답하라. 다른 텍스트나 코드펜스를 덧붙이지 마라.
{schema}
""".strip()


class LlmClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 2048,
        timeout: float = 60.0,
    ) -> None:
        if not base_url.rstrip("/").endswith("/v1"):
            # SDK 가 baseUrl 뒤에 /chat/completions 만 붙인다. /v1 이 없으면 404.
            raise ValueError(f"base_url 은 /v1 로 끝나야 한다: {base_url}")
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout)
        self._model = model
        self._max_tokens = max_tokens
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._model

    @property
    def timeout_s(self) -> float:
        return self._timeout

    @property
    def sdk_max_retries(self) -> int | None:
        """SDK 가 조용히 재시도하는 횟수.

        이 재시도는 span 안에서 일어나서 duration_ms 하나에 접혀 들어간다. 값을
        남겨 두지 않으면 느린 호출이 정말 느린 건지 재시도가 섞인 건지 모른다.
        """
        return getattr(self._client, "max_retries", None)

    async def _call(
        self, *, system: str, user: str, json_mode: bool
    ) -> CallResult:
        kwargs: dict = {}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        # temperature 는 넣지 않는다 — 위 모듈 주석 참고.
        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        # OpenAI 호환 엔드포인트라도 usage 를 안 주는 경우가 있다. 없으면 None 으로
        # 남기고 넘어간다 — 계측이 없다고 호출을 실패시킬 일은 아니다.
        choice = response.choices[0] if response.choices else None
        usage = getattr(response, "usage", None)
        return CallResult(
            text=(choice.message.content or "").strip() if choice else "",
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            total_tokens=getattr(usage, "total_tokens", None),
            finish_reason=getattr(choice, "finish_reason", None) if choice else None,
            response_model=getattr(response, "model", None),
            request_chars=len(system) + len(user),
        )

    async def text(
        self,
        *,
        stage: str,
        recorder: StageRecorder,
        system: str,
        user: str,
        attempt_no: int | None = None,
    ) -> str:
        """평문 응답. 캐릭터 대사처럼 구조가 필요 없는 것에 쓴다."""
        try:
            with recorder.span(stage, attempt_no) as slot:
                result = await self._call(system=system, user=user, json_mode=False)
                slot.attach(result)
                return result.text
        except Exception as exc:
            raise LlmCallError(stage, exc) from exc

    async def structured(
        self,
        *,
        stage: str,
        recorder: StageRecorder,
        system: str,
        user: str,
        contract: type[T],
        attempt_no: int | None = None,
    ) -> T:
        """구조화 응답. 검증 실패 시 **수리 재시도 1회** 후 예외를 올린다.

        sentinel 문자열을 돌려주지 않는다 — 호출부가 "실패"와 "실패라는 이름의 값"을
        구분할 수 있어야 한다.
        """
        system_with_schema = f"{system}\n\n{_JSON_INSTRUCTION.format(schema=schema_hint(contract))}"

        try:
            with recorder.span(stage, attempt_no) as slot:
                result = await self._call(system=system_with_schema, user=user, json_mode=True)
                slot.attach(result)
                raw = result.text
        except Exception as exc:
            raise LlmCallError(stage, exc) from exc

        parsed, detail = _validate(raw, contract)
        if parsed is not None:
            return parsed

        # 왜 깨졌는지 보이지 않으면 프롬프트를 고칠 수 없다.
        logger.warning("[%s] 구조 검증 실패 → 수리 재시도. %s | 원문 %.200r", stage, detail, raw)

        # 수리 재시도: 모델에게 자기 출력과 검증 오류를 되돌려주고 고치게 한다.
        repair_user = (
            f"{user}\n\n"
            f"[직전 출력이 형식을 어겼다]\n{raw}\n\n"
            f"[검증 오류]\n{detail}\n\n"
            f"위 오류를 고쳐 같은 내용을 올바른 JSON 으로만 다시 출력하라."
        )
        try:
            with recorder.span(f"{stage}.repair", attempt_no) as slot:
                repair_result = await self._call(
                    system=system_with_schema, user=repair_user, json_mode=True
                )
                slot.attach(repair_result)
                repaired = repair_result.text
        except Exception as exc:
            raise LlmCallError(f"{stage}.repair", exc) from exc

        parsed, repair_detail = _validate(repaired, contract)
        if parsed is not None:
            return parsed

        raise LlmContractError(stage, repaired, repair_detail or "unknown")


def _unwrap_envelope(payload: object, contract: type[BaseModel]) -> object:
    """모델이 결과를 한 겹 감싸서 주는 경우를 편다.

    실측: claude-haiku-4-5 가 `{"output": {...}}`, `{"parameter": {...}}` 처럼
    래퍼 키를 붙여 보낸다. 필수 필드가 있는 계약은 이때 검증 실패로 잡히지만,
    필드가 전부 선택적인 계약(GoalScanContract)은 **빈 값으로 조용히 통과**한다 —
    목표 스캐너가 아무것도 인정하지 않는 것처럼 보였던 원인이 이것이었다.
    """
    if not isinstance(payload, dict) or len(payload) != 1:
        return payload
    (key, value), = payload.items()
    if key in contract.model_fields or not isinstance(value, dict):
        return payload
    return value


def _validate(raw: str, contract: type[T]) -> tuple[T | None, str]:
    """JSON 파싱 + Pydantic 검증. 실패하면 (None, 사람이 읽을 수 있는 사유)."""
    text = raw.strip()
    # json_object 모드를 켜도 모델이 코드펜스를 붙이는 경우가 있다.
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        text = text.rsplit("```", 1)[0].strip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON 파싱 실패: {exc}"

    payload = _unwrap_envelope(payload, contract)

    try:
        return contract.model_validate(payload), ""
    except ValidationError as exc:
        return None, "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
