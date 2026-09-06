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
_STATE = _ROOT / "scripts" / "admin_feature_live_state.py"
_SUPERVISOR = _ROOT / "scripts" / "admin_feature_live_supervisor.py"

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


#: `run_helper cleanup "$RUNTIME_DIR/direct-cleanup.json"`
_HELPER_OUTPUT = re.compile(
    r'^\s*run_helper\s+[a-z][a-z0-9-]*\s+"\$RUNTIME_DIR/(?P<name>[A-Za-z0-9._-]+)"',
    re.MULTILINE,
)
#: `run_executor executor-main "$RUNTIME_DIR/playwright-main" 0`
_EXECUTOR_OUTPUT = re.compile(
    r'^\s*run_executor\s+[a-z][a-z0-9-]*\s+"\$RUNTIME_DIR/(?P<name>[A-Za-z0-9._-]+)"',
    re.MULTILINE,
)
#: `--output "$RUNTIME_DIR/cursor-probe.json"`
_PROBE_OUTPUT = re.compile(
    r'^\s*--output\s+"\$RUNTIME_DIR/(?P<name>[A-Za-z0-9._-]+)"', re.MULTILINE
)
#: 검증기의 normal/recovery 파일 집합 리터럴
_EXPECTED_BLOCK = re.compile(
    r"expected_names = \{(?P<body>.*?)\n        \}", re.DOTALL
)
_EXPECTED_ENTRY = re.compile(r'"(?P<name>[A-Za-z0-9._-]+)"')


def _state_source() -> str:
    return _STATE.read_text(encoding="utf-8")


#: `_write_root_only_file(f"{self.args.output}.stderr", log.stderr)`
_STDERR_SIBLING = re.compile(
    r'_write_root_only_file\(\s*f"\{self\.args\.output\}(?P<suffix>[A-Za-z0-9._-]+)"'
)


def _supervisor_stderr_suffix() -> str:
    """supervisor가 helper 출력 옆에 붙이는 접미사를 **생산자에서** 읽는다.

    검증기 상수(`_HELPER_STDERR_SUFFIX`)를 읽으면 두 소비자를 서로 대조하는 꼴이라
    둘이 같이 틀려도 green이다. 파일을 실제로 만드는 쪽에서 유도한다.
    """

    match = _STDERR_SIBLING.search(_SUPERVISOR.read_text(encoding="utf-8"))
    assert match is not None, (
        "supervisor의 helper stderr sibling 기록을 찾지 못했다 — 이름 규칙이 바뀌었으면 "
        "evidence 파일 집합 계약도 함께 다시 판단하라."
    )
    return match.group("suffix")


def _validator_expected_names() -> list[set[str]]:
    """검증기가 요구하는 파일 집합 둘(normal, recovery)을 소스에서 읽는다.

    `+ _HELPER_STDERR_SUFFIX`로 이어 붙인 항목은 리터럴에 접미사가 없으므로 여기서
    다시 붙인다 — 검증기가 상수를 쓰는 것과 같은 규칙이다.
    """

    source = _state_source()
    suffix = re.search(
        r'_HELPER_STDERR_SUFFIX:\s*Final\[str\]\s*=\s*"(?P<value>[^"]+)"', source
    )
    assert suffix is not None, "`_HELPER_STDERR_SUFFIX` 상수를 찾지 못했다"
    sets: list[set[str]] = []
    for match in _EXPECTED_BLOCK.finditer(source):
        names: set[str] = set()
        for line in match.group("body").splitlines():
            entry = _EXPECTED_ENTRY.search(line)
            if entry is None:
                continue
            name = entry.group("name")
            if "_HELPER_STDERR_SUFFIX" in line:
                name += suffix.group("value")
            names.add(name)
        sets.append(names)
    return sets


def _runner_produced_names() -> set[str]:
    """정상 실행이 `$RUNTIME_DIR` 최상위에 남기는 이름을 호출부에서 유도한다.

    helper 출력은 supervisor가 stderr sibling을 **무조건** 함께 쓴다(2026-09-05에
    그것이 없어서 helper 실패 원인이 0바이트로 사라졌다). executor는 아티팩트
    디렉터리를, probe는 파일 하나를 남긴다. 여기에 `lifecycle`이 더해진다.
    """

    source = _runner_source()
    suffix = _supervisor_stderr_suffix()
    names = {"lifecycle"}
    for match in _HELPER_OUTPUT.finditer(source):
        names.add(match.group("name"))
        names.add(match.group("name") + suffix)
    names |= {match.group("name") for match in _EXECUTOR_OUTPUT.finditer(source)}
    names |= {match.group("name") for match in _PROBE_OUTPUT.finditer(source)}
    return names


def test_the_evidence_file_set_matches_what_the_runner_produces() -> None:
    """검증기의 normal 파일 집합이 호출부 유도와 **정확히** 같아야 한다.

    이 대조가 없어서 값을 크게 치렀다. `.stderr` 셋과 `executor.log`가 계약에서 빠져
    있었고, 그 사실은 **스펙이 통과한 뒤에야** 도는 `_validate_evidence`에서만 드러난다.
    즉 배포 스택 실행 한 번을 통째로 치르고서야 파일 이름 하나를 알게 된다.
    """

    produced = _runner_produced_names()
    assert len(produced) >= 8, f"호출부에서 산출물 이름을 {len(produced)}개만 유도했다"
    sets = _validator_expected_names()
    assert len(sets) == 2, f"검증기의 파일 집합을 {len(sets)}개 읽었다 — 둘이어야 한다"
    normal, recovery = sets
    assert normal == produced, (
        f"검증기의 normal 파일 집합과 호출부 유도가 다르다.\n"
        f"  검증기에만: {sorted(normal - produced)}\n"
        f"  호출부에만: {sorted(produced - normal)}\n"
        "이 불일치는 스펙이 통과한 뒤 evidence 검증에서만 드러난다 — 배포 스택 실행 "
        "한 번을 치르고 나서야 알게 된다. 여기서 맞춰라."
    )
    assert recovery < normal, (
        f"recovery 파일 집합이 normal의 진부분집합이 아니다: {sorted(recovery - normal)}. "
        "recovery는 seed와 probe를 하지 않으므로 더 적어야 한다."
    )


def test_the_validator_requires_every_declared_operation() -> None:
    """검증기의 normal `required_operations`가 선언과 같아야 한다.

    lifecycle 파일 이름이 그 집합에서 만들어지고 실제 디렉터리와 exact 대조된다.
    선언에 operation을 더하고 여기를 안 고치면 lane은 **evidence 단계에서** 죽는다 —
    또 한 번의 배포 스택 실행이다.
    """

    match = re.search(
        r"required_operations = \{(?P<body>[^}]*)\}", _state_source(), re.DOTALL
    )
    assert match is not None, "검증기의 `required_operations`를 찾지 못했다"
    required = set(_EXPECTED_ENTRY.findall(match.group("body")))
    declared = set(_declared_operations())
    assert required == declared, (
        f"검증기의 normal `required_operations`와 러너 선언이 다르다.\n"
        f"  검증기에만: {sorted(required - declared)}\n"
        f"  선언에만: {sorted(declared - required)}"
    )
