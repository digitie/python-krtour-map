"""D2 executor가 넘기는 env를 **live config가 요구하는 것**에 결박한다.

`playwright.live.config.ts`의 `isolatedAuthRequestHeaders()`는 acceptance run ID가
있으면 격리 선언을 요구하고, 없으면 config 평가 자체를 throw로 끝낸다. 그런데 D2
lane의 supervisor는 그 선언을 **하지 않으면서** run ID는 항상 넘겼다. 결과는
구조적 통과 불가였다 — 2026-09-05 실행에서 executor 두 개가 3초 만에 exit 1로
죽었고, executor 경로는 로그를 거두지 않아 빈 디렉터리와 exit code만 남았다.
원인을 알려면 배포 스택에서 컨테이너를 손으로 재현해야 했다.

한쪽은 TypeScript, 한쪽은 Python이라 단일 빌드가 유도할 수 없다. 그래서 이 게이트가
**config 소스에서 요구 env를 유도**하고 supervisor 소스에 그것이 있는지 본다(AGENTS.md
DO NOT 15: 유도 → 결박 → 탐지).

2026-09-06 적대 리뷰가 이 게이트의 과허용 다섯을 **실측**했다. 전부 "게이트가 자기
존재 이유인 실패를 green으로 통과시킨다"였다:

1. `process.env.NAME` **dot 접근**을 유도하지 못했다. config 파일의 다수 스타일이
   그것인데(`E2E_BASE_URL`·`E2E_LIVE_WORKERS`·`E2E_LIVE_ALLOW_PROD`) 가드에 dot 형태
   요구를 더하면 게이트는 조용히 green이었다.
2. 값 비교 유도(`_guard_required_values`)가 **빈 집합이어도 green**이었다. 가드가
   지역 const를 경유하는 형태(이미 `recoveryRaw`가 그렇다)로 바뀌면 값 결박이 통째로
   사라진다.
3. 요구 env가 supervisor의 **조건부 `command.extend`** 안에 있어도 "넘긴다"로 셌다.
4. **config 평가 시 항상 도는 top-level IIFE**(`assertNotProdUnlessOptedIn`)를 아예
   읽지 않았다. executor가 `E2E_LIVE_ALLOW_PROD=1`을 빼도 green이었다.
5. **음(negative) 방향 결박이 없었다.** executor가 config가 거부하는 선언을 더해도
   green이었다 — 2026-09-05 사건의 거울상이다.
"""

from __future__ import annotations

import ast
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

_GUARD_ENTRY = "isolatedAuthRequestHeaders"
#: config 평가 중 **항상** 도는 top-level IIFE. 이것이 throw하면 어떤 spec도 시작하지 못한다.
_EVALUATION_ENTRY = "assertNotProdUnlessOptedIn"

#: `const ISOLATED_EVIDENCE_ENV = "E2E_ISOLATED_LIVE_EVIDENCE";` (따옴표 양쪽 허용 —
#: 이 저장소에는 prettier도 quote 룰도 없어 작은따옴표가 언제든 나올 수 있다)
_ENV_CONST = re.compile(
    r"""const\s+(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*["'](?P<value>E2E_[A-Z0-9_]+)["']"""
)
#: `const isolatedEvidenceRaw = process.env[ISOLATED_EVIDENCE_ENV];`
#: 접미사 `Raw`를 요구하지 않는다 — `const runId = process.env[...]`도 같은 형태다.
_LOCAL_ENV_BINDING = re.compile(
    r"const\s+(?P<local>[a-zA-Z][a-zA-Z0-9_]*)\s*=\s*process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]"
)
#: `const isolatedEvidence = isolatedEvidenceRaw === "1";` — 플래그 파생
_FLAG_SOURCE = re.compile(
    r"const\s+(?P<flag>[a-zA-Z][a-zA-Z0-9]*)Raw\s*=\s*process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]"
)
#: `process.env.E2E_LIVE_ALLOW_PROD`
_ENV_DOT = re.compile(r"process\.env\.(?P<name>E2E_[A-Z0-9_]+)")
#: `process.env[CONST]`
_ENV_BRACKET = re.compile(r"process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]")

