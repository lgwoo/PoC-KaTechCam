"""LLM 호출 실패를 타입으로 구분한다.

TS 판은 파싱 실패를 `"PARSE_ERROR"` 라는 **문자열**로 흘려보냈다. 그 값이 카테고리
자리에도 들어가고 decision 자리에도 들어가서, 호출부가 "실패"와 "실패라는 이름의 판정"을
구분할 수 없었다. 여기서는 예외로 올린다.
"""

from __future__ import annotations


class LlmError(Exception):
    """모든 LLM 관련 실패의 조상."""

    def __init__(self, stage: str, message: str) -> None:
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


class LlmCallError(LlmError):
    """네트워크·인증·5xx 등 호출 자체가 실패. 응답이 없다."""

    def __init__(self, stage: str, cause: Exception) -> None:
        self.cause = cause
        super().__init__(stage, f"{type(cause).__name__}: {cause}")


class LlmContractError(LlmError):
    """200 은 받았지만 약속한 구조가 아니다. 수리 재시도까지 실패한 경우."""

    def __init__(self, stage: str, raw: str, detail: str) -> None:
        self.raw = raw
        self.detail = detail
        preview = raw[:200].replace("\n", " ")
        super().__init__(stage, f"구조 검증 실패({detail}) — 원문: {preview!r}")
