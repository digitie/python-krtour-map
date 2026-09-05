"""D2 audit이 세는 domain command를 **registry에 결박**한다.

`_inspect_api_owned`는 receipt의 `operation`과 `response_status`를 대조한다. 두 값의
정본은 `kortravelmap.api.domain_command_registry`이고, 라우트가
`@idempotent_domain_command`로 같은 이름을 쓴다.

종전에는 helper가 `"admin.feature.create"`라고 **손으로 적어** 두었는데 레지스트리의
실제 이름은 `"admin.feature.create.manual-v1"`이다. 그래서 audit은 모든 create receipt를
소유권 위반으로 거절했고, 성공 status도 200으로 굳어 있어 201을 내는 create를 또 거절했다.
이 lane이 `api-audit`/`purge`를 아직 부르지 않아 잠복해 있었을 뿐이다(2026-09-06 적대
리뷰 적발).

이제 helper가 `command_policy(...)`로 유도한다. 이 게이트는 그 유도가 **살아 있는지**와
값이 레지스트리와 같은지를 본다 — 다시 리터럴로 굳으면 여기서 깨진다
(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kortravelmap.api.domain_command_registry import command_policy

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_HELPER = _ROOT / "scripts" / "admin_feature_live_fixture.py"

_CREATE_ROUTE = ("POST", "/v1/admin/features")
_STATE_ROUTE = ("PATCH", "/v1/admin/features/{feature_id}/state")


def _helper_source() -> str:
    return _HELPER.read_text(encoding="utf-8")


def test_the_helper_derives_the_operation_names_from_the_registry() -> None:
    """helper가 이름을 리터럴로 적지 않고 `command_policy`로 유도해야 한다."""

    source = _helper_source()
    assert "from kortravelmap.api.domain_command_registry import command_policy" in source, (
        "helper가 registry를 import하지 않는다 — 이름이 다시 리터럴로 굳으면 audit이 "
        "모든 receipt를 소유권 위반으로 거절한다."
    )
    for method, path in (_CREATE_ROUTE, _STATE_ROUTE):
        # 호출이 줄바꿈될 수 있으므로 공백에 관대해야 한다 — 그렇지 않으면 게이트가
        # 서식 때문에 red가 되고, 그 red는 계약과 무관하다.
        call = re.compile(
            r"command_policy\(\s*" + re.escape(f'"{method}"') + r",\s*"
            + re.escape(f'"{path}"') + r"\s*,?\s*\)"
        )
        assert call.search(source) is not None, (
            f"helper가 {method} {path}의 정책을 유도하지 않는다"
        )
    literal = re.search(
        r'_ADMIN_(?:CREATE|STATE)_OPERATION:\s*Final\[str\]\s*=\s*"', source
    )
    assert literal is None, (
        "operation 이름이 다시 리터럴로 적혔다. registry가 정본이며 이름이 갈리면 "
        "audit이 조용히 모든 receipt를 거절한다."
    )


def test_the_registry_still_declares_what_the_audit_assumes() -> None:
    """감사가 기대하는 형태가 레지스트리에 그대로 있는지 본다."""

    create = command_policy(*_CREATE_ROUTE)
    state = command_policy(*_STATE_ROUTE)
    assert create.operation == "admin.feature.create.manual-v1", (
        f"create operation 이름이 바뀌었다: {create.operation}. helper는 유도하므로 "
        "따라가지만, 이 사실이 바뀌면 acceptance 기대치도 다시 판단하라."
    )
    assert state.operation == "admin.feature.state", (
        f"state operation 이름이 바뀌었다: {state.operation}"
    )
    assert create.success_status == 201, (
        f"create의 성공 status가 {create.success_status}로 바뀌었다 — 스펙의 "
        "`requireBody` 기대값도 함께 다시 판단하라."
    )
    assert state.success_status in (None, 200), (
        f"state의 성공 status가 {state.success_status}로 바뀌었다"
    )


def test_the_audit_compares_status_per_operation() -> None:
    """status 비교가 operation별이어야 한다 — 200으로 굳으면 create를 거절한다."""

    source = _helper_source()
    assert "_ADMIN_EXPECTED_STATUS[operation]" in source, (
        "audit이 receipt status를 operation별로 비교하지 않는다. create는 201, "
        "state는 200이므로 하나로 굳히면 한쪽을 반드시 거절한다."
    )
    assert 'command["response_status"] != 200' not in source, (
        "audit이 다시 200을 고정 비교한다 — create receipt(201)를 거절한다."
    )