#: 세 가지 top-level 함수 형태. 종전에는 `^function name(`만 봤고, arrow-const와 IIFE는
#: 유도에서 통째로 빠졌다(적대 리뷰 실측).
_FUNCTION_FORMS = (
    re.compile(r"^function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE),
    re.compile(
        r"^const\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]*)?=\s*"
        r"(?:async\s+)?(?:\([^)]*\)|[A-Za-z_][A-Za-z0-9_]*)\s*(?::[^=]*)?=>\s*\{",
        re.MULTILINE,
    ),
    re.compile(
        r"^\(function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", re.MULTILINE
    ),
)
_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")


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


def _local_bindings() -> dict[str, str]:
    """`const runId = process.env[X]` 같은 지역 이름 → env 이름."""

    constants = _env_constants()
    mapping: dict[str, str] = {}
    for match in _LOCAL_ENV_BINDING.finditer(_config_source()):
        value = constants.get(match.group("const"))
        if value is not None:
            mapping[match.group("local")] = value
    return mapping


def _optional_envs(body: str) -> set[str]:
    """**있으면 값을 검사하고 없으면 넘어가는** env — 요구가 아니다.

    config는 두 형태를 구분해서 쓴다:

        if (raw !== undefined && raw !== "1") throw     // 선택 — 값만 제한
        if (process.env.X !== "1") throw                // 요구 — 없으면 죽는다

    이 둘을 섞으면 게이트가 baseline에서 거짓 실패한다. 실제로 `RECOVERY_ONLY`와
    두 topology 플래그가 전자다.
    """

    optional: set[str] = set()
    for local, env in {**_local_bindings(), **_flag_to_env()}.items():
        if re.search(re.escape(local) + r"(?:Raw)?\s*[!=]==\s*undefined", body):
            optional.add(env)
    for match in re.finditer(
        r"process\.env\.(?P<name>E2E_[A-Z0-9_]+)\s*[!=]==\s*undefined", body
    ):
        optional.add(match.group("name"))
    constants = _env_constants()
    for match in re.finditer(
        r"process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]\s*[!=]==\s*undefined", body
    ):
        name = constants.get(match.group("const"))
        if name is not None:
            optional.add(name)
    return optional


def _function_bodies() -> dict[str, str]:
    """config 모듈의 최상위 함수 이름 → 본문. 세 형태를 모두 읽는다."""

    source = _config_source()
    bodies: dict[str, str] = {}
    for pattern in _FUNCTION_FORMS:
        for match in pattern.finditer(source):
            start = match.start()
            end = source.find("\n}", start)
            if end == -1:
                continue
            bodies.setdefault(match.group("name"), source[start:end])
    return bodies


def _reachable_body(entry: str) -> str:
    """진입점과 **그것이 부르는 같은 모듈 함수들**의 본문을 합친다.

    요구 하나가 헬퍼로 빠지면 유도 집합이 조용히 줄고 `missing == []`이 공허하게
    통과한다. 호출을 따라가면 그 구멍이 닫힌다.
    """

    bodies = _function_bodies()
    assert entry in bodies, (
        f"config에서 `{entry}`를 찾지 못했다 — 이름이나 선언 형태가 바뀌었으면 이 "
        "게이트도 함께 다시 판단하라. 지금 상태로는 아무것도 유도하지 못한다."
    )
    seen: set[str] = set()
    pending = [entry]
    parts: list[str] = []
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        body = bodies[name]
        parts.append(body)
        pending.extend(
            called
            for called in _CALL.findall(body)
            if called in bodies and called not in seen
        )
    return "\n".join(parts)


def _envs_read_in(body: str) -> set[str]:
    """본문이 읽는 env 이름 — bracket 상수와 **dot 접근**을 모두 본다."""

    constants = _env_constants()
    names = {match.group("name") for match in _ENV_DOT.finditer(body)}
    for match in _ENV_BRACKET.finditer(body):
        value = constants.get(match.group("const"))
        if value is not None:
            names.add(value)
    return names


def _required_envs() -> set[str]:
    """acceptance 마커 가드가 요구하는 env 이름을 유도한다.

    세 형태를 모두 본다: 모듈 스코프 플래그(`!isolatedEvidence`), 가드 안에서 직접
    읽는 `process.env[CONST]`/`process.env.NAME`, 그리고 run ID 상수.
    """

    body = _reachable_body(_GUARD_ENTRY)
    constants = _env_constants()
    optional = _optional_envs(body)
    required = {
        env
        for flag, env in _flag_to_env().items()
        if re.search(r"!\s*" + re.escape(flag) + r"\b", body)
    }
    # 값을 비교하며 **undefined 가드가 없는** 것은 없으면 throw다 = 요구.
    required |= set(_comparison_values(body))
    required -= optional
    # run ID는 config가 아니라 **lane 계약**이 요구한다 — 없으면 config는 조용히
    # 마커를 안 붙이고, 그러면 cleanup·audit의 소유 회계가 통째로 무너진다.
    for name, value in constants.items():
        if re.search(r"\b" + re.escape(name) + r"\b", body) and "RUN_ID" in name:
            required.add(value)
    return required


def _comparison_values(body: str) -> dict[str, str]:
    """본문의 `X !== "V"` 비교를 env 이름 → 요구 값으로 유도한다.

    세 경로를 따라간다: `process.env.NAME !== "V"`, `process.env[CONST] !== "V"`,
    그리고 **지역 const를 경유한** `nameRaw !== "V"`. 마지막이 없으면 가드가
    `const raw = process.env[X]; if (raw !== "1")` 형태로만 바뀌어도 값 결박이
    통째로 사라진다 — 이미 `recoveryRaw`가 그 형태다(적대 리뷰 실측).
    """

    constants = _env_constants()
    values: dict[str, str] = {}
    for match in re.finditer(
        r"process\.env\.(?P<name>E2E_[A-Z0-9_]+)\s*!==\s*[\"'](?P<value>[^\"']+)[\"']",
        body,
    ):
        values[match.group("name")] = match.group("value")
    for match in re.finditer(
        r"process\.env\[(?P<const>[A-Z][A-Z0-9_]*)\]\s*!==\s*[\"'](?P<value>[^\"']+)[\"']",
        body,
    ):
        name = constants.get(match.group("const"))
        if name is not None:
            values[name] = match.group("value")
    for flag, env in _flag_to_env().items():
        match = re.search(
            re.escape(flag) + r"Raw\s*!==\s*[\"'](?P<value>[^\"']+)[\"']", body
        )
        if match is not None:
            values[env] = match.group("value")
    return values


def _guard_required_values() -> dict[str, str]:
    """acceptance 가드가 **없으면 죽는** env → 요구 값."""

    body = _reachable_body(_GUARD_ENTRY)
    optional = _optional_envs(body)
    return {
        name: value
        for name, value in _comparison_values(body).items()
        if name not in optional
    }


def _evaluation_required_values() -> dict[str, str]:
    """**config 평가 시 항상 도는** 코드가 값까지 비교하는 env → 요구 값.

    `assertNotProdUnlessOptedIn`은 IIFE라 spec과 무관하게 매번 돈다. 여기서 throw하면
    executor는 3초 만에 죽고 로그도 안 남는다 — 2026-09-05에 그랬다. 종전 게이트는
    `^function`만 찾아 이 블록을 통째로 보지 못했고, executor가
    `E2E_LIVE_ALLOW_PROD=1`을 빼도 green이었다(적대 리뷰 실측).
    """

    body = _reachable_body(_EVALUATION_ENTRY)
    optional = _optional_envs(body)
    return {
        name: value
        for name, value in _comparison_values(body).items()
        if name not in optional
    }


def _value_constraints() -> dict[str, str]:
    """config가 **값을 제한하는** env 전부 → 허용 값.

    가드와 평가 코드 양쪽을 합친다. 이 표는 두 방향으로 쓴다 — 요구되는 env는 그 값을
    넘겨야 하고(양), 요구되지 않는 env라도 넘긴다면 그 값이어야 한다(음).
    """

    merged = _comparison_values(_reachable_body(_EVALUATION_ENTRY))
    merged.update(_comparison_values(_reachable_body(_GUARD_ENTRY)))
    return merged


def _executor_node() -> ast.FunctionDef:
    tree = ast.parse(_SUPERVISOR.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "executor":
            return node
    raise AssertionError("supervisor에서 `executor` 메서드를 찾지 못했다")


def _literal_prefix(node: ast.expr) -> str | None:
    """`"E2E_X=1"` 또는 `f"E2E_X={...}"`에서 앞쪽 리터럴을 읽는다."""

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values:
        head = node.values[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _env_pairs(items: list[ast.expr]) -> dict[str, str | None]:
    """`"--env", "NAME=VALUE"` 또는 `"--env", "NAME"` 쌍을 이름 → 값으로."""

    pairs: dict[str, str | None] = {}
    for index, item in enumerate(items[:-1]):
        head = _literal_prefix(item)
        if head != "--env":
            continue
        argument = _literal_prefix(items[index + 1])
        if argument is None or not argument.startswith("E2E_"):
            continue
        name, separator, value = argument.partition("=")
        pairs[name] = value if separator else None
    return pairs


def _executor_env_arguments() -> tuple[dict[str, str | None], dict[str, str | None]]:
    """executor가 넘기는 env를 (무조건, 조건부)로 나눠 유도한다.

    종전에는 메서드 소스 전체에 정규식을 돌려 **조건부 `command.extend`도 "넘긴다"로
    셌다**. 요구 env를 `if self.args.recovery_only:` 안으로 옮기면 main lane은 config
    평가에서 죽는데 게이트는 green이었다(적대 리뷰 실측). AST로 무조건 경로만 센다.
    """

    node = _executor_node()
    unconditional: dict[str, str | None] = {}
    conditional: dict[str, str | None] = {}
    for statement in node.body:
        if (
            isinstance(statement, ast.Assign)
            and isinstance(statement.value, ast.List)
            and any(
                isinstance(target, ast.Name) and target.id == "command"
                for target in statement.targets
            )
        ):
            unconditional.update(_env_pairs(statement.value.elts))
    for inner in ast.walk(node):
        if not isinstance(inner, ast.If):
            continue
        for call in ast.walk(inner):
            if not isinstance(call, ast.Call) or not call.args:
                continue
            func = call.func
            if not (isinstance(func, ast.Attribute) and func.attr == "extend"):
                continue
            argument = call.args[0]
            if isinstance(argument, (ast.Tuple, ast.List)):
                conditional.update(_env_pairs(list(argument.elts)))
    return unconditional, conditional


def _forbidden_topology_envs() -> tuple[str, ...]:
    return ("E2E_ISOLATED_LIVE_EVIDENCE", "E2E_ISOLATED_LIVE_DOCKER_NETWORK")


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    constants = _env_constants()
    assert len(constants) >= 3, f"config에서 env 상수를 {len(constants)}개만 읽었다"
    flags = _flag_to_env()
    assert flags, "config의 격리 플래그를 하나도 유도하지 못했다"
    bodies = _function_bodies()
    # IIFE 형태를 실제로 파싱했는지 본다. 종전 파서는 `^function`만 찾아 config 평가
    # 코드를 통째로 놓쳤고, 그 사실이 `len(bodies) >= 5` 같은 개수 단언에는 잡히지
    # 않았다(동어반복).
    assert _EVALUATION_ENTRY in bodies, (
        f"config 평가 IIFE `{_EVALUATION_ENTRY}`를 파싱하지 못했다 — 그 안의 요구가 "
        "통째로 유도에서 빠진다."
    )
    assert _GUARD_ENTRY in bodies, f"acceptance 가드 `{_GUARD_ENTRY}`를 파싱하지 못했다"
    assert _required_envs(), "가드가 요구하는 env를 하나도 유도하지 못했다 — 파서를 의심하라"
    # 값 결박이 살아 있는지 본다. 비면 `test_executor_passes_the_value...`가 공허하다.
    assert _guard_required_values(), (
        "가드의 값 비교를 하나도 유도하지 못했다 — 값 결박이 공허해졌다. 가드가 지역 "
        "const를 경유하는 형태로 바뀌었는지 보라."
    )
    assert _evaluation_required_values(), (
        "config 평가 코드의 값 비교를 하나도 유도하지 못했다 — 평가 단계 요구가 "
        "통째로 사라졌다."
    )
    unconditional, _ = _executor_env_arguments()
    assert len(unconditional) >= 5, (
        f"executor의 무조건 `--env` 인자를 {len(unconditional)}개만 읽었다 — 파서를 의심하라"
    )


def test_executor_declares_every_env_the_guard_requires() -> None:
    """가드가 요구하는 격리 선언을 executor가 **무조건 경로에서** 넘겨야 한다."""

    unconditional, conditional = _executor_env_arguments()
    required = _required_envs()
    missing = sorted(env for env in required if env not in unconditional)
    behind_a_branch = sorted(env for env in missing if env in conditional)
    assert missing == [], (
        f"live config의 acceptance 가드가 요구하는 env를 executor가 무조건 `--env`로 "
        f"넘기지 않는다: {missing}"
        + (
            f" (그중 {behind_a_branch}는 조건부 `command.extend` 뒤에 있다 — 그 분기가 "
            "타지 않는 실행에서는 config 평가가 죽는다)"
            if behind_a_branch
            else ""
        )
        + ". 넘기지 않으면 Playwright가 **config 평가에서** 죽고, 선언이 사실이 아니라면 "
        "config 쪽 요구를 다시 판단하라 — 거짓 선언으로 통과시키지 마라."
    )


def test_executor_declares_every_env_the_config_evaluation_requires() -> None:
    """config 평가 IIFE가 요구하는 env도 무조건 경로에 있어야 한다.

    이 블록은 spec과 무관하게 **매 평가마다** 돈다. `E2E_LIVE_ALLOW_PROD=1`이 여기서
    나온다 — 종전 게이트는 이 블록을 읽지 못해 그것을 빼도 green이었다.
    """

    unconditional, _ = _executor_env_arguments()
    required = _evaluation_required_values()
    missing = sorted(name for name in required if name not in unconditional)
    assert missing == [], (
        f"config 평가 시 항상 도는 `{_EVALUATION_ENTRY}`가 요구하는 env를 executor가 "
        f"넘기지 않는다: {missing}. 이 lane은 공개 HTTPS prod origin을 쓰므로 그 분기를 "
        "반드시 탄다 — 넘기지 않으면 어떤 spec도 시작하지 못한다."
    )


def test_executor_passes_the_value_the_guard_compares() -> None:
    """가드가 값을 비교하면 executor가 **그 값**을 넘겨야 한다."""

    unconditional, _ = _executor_env_arguments()
    wrong = {
        name: unconditional.get(name)
        for name, want in _guard_required_values().items()
        if name in unconditional and unconditional[name] != want
    }
    assert wrong == {}, (
        f"executor가 가드의 비교값과 다른 값을 넘긴다: {wrong}. 가드는 값을 비교하므로 "
        "이름만 맞추면 config 평가에서 죽는다."
    )


def test_executor_never_declares_a_value_the_config_rejects() -> None:
    """**음 방향 결박** — executor가 config가 거부하는 선언을 하면 안 된다.

    2026-09-05 사건의 거울상이다. 그때는 필요한 선언을 안 해서 죽었고, 반대로 config가
    "값 V만 허용"하는 env를 다른 값으로 넘겨도 같은 자리에서 죽는다. supervisor에는
    "여기서 선언하지 않는다"는 주석뿐이었고 그것을 확인하는 것이 없었다.
    """

    unconditional, conditional = _executor_env_arguments()
    passed = {**unconditional, **conditional}
    constraints = _value_constraints()
    violations = {
        name: passed[name]
        for name, want in constraints.items()
        if name in passed and passed[name] is not None and passed[name] != want
    }
    assert violations == {}, (
        f"executor가 config가 거부하는 값을 넘긴다: {violations}. config는 그 env에 "
        f"특정 값만 허용한다({ {k: v for k, v in constraints.items() if k in violations} }) "
        "— 다른 값이면 config 평가에서 throw다."
    )
    forbidden = sorted(env for env in _forbidden_topology_envs() if env in passed)
    assert forbidden == [], (
        f"executor가 topology 선언을 넘긴다: {forbidden}. 그 선언은 localhost 대상에서만 "
        "참이고 이 lane은 공개 HTTPS prod origin이라 "
        f"`{_EVALUATION_ENTRY}`가 즉시 throw한다."
    )


def test_the_guard_does_not_require_a_topology_this_lane_cannot_have() -> None:
    """가드가 prod lane이 만족할 수 없는 topology 선언을 요구하지 않아야 한다.

    `E2E_ISOLATED_LIVE_EVIDENCE`는 `assertNotProdUnlessOptedIn`에서 `isLocalHost`
    대상을 요구한다. D2 lane은 공개 HTTPS prod origin을 쓰므로 그 선언은 **거짓**이고,
    거짓으로 통과시키는 대신 가드가 실제로 필요한 것만 요구해야 한다. 2026-09-05에
    이 결박이 executor를 구조적으로 통과 불가로 만들었다.
    """

    required = _required_envs()
    forbidden = sorted(env for env in _forbidden_topology_envs() if env in required)
    assert forbidden == [], (
        f"acceptance 감사 마커 가드가 topology 선언을 요구한다: {forbidden}. "
        "그 선언은 localhost 대상에서만 참이라 prod lane이 만족할 수 없다 — "
        "거짓 선언으로 통과시키지 말고 가드가 실제로 필요한 것만 요구하게 하라."
    )
    assert "E2E_ADMIN_FEATURE_ACCEPTANCE_WRITE" in required, (
        "가드가 acceptance write opt-in을 더 이상 요구하지 않는다. 그러면 아무 live "
        "실행이나 감사 마커를 붙일 수 있고 cleanup·audit의 소유 회계가 오염된다."
    )
