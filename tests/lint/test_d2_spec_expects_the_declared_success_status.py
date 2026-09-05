"""D2 스펙의 성공 status 기대를 **route 선언에 결박**한다.

성공 status는 route마다 다르다. `POST /v1/admin/features`는
`status_code=status.HTTP_201_CREATED`인데, 스펙의 `requireBody`는 200만 성공으로 봤다.
그래서 **201로 성공한 create를 실패로 읽었다** — 2026-09-05 실측:

    Error: create typed Feature 실패: HTTP 201

D2가 그 지점까지 온 적이 없어 아무도 몰랐다. 이 게이트는 route가 선언한 성공 status를
**유도**해 스펙이 그 값을 기대하는지 본다. 한쪽은 Python, 한쪽은 TypeScript라 단일
빌드가 유도할 수 없다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).

## 범위

스펙이 `requireBody`로 성공을 요구하는 호출만 본다. route가 기본 200이면 스펙도 기본값을
쓰면 되고, 201처럼 다른 값이면 스펙이 그 값을 **명시**해야 한다.
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

#: `@router.post(\n    "",\n ... status_code=status.HTTP_201_CREATED,`
_ROUTE = re.compile(
    r"@router\.(?P<method>get|post|patch|put|delete)\(\s*\n\s*\"(?P<path>[^\"]*)\","
    r"(?P<options>.*?)\n\)\n",
    re.DOTALL,
)
_DECLARED_STATUS = re.compile(r"status_code=status\.HTTP_(?P<code>\d{3})_")


def _declared_success_status() -> dict[tuple[str, str], int]:
    """route → 선언한 성공 status. 미선언이면 FastAPI 기본값 200."""

    source = _ROUTER.read_text(encoding="utf-8")
    routes: dict[tuple[str, str], int] = {}
    for match in _ROUTE.finditer(source):
        declared = _DECLARED_STATUS.search(match.group("options"))
        routes[(match.group("method").upper(), match.group("path"))] = (
            int(declared.group("code")) if declared else 200
        )
    return routes


def _spec_source() -> str:
    return _SPEC.read_text(encoding="utf-8")


def test_the_gate_reads_real_routes() -> None:
    """route를 실제로 읽었는지부터 본다 — 비면 아래 단언이 공허하다."""

    routes = _declared_success_status()
    assert len(routes) >= 8, f"route를 {len(routes)}개만 읽었다 — 파서를 의심하라"
    assert ("POST", "") in routes, (
        f"`POST /v1/admin/features`를 찾지 못했다 — 파서를 의심하라. 읽은 것={sorted(routes)}"
    )


def test_the_manual_create_route_still_declares_201() -> None:
    """이 게이트를 만들게 한 route가 여전히 201을 선언하는지 본다."""

    assert _declared_success_status()[("POST", "")] == 201, (
        "manual Feature 생성 route가 더 이상 201을 선언하지 않는다 — 스펙의 기대값도 "
        "함께 다시 판단하라."
    )


def test_the_spec_expects_the_declared_create_status() -> None:
    """스펙의 create 호출이 route가 선언한 status를 기대해야 한다."""

    expected = _declared_success_status()[("POST", "")]
    spec = _spec_source()
    anchor = spec.index('"create typed Feature",')
    tail = spec[anchor : anchor + 200]
    supplied = re.search(r'"create typed Feature",\s*\n\s*(?P<status>\d{3}),', tail)
    assert supplied is not None and int(supplied.group("status")) == expected, (
        f"스펙의 create 호출이 {expected}를 기대하지 않는다. `requireBody`의 기본값은 "
        "200이라 201로 성공한 요청을 **실패로 읽는다** — 배포 스택 실행 도중에 알게 "
        f"두지 마라. 발견={supplied.group('status') if supplied else '(미지정)'}"
    )


def test_require_body_takes_an_expected_status() -> None:
    """`requireBody`가 기대 status를 받을 수 있어야 한다 — 아니면 위 단언이 공허하다."""

    spec = _spec_source()
    assert re.search(
        r"function requireBody<T>\(\s*result: FetchResult<T>,\s*label: string,\s*"
        r"expectedStatus = 200,\s*\)",
        spec,
    ), (
        "`requireBody`가 더 이상 기대 status를 받지 않는다 — 200 고정으로 되돌아가면 "
        "201 route가 다시 실패로 읽힌다."
    )
