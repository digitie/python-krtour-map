"""M01 live gate가 보내는 create body를 **API 요청 모델에 결박**한다.

이 게이트 스크립트는 `POST /v1/admin/features`를 잘못된 자격으로 불러 403을 확인한다.
그래서 body는 거부되기만 하면 되는 것처럼 보이지만, **아니다** — body 검증(422)이
자격 검증(403)보다 먼저 돌면 게이트는 "자격 때문에 거부됐다"고 말할 근거를 잃는다.
그러면 통과가 근거가 되지 못한다.

초판의 `CREATE_BODY`는 `lifecycle_state`·`publication_state`·`quality_state`를 담고
있었다 — `AdminFeatureCreateRequest`에 없고 base가 `extra="forbid"`인 바로 그 셋이다.
D2 스펙이 같은 이유로 422에 걸렸고(2026-09-05 실측), 적대 리뷰가 이 스크립트에도 같은
결함이 복사돼 있음을 잡았다(2026-09-06).

`tests/lint/test_d2_create_body_matches_the_api_contract.py`가 스펙 쪽을 맡고, 이 파일이
스크립트 쪽을 맡는다 — 같은 모델을 상대로 두 소비자가 있으므로 둘 다 결박한다
(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_ROUTER = (
    _ROOT
    / "packages"
    / "kor-travel-map-api"
    / "src"
    / "kortravelmap"
    / "api"
    / "routers"
    / "admin_features.py"
)
_GATE = _ROOT / "scripts" / "m01_activation_live_gate.py"

_FIELD = re.compile(r"^    (?P<name>[a-z][a-z0-9_]*)\s*:\s*[^=\n]", re.MULTILINE)
_STATE_AXES = frozenset({"lifecycle_state", "publication_state", "quality_state"})


def _class_body(source: str, name: str) -> str:
    start = source.index(f"\nclass {name}(")
    rest = source[start + 1 :]
    return rest[: rest.index("\nclass ", 1)]


def _create_request_fields() -> set[str]:
    source = _ROUTER.read_text(encoding="utf-8")
    fields: set[str] = set()
    for name in ("AdminFeatureCreateRequest", "AdminFeatureBaseMutation"):
        fields |= {
            match.group("name") for match in _FIELD.finditer(_class_body(source, name))
        }
    return fields


def _gate_create_body_keys() -> set[str]:
    """`CREATE_BODY = {...}` 의 키를 AST로 뽑는다."""

    tree = ast.parse(_GATE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "CREATE_BODY" not in targets or not isinstance(node.value, ast.Dict):
            continue
        return {
            key.value
            for key in node.value.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
    raise AssertionError("`CREATE_BODY`를 찾지 못했다 — 이 게이트가 공허해졌다")


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    fields = _create_request_fields()
    assert len(fields) >= 15, f"모델 필드를 {len(fields)}개만 읽었다 — 파서를 의심하라"
    keys = _gate_create_body_keys()
    assert len(keys) >= 5, f"게이트 body 키를 {len(keys)}개만 읽었다 — 파서를 의심하라"
    body = _class_body(_ROUTER.read_text(encoding="utf-8"), "AdminFeatureBaseMutation")
    assert 'extra="forbid"' in body, (
        "create body 모델이 더 이상 extra를 거부하지 않는다 — 이 게이트를 다시 판단하라"
    )


def test_every_gate_body_key_exists_in_the_request_model() -> None:
    """게이트가 보내는 키가 전부 모델에 있어야 한다."""

    unknown = sorted(_gate_create_body_keys() - _create_request_fields())
    assert unknown == [], (
        f"live gate의 create body가 모델에 없는 키를 보낸다: {unknown}. 모델은 "
        '`extra="forbid"`라 요청이 422가 되고, 그러면 게이트가 관측하는 것은 '
        "**자격 거부가 아니라 body 거부**다 — 통과가 근거를 잃는다."
    )


def test_the_state_axes_stay_out_of_the_gate_body() -> None:
    """이 게이트를 만들게 한 실제 사례가 계속 덮이는지 본다."""

    sent = sorted(_STATE_AXES & _gate_create_body_keys())
    assert sent == [], (
        f"live gate body가 state 축을 보낸다: {sent}. 초기 tuple은 DB wrapper가 "
        "정하며 모델은 그 필드를 받지 않는다."
    )
