"""D2 lane의 컨테이너 operation 목록이 **한 곳에서만** 선언되는지 본다.

`assert_container_residue_zero`는 실행이 끝난 뒤 결정론적 컨테이너 이름이 남아 있지
않은지 확인한다. 그런데 그 이름을 만들려면 operation 목록이 필요했고, 종전에는 그
목록이 호출부(`run_helper`/`run_executor`/`run_cursor_probe`)와 잔여물 루프 **두 곳에**
리터럴로 있었다. 새 operation을 호출부에만 더하면 잔여물 확인이 그 이름을 조용히
건너뛴다 — 컨테이너가 남아도 lane이 green이다.

곧 실제로 그럴 예정이었다: `T-VN-D2-API-AUDIT`가 helper의 `api-audit`/`purge` 경로를
살리면 `helper-api-audit`·`helper-purge`가 생기는데 잔여물 루프는 그 둘을 모른다.

이제 목록은 `LANE_OPERATIONS` 하나이고 `run_supervisor`가 실행 순간에 등록 여부를
확인한다(런타임 결박). 이 게이트는 그 결박이 유지되는지, 그리고 호출부가 만드는
operation이 전부 선언에 있는지를 **정적으로** 본다 — lane 실행 한 번을 치르기 전에
알아야 하기 때문이다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _ROOT / "scripts" / "run-admin-feature-live-acceptance.sh"

_DECLARATION = re.compile(
    r"^readonly LANE_OPERATIONS=\(\n(?P<body>.*?)^\)$", re.MULTILINE | re.DOTALL
)
#: `run_helper cleanup "$RUNTIME_DIR/direct-cleanup.json"` → `helper-cleanup`
_HELPER_CALL = re.compile(r"^\s*run_helper\s+(?P<action>[a-z][a-z0-9-]*)\b", re.MULTILINE)
#: `run_executor executor-main "$RUNTIME_DIR/playwright-main" 0`
_EXECUTOR_CALL = re.compile(
    r"^\s*run_executor\s+(?P<operation>[a-z][a-z0-9-]*)\b", re.MULTILINE
)
#: `run_supervisor probe probe-cursor-missing \`
_SUPERVISOR_CALL = re.compile(
    r"^\s*run_supervisor\s+[a-z]+\s+(?P<operation>[a-z][a-z0-9-]*)\b", re.MULTILINE
)


def _runner_source() -> str:
    return _RUNNER.read_text(encoding="utf-8")


def _declared_operations() -> list[str]:
    match = _DECLARATION.search(_runner_source())
    assert match is not None, (
        "`readonly LANE_OPERATIONS=(...)` 선언을 찾지 못했다 — 목록이 다시 흩어졌거나 "
        "이 게이트의 파서가 낡았다. 어느 쪽이든 잔여물 확인의 범위를 다시 판단하라."
    )
    return [
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _invoked_operations() -> set[str]:
    """호출부가 실제로 만드는 operation 이름을 유도한다.

    `run_helper <action>`은 supervisor에 `helper-<action>`으로 넘어간다(호출부 본문이
    `"helper-$action"`을 만든다). 나머지 둘은 이름을 그대로 넘긴다.
    """

    source = _runner_source()
    operations = {f"helper-{match.group('action')}" for match in _HELPER_CALL.finditer(source)}
    operations |= {match.group("operation") for match in _EXECUTOR_CALL.finditer(source)}
    operations |= {
        match.group("operation")
        for match in _SUPERVISOR_CALL.finditer(source)
        # `run_supervisor helper "helper-$action"`처럼 변수를 끼운 호출은 리터럴이
        # 아니므로 여기 걸리지 않는다 — 그 경로는 위 `run_helper` 유도가 덮는다.
    }
    return operations


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    declared = _declared_operations()
    assert len(declared) >= 5, f"선언된 operation을 {len(declared)}개만 읽었다"
    assert len(declared) == len(set(declared)), f"선언에 중복이 있다: {declared}"
    invoked = _invoked_operations()
    assert len(invoked) >= 5, f"호출부 operation을 {len(invoked)}개만 유도했다 — 파서를 의심하라"


def test_every_invoked_operation_is_declared() -> None:
    """호출부가 만드는 operation이 전부 선언에 있어야 한다."""

    unknown = sorted(_invoked_operations() - set(_declared_operations()))
    assert unknown == [], (
        f"호출부가 선언에 없는 operation을 만든다: {unknown}. "
        "`assert_container_residue_zero`가 그 이름의 컨테이너를 **확인하지 않으므로** "
        "잔여물이 남아도 lane이 green이 된다. `LANE_OPERATIONS`에 더해라."
    )


def test_the_residue_loop_reads_the_declaration() -> None:
    """잔여물 루프가 리터럴이 아니라 선언을 읽어야 한다."""

    source = _runner_source()
    start = source.index("assert_container_residue_zero() {")
    body = source[start : source.index("\n}", start)]
    assert '"${LANE_OPERATIONS[@]}"' in body, (
        "잔여물 루프가 `LANE_OPERATIONS`를 읽지 않는다 — 목록이 다시 이중 선언됐다. "
        "호출부에만 더한 operation을 이 확인이 건너뛰게 된다."
    )


def test_the_launcher_binds_the_declaration_at_runtime() -> None:
    """단일 funnel이 등록 여부를 실행 순간에 확인해야 한다.

    정적 유도는 리터럴 호출만 본다. `run_helper "$action"`처럼 변수로 오는 경로는
    런타임 확인이 있어야 잡힌다.
    """

    source = _runner_source()
    start = source.index("run_supervisor() {")
    body = source[start : source.index("\n}", start)]
    assert 'assert_registered_operation "$operation"' in body, (
        "`run_supervisor`가 operation 등록을 확인하지 않는다 — 변수로 들어오는 "
        "operation이 선언 밖이어도 컨테이너가 만들어진다."
    )
    guard_start = source.index("assert_registered_operation() {")
    guard = source[guard_start : source.index("\n}", guard_start)]
    assert "die " in guard, "등록 확인이 실패해도 죽지 않는다 — 확인이 아니라 장식이다"
