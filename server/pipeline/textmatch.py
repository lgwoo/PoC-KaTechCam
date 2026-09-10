"""한국어 텍스트 겹침 판정.

형태소 분석기를 쓰지 않는다 — konlpy/mecab 은 설치가 무겁고 PoC 의존성을 늘린다.
대신 조사를 제거한 뒤 문자 n-gram Jaccard 로 근사한다. 임계값은 추측이 아니라
녹화된 실제 턴으로 튜닝해야 하는 값이므로 상수로 모아 두고 호출부에서 주입한다.
"""

from __future__ import annotations

import re
import unicodedata

# 어말 조사·어미. 긴 것부터 지워야 "에서" 가 "에" 로 잘리지 않는다.
_PARTICLES = (
    "이라고", "라고", "이랑", "에서", "에게", "한테", "부터", "까지", "보다", "처럼",
    "으로", "에는", "에도", "이나", "나마", "든지", "마저", "조차", "밖에",
    "은", "는", "이", "가", "을", "를", "에", "의", "와", "과", "도", "만", "로", "랑", "께",
)

# 그 자체로는 어떤 목표의 증거도 될 수 없는 발화. 이것만으로 목표가 인정되면 안 된다.
FILLER_WORDS = frozenset(
    {
        "응", "어", "음", "네", "예", "웅", "ㅇㅇ", "ㅇ", "아니", "노",
        "몰라", "모르겠어", "모르겠는데", "글쎄", "그냥", "그래", "맞아", "그렇구나",
        "안녕", "뭐", "왜", "누구", "어디", "언제",
    }
)

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """비교용 정규화: NFC 통일, 구두점 제거, 공백 축약, 소문자화."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = _PUNCT.sub(" ", text)
    text = _SPACE.sub(" ", text)
    return text.strip().lower()


def strip_particles(token: str) -> str:
    """어말 조사 하나를 제거한다. 제거 후 남는 게 너무 짧으면 원형을 유지한다."""
    for particle in _PARTICLES:
        if len(token) > len(particle) + 1 and token.endswith(particle):
            return token[: -len(particle)]
    return token


def content_tokens(text: str) -> list[str]:
    """조사를 뗀 내용어 토큰. 필러는 제외한다."""
    tokens = [strip_particles(t) for t in normalize(text).split()]
    return [t for t in tokens if t and t not in FILLER_WORDS]


def is_filler_only(text: str) -> bool:
    """발화 전체가 필러뿐인가 — 증거로 인정하면 안 되는 발화."""
    return not content_tokens(text)


def char_ngrams(text: str, n: int = 2) -> set[str]:
    """공백까지 제거한 문자 n-gram. 한국어는 띄어쓰기가 불안정해 공백을 빼고 센다."""
    compact = normalize(text).replace(" ", "")
    if len(compact) < n:
        return {compact} if compact else set()
    return {compact[i : i + n] for i in range(len(compact) - n + 1)}


def jaccard(a: str, b: str, n: int = 2) -> float:
    """문자 n-gram Jaccard 유사도. 0.0 ~ 1.0."""
    ga, gb = char_ngrams(a, n), char_ngrams(b, n)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def containment(needle: str, haystack: str, n: int = 2) -> float:
    """needle 의 n-gram 중 haystack 에 들어 있는 비율.

    Jaccard 와 달리 길이 차이에 벌점을 주지 않는다. "후보가 정답 문구를 품고 있는가"
    처럼 짧은 쪽이 긴 쪽에 삼켜졌는지를 볼 때 쓴다.
    """
    gn, gh = char_ngrams(needle, n), char_ngrams(haystack, n)
    if not gn:
        return 0.0
    return len(gn & gh) / len(gn)


def contains_normalized(haystack: str, needle: str) -> bool:
    """정규화 후 부분 문자열 포함. 방향이 중요하다 — 반대로 쓰면 안 된다."""
    hay, need = normalize(haystack), normalize(needle)
    return bool(need) and need in hay
