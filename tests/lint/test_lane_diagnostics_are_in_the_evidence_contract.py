"""lane이 쓰는 **진단 파일**이 evidence exact-file-set 계약 안에 있는지 결박한다.

`admin_feature_live_state.py`의 evidence 검증은 runtime 디렉터리와 executor artifact
디렉터리의 **정확한 파일 집합**을 요구한다. 그래서 진단 파일을 하나 추가하면
성공한 run이 `evidence exact file set mismatch` / `redacted report exact file set
mismatch`로 죽는다.

그런데 그 검증은 **스펙이 통과한 뒤에만** 실행된다(`run_new`가 `test_status != 0`이면
그 전에 die한다). 즉 D2가 통과한 적이 없는 동안에는 이 결함이 구조적으로 드러날 수
없었다. 2026-09-05에 두 진단 파일(`<output>.stderr`, `executor.log`)이 정확히 그렇게
들어왔고, 적대 리뷰가 D2를 처음 통과시키기 직전에 잡았다.

진단은 곁다리가 아니라 **증거**다. 그러니 계약에서 빼는 대신 계약에 넣고, 여기서
양쪽을 대조한다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).

조건부로 쓰면 exact 집합이 흔들리므로 **항상 쓰는지**도 함께 본다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SUPERVISOR = _ROOT / "scripts" / "admin_feature_live_supervisor.py"
_STATE = _ROOT / "scripts" / "admin_feature_live_state.py"


def _supervisor_source() -> str:
    return _SUPERVISOR.read_text(encoding="utf-8")


def _state_source() -> str:
    return _STATE.read_text(encoding="utf-8")


def _written_artifact_basenames() -> set[str]:
    """supervisor가 evidence 디렉터리에 만드는 파일의 basename을 유도한다.

    `_write_root_only_file(...)` 호출의 경로 인자에서 리터럴 조각을 뽑는다.
    """

    tree = ast.parse(_supervisor_source())
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Name) and target.id == "_write_root_only_file"):
            continue
        if not node.args:
            continue
        for child in ast.walk(node.args[0]):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                if child.value.startswith("."):
                    names.add("<output>" + child.value)
                elif "." in child.value and "/" not in child.value:
                    names.add(child.value)
            elif isinstance(child, ast.JoinedStr):
                rendered = "".join(
                    part.value
                    for part in child.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                if rendered.startswith("."):
                    names.add("<output>" + rendered)
    return names


def test_the_gate_finds_the_diagnostic_writes() -> None:
    """대조 대상이 실제로 잡혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    names = _written_artifact_basenames()
    assert len(names) >= 2, (
        f"supervisor가 evidence에 쓰는 파일을 {len(names)}개만 찾았다 — 파서를 "
        f"의심하라. 찾은 것={sorted(names)}"
    )


def test_executor_log_is_in_the_report_contract() -> None:
    """`executor.log`가 `_REPORT_NAMES`에 있어야 한다."""

    written = _written_artifact_basenames()
    if "executor.log" not in written:
        pytest.skip("executor가 더 이상 executor.log를 쓰지 않는다")
    match = re.search(
        r"_REPORT_NAMES:\s*Final\[set\[str\]\]\s*=\s*\{(?P<body>[^}]*)\}",
        _state_source(),
    )
    assert match is not None, "`_REPORT_NAMES`를 찾지 못했다 — 이 게이트가 공허해졌다"
    declared = set(re.findall(r'"([^"]+)"', match.group("body")))
    assert "executor.log" in declared, (
        "supervisor가 `executor.log`를 쓰는데 `_REPORT_NAMES`에 없다. "
        "evidence 검증은 exact 파일 집합을 요구하므로 **성공한 run이** "
        "`redacted report exact file set mismatch`로 죽는다 — 그리고 그 검증은 "
        "스펙이 통과한 뒤에만 돌기 때문에 실패할 때는 보이지 않는다."
    )


def test_helper_stderr_sibling_is_in_the_evidence_contract() -> None:
    """helper stderr sibling이 normal·recover 양쪽 기대 집합에 있어야 한다."""

    written = _written_artifact_basenames()
    if "<output>.stderr" not in written:
        pytest.skip("helper가 더 이상 stderr sibling을 쓰지 않는다")
    state = _state_source()
    for mode, output in (
        ("normal", "direct-seed.json"),
        ("normal", "direct-cleanup.json"),
        ("normal", "direct-audit.json"),
        ("recover", "direct-cleanup.json"),
        ("recover", "direct-audit.json"),
    ):
        assert f'"{output}" + _HELPER_STDERR_SUFFIX' in state, (
            f"{mode} 기대 집합에 `{output}`의 stderr sibling이 없다. helper가 그 "
            "파일을 항상 쓰므로 evidence 검증이 exact 집합에서 걸린다."
        )


def test_diagnostic_writes_are_unconditional() -> None:
    """진단 파일은 **조건 없이** 쓰여야 한다 — 아니면 exact 집합이 흔들린다."""

    source = _supervisor_source()
    assert "if log.stderr:" not in source, (
        "helper stderr를 조건부로 쓴다. stderr 유무에 따라 evidence 파일 집합이 "
        "달라져 성공한 run이 `evidence exact file set mismatch`로 죽는다."
    )
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != "executor":
            continue
        for child in ast.walk(node):
            if (
                isinstance(child, ast.If)
                and "executor.log" in ast.unparse(child)
                and any(
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "_write_root_only_file"
                    for inner in ast.walk(child)
                )
            ):
                raise AssertionError(
                    "`executor.log` 쓰기가 `if` 안에 있다. 조건부면 artifact 파일 "
                    "집합이 흔들려 성공한 run이 exact 집합에서 죽는다."
                )
