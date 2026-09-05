"""D2 acceptance 스펙의 create body를 **API 요청 모델에 결박**한다.

`POST /v1/admin/features`의 body는 `AdminFeatureCreateRequest`이고 그 base는
`model_config = ConfigDict(extra="forbid")`다. 즉 모델에 없는 키를 보내면 422다.

D2 스펙은 `lifecycle_state`·`publication_state`·`quality_state` 셋을 보내고 있었다.
그 셋은 모델에 **없다** — 초기 tuple은 DB wrapper
`create_admin_manual_feature_with_initial_state`가 `active`/`published`/`valid`로 정한다.
스펙이 계약에 없는 것을 보내고 있었고, D2가 그 지점까지 온 적이 없어 아무도 몰랐다.
2026-09-05 실측: 셋 포함 → 422(정확히 그 세 필드), 제거 → 201.

한쪽은 TypeScript, 한쪽은 Python이라 단일 빌드가 유도할 수 없다. 그래서 이 게이트가
모델의 필드 집합을 **유도**해 스펙 body의 키와 대조한다(AGENTS.md DO NOT 15).
"""

from __future__ import annotations

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
_SPEC = (
    _ROOT
    / "packages"
    / "kor-travel-map-admin"
    / "frontend"
    / "e2e"
    / "live"
    / "admin-feature-acceptance-write.live.spec.ts"
)

#: `    name: str | None = Field(...)` / `    kind: Literal["place", "event"]`
_FIELD = re.compile(r"^    (?P<name>[a-z][a-z0-9_]*)\s*:\s*[^=\n]", re.MULTILINE)


def _class_body(source: str, name: str) -> str:
    start = source.index(f"\nclass {name}(")
    rest = source[start + 1 :]
    end = rest.index("\nclass ", 1)
    return rest[:end]


def _model_fields(name: str) -> set[str]:
    source = _ROUTER.read_text(encoding="utf-8")
    body = _class_body(source, name)
    return {match.group("name") for match in _FIELD.finditer(body)}


def _create_request_fields() -> set[str]:
    """`AdminFeatureCreateRequest` + 상속한 base의 필드 집합."""

    return _model_fields("AdminFeatureCreateRequest") | _model_fields(
        "AdminFeatureBaseMutation"
    )


def _spec_create_body_keys() -> set[str]:
    """스펙이 `POST /v1/admin/features`에 보내는 body 키."""

    spec = _SPEC.read_text(encoding="utf-8")
    anchor = spec.index('"/v1/admin/features",')
    body_start = spec.index("body: {", anchor)
    body_end = spec.index("\n          },", body_start)
    block = spec[body_start:body_end]
    return set(re.findall(r"^\s{12}([a-z][a-z0-9_]*):", block, re.MULTILINE))


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    fields = _create_request_fields()
    assert len(fields) >= 15, f"모델 필드를 {len(fields)}개만 읽었다 — 파서를 의심하라"
    keys = _spec_create_body_keys()
    assert len(keys) >= 5, f"스펙 body 키를 {len(keys)}개만 읽었다 — 파서를 의심하라"
    # 모델이 실제로 `extra="forbid"`여야 이 대조가 의미를 갖는다.
    body = _class_body(_ROUTER.read_text(encoding="utf-8"), "AdminFeatureBaseMutation")
    assert 'extra="forbid"' in body, (
        "create body 모델이 더 이상 extra를 거부하지 않는다 — 이 게이트를 다시 판단하라"
    )


def test_every_spec_create_key_exists_in_the_request_model() -> None:
    """스펙이 보내는 키가 전부 모델에 있어야 한다."""

    unknown = sorted(_spec_create_body_keys() - _create_request_fields())
    assert unknown == [], (
        f"D2 스펙이 `AdminFeatureCreateRequest`에 없는 키를 보낸다: {unknown}. "
        "모델은 `extra=\"forbid\"`라 그 요청은 **422로 죽는다** — 배포 스택 실행 "
        "도중에 알게 두지 마라. 서버가 정하는 값이면 보내지 말고, 정말 필요한 "
        "입력이면 모델에 더해라."
    )


def test_the_state_axes_that_broke_this_stay_out_of_the_create_body() -> None:
    """이 게이트를 만들게 한 실제 사례가 계속 덮이는지 본다.

    초기 tuple은 DB wrapper가 정한다. 스펙은 그것을 **보내는 대신 검증한다.**
    """

    axes = {"lifecycle_state", "publication_state", "quality_state"}
    sent = sorted(axes & _spec_create_body_keys())
    assert sent == [], (
        f"create body가 state 축을 다시 보낸다: {sent}. 초기 tuple은 "
        "`create_admin_manual_feature_with_initial_state`가 정한다."
    )
    assert not (axes & _create_request_fields()), (
        "요청 모델이 state 축을 받기 시작했다 — 초기 tuple의 생산자가 둘이 되면 "
        "이 게이트도 함께 다시 판단하라."
    )
