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
_SPEC = (
    _ROOT
    / "packages"
    / "kor-travel-map-admin"
    / "frontend"
    / "e2e"
    / "live"
    / "admin-feature-acceptance-write.live.spec.ts"
)

_CREATE_MODEL = "AdminFeatureCreateRequest"


def _router_tree() -> ast.Module:
    return ast.parse(_ROUTER.read_text(encoding="utf-8"))


def _class_nodes() -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in ast.walk(_router_tree())
        if isinstance(node, ast.ClassDef)
    }


def _declared_fields(node: ast.ClassDef) -> set[str]:
    """클래스 **본문에 직접 선언된** annotated field만 센다.

    종전에는 `\\nclass X(` 부터 다음 `\\nclass ` 까지를 텍스트로 잘라 정규식을
    돌렸다. 두 클래스 사이에 있는 **모듈 수준 함수의 지역 주석까지 쓸어 담았고**,
    실제로 `try`가 "모델 필드"로 잡혔다(2026-09-06 적대 리뷰 실측). 그러면 스펙이
    그 이름을 보내도 게이트가 green이 된다. AST로 본문만 본다.
    """

    return {
        statement.target.id
        for statement in node.body
        if isinstance(statement, ast.AnnAssign)
        and isinstance(statement.target, ast.Name)
    }


def _create_request_fields() -> set[str]:
    """`AdminFeatureCreateRequest`와 **선언된 base들**의 필드 집합.

    base 이름을 손으로 적지 않는다 — `class X(Base)`의 base를 따라간다. 종전에는
    `AdminFeatureBaseMutation`을 리터럴로 합쳤는데, create가 base에서 떨어져 나가면
    게이트가 없는 필드를 계속 허용해 **거짓 green**이 된다.
    """

    classes = _class_nodes()
    assert _CREATE_MODEL in classes, f"{_CREATE_MODEL}을 찾지 못했다 — 게이트가 공허해졌다"
    fields: set[str] = set()
    pending = [_CREATE_MODEL]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in classes:
            continue
        seen.add(name)
        node = classes[name]
        fields |= _declared_fields(node)
        pending.extend(
            base.id for base in node.bases if isinstance(base, ast.Name)
        )
    return fields


def _base_model_config_forbids_extra() -> bool:
    """`AdminFeatureCreateRequest` 계열 어딘가가 `extra="forbid"`를 선언하는가."""

    classes = _class_nodes()
    pending = [_CREATE_MODEL]
    seen: set[str] = set()
    while pending:
        name = pending.pop()
        if name in seen or name not in classes:
            continue
        seen.add(name)
        node = classes[name]
        if 'extra="forbid"' in ast.unparse(node).replace("'", '"'):
            return True
        pending.extend(base.id for base in node.bases if isinstance(base, ast.Name))
    return False


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
    # 모델이 실제로 `extra="forbid"`여야 이 대조가 의미를 갖는다. base 이름을 손으로
    # 적지 않고 상속을 따라 찾는다.
    assert _base_model_config_forbids_extra(), (
        "create body 모델 계열이 더 이상 extra를 거부하지 않는다 — 모델에 없는 키를 "
        "보내도 422가 아니게 되므로 이 게이트를 다시 판단하라"
    )
    # 텍스트 슬라이스가 쓸어 담던 비-필드가 사라졌는지 본다.
    assert "try" not in fields, (
        "필드 집합에 `try`가 있다 — 클래스 본문이 아니라 텍스트 구간을 읽고 있다"
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
