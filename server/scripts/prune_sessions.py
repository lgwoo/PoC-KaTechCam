"""끝까지 가지 않은 세션을 기록에서 지운다.

    python -m scripts.prune_sessions --dry-run    (기본: 지우지 않고 목록만)
    python -m scripts.prune_sessions --yes

end_reason 이 없다는 건 목표 달성도, 턴 상한도, 안전 중단도 아닌 채로 멈췄다는 뜻이다.
대부분 사람이 콘솔에서 몇 마디 해보고 닫은 것이고, 대화가 중간에 끊겨 있어서 기록으로
읽어도 판단 근거가 되지 않는다. 목록만 늘리고 지표는 흐린다.

지우기 전에 무엇이 지워지는지 반드시 찍는다 — 아동 발화가 담긴 기록이라 되돌릴 수 없다.
"""

from __future__ import annotations

import argparse
import sys

from app.config import load_settings
from db import repo


def _stdout_utf8() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass


def main() -> int:
    _stdout_utf8()
    parser = argparse.ArgumentParser(
        prog="scripts.prune_sessions",
        description="end_reason 이 없는 세션과 그에 딸린 기록을 지운다.",
    )
    parser.add_argument("--yes", action="store_true", help="실제로 지운다")
    parser.add_argument("--dry-run", action="store_true", help="목록만 보여준다(기본)")
    args = parser.parse_args()

    settings = load_settings()
    if not settings.db_path.exists():
        raise SystemExit(f"DB 가 없다: {settings.db_path}")

    conn = repo.connect(settings.db_path)
    targets = repo.unfinished_sessions(conn)
    if not targets:
        print("끝나지 않은 세션이 없다.")
        return 0

    from_sweep = [t for t in targets if t["from_sweep"]]
    zero_turn = [t for t in targets if t["turn_index"] == 0]
    print(f"끝나지 않은 세션 {len(targets)}개")
    print(f"  발화 없이 버려진 것 {len(zero_turn)}개 · 시뮬레이션이 남긴 것 {len(from_sweep)}개")
    for target in targets:
        tag = "스윕" if target["from_sweep"] else "콘솔"
        print(
            f"  {target['session_id'][:8]} 턴{target['turn_index']:<2}"
            f" {target['started_at'][:16].replace('T', ' ')} {tag} {target['scenario_id']}"
        )

    if from_sweep:
        print()
        print("!! 시뮬레이션 회차에 묶인 세션이 있다. 지우면 그 회차의 집계가 바뀐다.")

    if not args.yes:
        print()
        print("지우지 않았다. 실제로 지우려면 --yes 를 붙여라.")
        return 0

    removed = repo.delete_sessions(conn, [t["session_id"] for t in targets])
    print()
    print(f"세션 {removed['sessions']}개, 턴 {removed['turns']}개를 지웠다.")
    left = repo.unfinished_sessions(conn)
    print(f"남은 미완 세션: {len(left)}개")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
