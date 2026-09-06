"""helper가 내는 evidence 키를 **검증기의 exact 집합에 결박**한다.

`_validate_direct`는 `direct-*.json`의 키 집합을 exact로 요구한다. helper가 키를 하나
더 내면 성공한 evidence가 `direct evidence mismatch`로 거절된다.

2026-09-06에 정확히 그랬다 — `seed`가 `summary_run_ids`를 내는데 검증기의 집합에 없어
D2가 **스펙을 통과한 직후** 죽었다. 그 검증은 `run_new`에서 스펙 통과 뒤에만 돌기
때문에, D2가 통과한 적이 없는 동안에는 구조적으로 드러날 수 없었다.

이 게이트는 helper의 결과 조립부에서 action별 추가 키를 **유도**해 검증기의
`_DIRECT_EXTRA_KEYS`와 대조한다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = _ROOT / "scripts" / "admin_feature_live_fixture.py"
_STATE = _ROOT / "scripts" / "admin_feature_live_state.py"

#: 검증기가 모든 action에 요구하는 공통 키.
_BASE_KEYS = frozenset(
    {
        "action",
        "counts",
        "foreign_key_constraints_checked",
        "foreign_key_references",
        "version",
    }
)


def _helper_extra_keys() -> dict[str, set[str]]:
    """`if action == "<x>": result["<key>"] = ...` 를 AST로 유도한다."""

    tree = ast.parse(_HELPER.read_text(encoding="utf-8"))
    found: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Name)
            and test.left.id == "action"
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
            and isinstance(test.comparators[0], ast.Constant)
            and isinstance(test.comparators[0].value, str)
        ):
            continue
        action = test.comparators[0].value
        for child in ast.walk(node):
            if not isinstance(child, ast.Assign):
                continue
            for target in child.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Name)
                    and target.value.id == "result"
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)
                ):
                    found.setdefault(action, set()).add(target.slice.value)
    return found


def _validator_extra_keys() -> dict[str, set[str]]:
    """`_DIRECT_EXTRA_KEYS` 선언을 읽는다."""

    source = _STATE.read_text(encoding="utf-8")
    match = re.search(
        r"_DIRECT_EXTRA_KEYS:\s*Final\[dict\[str,\s*frozenset\[str\]\]\]\s*=\s*\{"
        r"(?P<body>.*?)\n\}",
        source,
        re.DOTALL,
    )
    assert match is not None, "`_DIRECT_EXTRA_KEYS`를 찾지 못했다 — 이 게이트가 공허해졌다"
    found: dict[str, set[str]] = {}
    for entry in re.finditer(
        r'"(?P<action>[a-z-]+)":\s*frozenset\(\{(?P<keys>[^}]*)\}\)', match.group("body")
    ):
        found[entry.group("action")] = set(re.findall(r'"([^"]+)"', entry.group("keys")))
    return found


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    helper = _helper_extra_keys()
    assert helper, "helper의 action별 추가 키를 하나도 유도하지 못했다 — 파서를 의심하라"
    assert "seed" in helper, f"seed의 추가 키를 찾지 못했다. 찾은 것={sorted(helper)}"
    assert _validator_extra_keys(), "검증기의 추가 키 선언이 비었다"


def test_every_helper_extra_key_is_accepted_by_the_validator() -> None:
    """helper가 내는 추가 키를 검증기가 전부 받아야 한다."""

    helper = _helper_extra_keys()
    validator = _validator_extra_keys()
    missing = {
        action: sorted(keys - validator.get(action, set()))
        for action, keys in helper.items()
        if keys - validator.get(action, set())
    }
    assert missing == {}, (
        f"helper가 내는 evidence 키를 검증기가 모른다: {missing}. `_validate_direct`는 "
        "키 집합을 **exact**로 요구하므로 성공한 evidence가 `direct evidence mismatch`로 "
        "거절된다 — 그리고 그 검증은 스펙이 통과한 뒤에만 돌기 때문에 실패할 때는 "
        "보이지 않는다."
    )


def test_the_validator_does_not_accept_keys_the_helper_never_emits() -> None:
    """반대로 검증기가 없는 키를 받아 주면 계약이 느슨해진다."""

    helper = _helper_extra_keys()
    extra = {
        action: sorted(keys - helper.get(action, set()))
        for action, keys in _validator_extra_keys().items()
        if keys - helper.get(action, set())
    }
    assert extra == {}, (
        f"검증기가 helper가 내지 않는 키를 허용한다: {extra}. exact 집합의 의미가 "
        "사라지므로 선언을 지우거나 helper를 맞춰라."
    )


def test_the_key_that_broke_this_is_covered() -> None:
    """이 게이트를 만들게 한 실제 사례가 계속 덮이는지 본다."""

    assert "summary_run_ids" in _validator_extra_keys().get("seed", set()), (
        "seed의 `summary_run_ids`를 검증기가 다시 모른다 — D2가 스펙 통과 직후 "
        "`direct evidence mismatch`로 죽는다."
    )
    assert "summary_run_ids" not in _BASE_KEYS, (
        "공통 키에 들어갔다면 cleanup/audit도 그 키를 내야 한다 — 내지 않는다."
    )
