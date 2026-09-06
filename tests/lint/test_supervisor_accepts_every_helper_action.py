"""supervisor의 `--helper-action` 허용 목록을 **러너 호출부**에 결박한다.

lane은 helper를 supervisor 경유로만 띄운다. supervisor의 argparse `choices`에 없는
action을 넘기면 **argparse가 exit 2로 죽는다** — 그것은 lifecycle 파일도 출력 파일도
쓰기 **전**이라 lane에 아무 흔적이 남지 않는다. 러너 쪽에서는 그저 helper가 non-zero를
냈을 뿐이라 "cleanup left residue"로 보이고, 증거 디렉터리에는 그 operation이 통째로
없다.

2026-09-06에 정확히 그랬다. `T-VN-D2-API-AUDIT`이 `run_helper api-audit`을 더하면서
네 곳(러너 선언·검증기의 두 집합·러너 호출부)을 함께 고쳤는데, **다섯째인 이곳을
아무도 세지 않았다.** 배포 스택 실행 한 번을 통째로 치르고서야 알았다:

    lifecycle 48개 = 6 operation × 8 phase   ← helper-api-audit은 0개
    direct-api-audit.json 없음                ← supervisor가 쓰기 전에 죽었다
    runner die: "owned fixture cleanup left residue"

그래서 이 게이트가 러너의 `run_helper <action>` 호출부에서 action을 유도해 supervisor의
허용 목록과 대조한다(AGENTS.md DO NOT 15: 유도 → 결박 → 탐지).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _ROOT / "scripts" / "run-admin-feature-live-acceptance.sh"
_SUPERVISOR = _ROOT / "scripts" / "admin_feature_live_supervisor.py"
_FIXTURE = _ROOT / "scripts" / "admin_feature_live_fixture.py"

_HELPER_CALL = re.compile(
    r"^\s*run_helper\s+(?P<action>[a-z][a-z0-9-]*)\b", re.MULTILINE
)


def _runner_actions() -> set[str]:
    """러너가 실제로 부르는 helper action."""

    source = _RUNNER.read_text(encoding="utf-8")
    stripped = "\n".join(re.sub(r"#.*$", "", line) for line in source.splitlines())
    return {match.group("action") for match in _HELPER_CALL.finditer(stripped)}


def _argparse_choices(path: Path, option: str) -> set[str]:
    """`parser.add_argument("<option>", choices=(...))`의 choices를 AST로 읽는다."""

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_argument"):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and first.value == option):
            continue
        for keyword in node.keywords:
            if keyword.arg != "choices":
                continue
            if isinstance(keyword.value, (ast.Tuple, ast.List, ast.Set)):
                return {
                    element.value
                    for element in keyword.value.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)
                }
    return set()


def _supervisor_actions() -> set[str]:
    return _argparse_choices(_SUPERVISOR, "--helper-action")


def _helper_actions() -> set[str]:
    return _argparse_choices(_FIXTURE, "action")


def test_the_gate_reads_every_side() -> None:
    """세 쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    runner = _runner_actions()
    assert len(runner) >= 3, f"러너의 helper 호출을 {len(runner)}개만 유도했다 — 파서를 의심하라"
    supervisor = _supervisor_actions()
    assert supervisor, "supervisor의 `--helper-action` choices를 읽지 못했다"
    helper = _helper_actions()
    assert len(helper) >= 5, f"helper의 action choices를 {len(helper)}개만 읽었다"


def test_the_supervisor_accepts_every_action_the_runner_invokes() -> None:
    """러너가 부르는 action을 supervisor가 전부 받아야 한다.

    빠지면 argparse가 **아무 흔적 없이** exit 2로 죽는다 — lifecycle도 출력도 남지
    않아 lane 실패가 엉뚱한 곳(cleanup residue)을 가리킨다.
    """

    missing = sorted(_runner_actions() - _supervisor_actions())
    assert missing == [], (
        f"러너가 부르는 helper action을 supervisor가 거부한다: {missing}. "
        "argparse `choices`에 없으면 exit 2로 죽고 **lifecycle도 출력 파일도 남지 "
        "않는다** — 배포 스택 실행 한 번을 치른 뒤에야 알게 된다. "
        "`--helper-action`의 choices에 더해라."
    )


def test_the_supervisor_does_not_accept_actions_the_helper_cannot_run() -> None:
    """supervisor가 helper에 없는 action을 받아들이면 안 된다.

    받아들이면 supervisor는 컨테이너를 띄우고, 죽는 쪽은 helper다 — 진단이 한 겹
    멀어진다.
    """

    unknown = sorted(_supervisor_actions() - _helper_actions())
    assert unknown == [], (
        f"supervisor가 helper에 없는 action을 허용한다: {unknown}. "
        "컨테이너를 띄운 뒤 helper 쪽에서 죽으므로 진단이 멀어진다."
    )


def test_the_supervisor_allowlist_is_not_wider_than_the_lane_needs() -> None:
    """부르지 않는 action은 허용하지 않는다.

    허용 목록은 컨테이너 인자가 되는 보안 경계다. 호출자가 없는 action을 열어 두면
    그만큼 넓어지기만 한다 — `purge`가 그 예다(hard purge는 `T-VN-M02`까지 fence).
    """

    extra = sorted(_supervisor_actions() - _runner_actions())
    assert extra == [], (
        f"러너가 부르지 않는 action을 supervisor가 허용한다: {extra}. "
        "허용 목록은 컨테이너 인자가 되는 경계다 — 호출자가 생길 때 함께 열어라."
    )
