"""D2 executor가 넘기는 env를 **live config가 요구하는 것**에 결박한다.

`playwright.live.config.ts`의 `isolatedAuthRequestHeaders()`는 acceptance run ID가
있으면 격리 선언을 요구하고, 없으면 config 평가 자체를 throw로 끝낸다. 그런데 D2
lane의 supervisor는 그 선언을 **하지 않으면서** run ID는 항상 넘겼다. 결과는
구조적 통과 불가였다 — 2026-09-05 실행에서 executor 두 개가 3초 만에 exit 1로
죽었고, executor 경로는 로그를 거두지 않아 빈 디렉터리와 exit code만 남았다.
원인을 알려면 배포 스택에서 컨테이너를 손으로 재현해야 했다.

한쪽은 TypeScript, 한쪽은 Python이라 단일 빌드가 유도할 수 없다. 그래서 이 게이트가
**config 소스에서 요구 env를 유도**하고 supervisor 소스에 그것이 있는지 본다. config가
새 요구를 더하면 여기서 깨진다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_CONFIG = (
    _ROOT
    / "packages"
    / "kor-travel-map-admin"
    / "frontend"
    / "playwright.live.config.ts"
)
_SUPERVISOR = _ROOT / "scripts" / "admin_feature_live_supervisor.py"

#: `const ISOLATED_EVIDENCE_ENV = "E2E_ISOLATED_LIVE_EVIDENCE";`
_ENV_CONST = re.compile(
    r'const\s+(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*"(?P<value>E2E_[A-Z0-9_]+)"'
)
#: `const isolatedEvidence = isolatedEvidenceRaw === "1";`
_FLAG_SOURCE = re.compile(
    r'const\s+(?P<flag>[a-zA-Z][a-zA-Z0-9]*)Raw\s*=\s*process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]'
)


def _config_source() -> str:
    return _CONFIG.read_text(encoding="utf-8")


def _env_constants() -> dict[str, str]:
    return {
        match.group("name"): match.group("value")
        for match in _ENV_CONST.finditer(_config_source())
    }


def _flag_to_env() -> dict[str, str]:
    """`isolatedEvidence` 같은 플래그 이름 → 그것이 읽는 env 이름."""

    constants = _env_constants()
    mapping: dict[str, str] = {}
    for match in _FLAG_SOURCE.finditer(_config_source()):
        value = constants.get(match.group("const"))
        if value is not None:
            mapping[match.group("flag")] = value
    return mapping


def _guard_body() -> str:
    source = _config_source()
    start = source.index("function isolatedAuthRequestHeaders(")
    return source[start : source.index("\n}", start)]


def _required_envs() -> set[str]:
    """가드가 실제로 요구하는 env 이름을 유도한다.

    세 형태를 모두 본다: 모듈 스코프 플래그(`!isolatedEvidence`), 가드 안에서
    직접 읽는 `process.env[CONST]`, 그리고 run ID 상수. 하나라도 빠뜨리면 supervisor가
    선언하지 않아도 조용히 통과한다.
    """

    body = _guard_body()
    constants = _env_constants()
    required = {
        env
        for flag, env in _flag_to_env().items()
        if re.search(r"!\s*" + re.escape(flag) + r"\b", body)
    }
    for match in re.finditer(r"process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]", body):
        value = constants.get(match.group("const"))
        if value is not None:
            required.add(value)
    for name, value in constants.items():
        if re.search(r"\b" + re.escape(name) + r"\b", body) and "RUN_ID" in name:
            required.add(value)
    return required


def _executor_source() -> str:
    source = _SUPERVISOR.read_text(encoding="utf-8")
    start = source.index("    def executor(self)")
    return source[start : source.index("\n    def ", start + 10)]


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    constants = _env_constants()
    assert len(constants) >= 3, f"config에서 env 상수를 {len(constants)}개만 읽었다"
    flags = _flag_to_env()
    assert flags, "config의 격리 플래그를 하나도 유도하지 못했다"
    required = _required_envs()
    assert required, "가드가 요구하는 env를 하나도 유도하지 못했다 — 파서를 의심하라"
    assert "--env" in _executor_source(), "supervisor의 executor env 목록을 찾지 못했다"


#: `"--env",\n    "NAME=VALUE",` 또는 `"--env",\n    "NAME",`(호스트 env 전달)
_ENV_ARGUMENT = re.compile(
    r'"--env",\s*\n?\s*(?:f")?"?(?P<name>E2E_[A-Z0-9_]+)(?:=(?P<value>[^"]*))?"'
)


def _executor_env_arguments() -> dict[str, str | None]:
    """executor가 실제로 `--env`로 넘기는 이름 → 값(전달만이면 None).

    단순 부분문자열로 보면 안 된다 — 주석에 이름이 적혀 있거나 `=0`으로 바뀌어도
    통과한다. 실제로 2026-09-06 적대 리뷰가 두 변이(`=1`을 `=0`으로, `--env` 쌍을
    지우고 주석만 남기기)를 **green으로 측정**했다.
    """

    arguments: dict[str, str | None] = {}
    for match in _ENV_ARGUMENT.finditer(_executor_source()):
        arguments[match.group("name")] = match.group("value")
    return arguments


def _guard_required_values() -> dict[str, str]:
    """가드가 값까지 비교하는 env → 요구 값."""

    body = _guard_body()
    constants = _env_constants()
    required: dict[str, str] = {}
    for match in re.finditer(
        r"process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]\s*!==\s*\"(?P<value>[^\"]+)\"", body
    ):
        name = constants.get(match.group("const"))
        if name is not None:
            required[name] = match.group("value")
    return required


def test_executor_declares_every_env_the_guard_requires() -> None:
    """가드가 요구하는 격리 선언을 executor가 **`--env` 인자로** 넘겨야 한다."""

    passed = _executor_env_arguments()
    missing = sorted(env for env in _required_envs() if env not in passed)
    assert missing == [], (
        f"live config의 acceptance 가드가 요구하는 env를 executor가 `--env`로 넘기지 "
        f"않는다: {missing}. 넘기지 않으면 Playwright가 **config 평가에서** 죽고, "
        "선언이 사실이 아니라면 config 쪽 요구를 다시 판단하라 — 거짓 선언으로 "
        "통과시키지 마라. (주석에 이름만 적는 것은 넘긴 것이 아니다.)"
    )


def test_executor_passes_the_value_the_guard_compares() -> None:
    """가드가 값을 비교하면 executor가 **그 값**을 넘겨야 한다.

    이름만 보면 `=1`을 `=0`으로 바꿔도 통과한다 — 적대 리뷰가 실측했다.
    """

    passed = _executor_env_arguments()
    wrong = {
        name: passed.get(name)
        for name, want in _guard_required_values().items()
        if name in passed and passed[name] != want
    }
    assert wrong == {}, (
        f"executor가 가드의 비교값과 다른 값을 넘긴다: {wrong}. 가드는 값을 비교하므로 "
        "이름만 맞추면 config 평가에서 죽는다."
    )


def test_the_guard_does_not_require_a_topology_this_lane_cannot_have() -> None:
    """가드가 prod lane이 만족할 수 없는 topology 선언을 요구하지 않아야 한다.

    `E2E_ISOLATED_LIVE_EVIDENCE`는 `assertNotProdUnlessOptedIn`에서 `isLocalHost`
    대상을 요구한다. D2 lane은 공개 HTTPS prod origin을 쓰므로 그 선언은 **거짓**이고,
    거짓으로 통과시키는 대신 가드가 실제로 필요한 것만 요구해야 한다. 2026-09-05에
    이 결박이 executor를 구조적으로 통과 불가로 만들었다.
    """

    required = _required_envs()
    forbidden = sorted(
        env
        for env in ("E2E_ISOLATED_LIVE_EVIDENCE", "E2E_ISOLATED_LIVE_DOCKER_NETWORK")
        if env in required
    )
    assert forbidden == [], (
        f"acceptance 감사 마커 가드가 topology 선언을 요구한다: {forbidden}. "
        "그 선언은 localhost 대상에서만 참이라 prod lane이 만족할 수 없다 — "
        "거짓 선언으로 통과시키지 말고 가드가 실제로 필요한 것만 요구하게 하라."
    )
    assert "E2E_ADMIN_FEATURE_ACCEPTANCE_WRITE" in required, (
        "가드가 acceptance write opt-in을 더 이상 요구하지 않는다. 그러면 아무 live "
        "실행이나 감사 마커를 붙일 수 있고 cleanup·audit의 소유 회계가 오염된다."
    )
