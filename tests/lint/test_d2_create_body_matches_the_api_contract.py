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

2026-09-06 적대 리뷰가 과허용 넷을 **실측**했다. 전부 "게이트가 자기 존재 이유인
422를 green으로 통과시킨다"였다:

1. 스펙 키 유도가 **따옴표 친 키**(`"lifecycle_state": "active"`)를 보지 못했다.
   TS/JSON에서 완전히 정상인 형태이고 이 저장소엔 prettier도 quote 룰도 없다.
2. `spec.index(...)`가 **첫 create 호출만** 봤다. 두 번째 호출이 무엇을 보내든 무관.
3. 결박이 **단방향**(spec ⊆ model)이라 모델에 **기본값 없는 필드**가 늘면 스펙은
   그것을 안 보내고 프로덕션은 `422 field required`가 된다.
4. `extra="forbid"` 확인이 텍스트 검색이라 **docstring 문구**로 만족되고,
   서브클래스의 `model_config` override도 보지 못했다.

**덮지 못하는 것**: 조건부 spread(`...(SEED ? { x: 1 } : {})`)로 키를 넣으면 이
정규식 유도는 보지 못한다. TS 파서가 없으면 닫히지 않는 구멍이라 사실로 남긴다 —
스펙 body는 리터럴 키로만 쓴다는 규약에 기대고 있다.
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
_CREATE_PATH = '"/v1/admin/features",'
#: body 블록 한 줄의 키. 들여쓰기 폭은 **블록에서 유도**한다 — 리터럴 12로 박으면
#: 스펙을 다시 포맷하는 순간 키를 0개로 읽는다. 따옴표를 친 형태도 TS에서 정상이므로
#: 양쪽 다 읽는다.
_SPEC_KEY_LINE = re.compile(
    r"""^(?P<indent>\s*)["']?(?P<name>[a-z][a-z0-9_]*)["']?\s*:"""
)


def _router_tree() -> ast.Module:
    return ast.parse(_ROUTER.read_text(encoding="utf-8"))


def _class_nodes() -> dict[str, ast.ClassDef]:
    return {
        node.name: node
        for node in ast.walk(_router_tree())
        if isinstance(node, ast.ClassDef)
    }


def _mro(start: str) -> list[ast.ClassDef]:
    """`start`부터 선언된 base를 따라간 순서(파생 먼저).

    base 이름을 손으로 적지 않는다 — 종전에는 `AdminFeatureBaseMutation`을 리터럴로
    합쳤는데, create가 base에서 떨어져 나가면 게이트가 없는 필드를 계속 허용해
    **거짓 green**이 된다.
    """

    classes = _class_nodes()
    assert start in classes, f"{start}을 찾지 못했다 — 게이트가 공허해졌다"
    order: list[ast.ClassDef] = []
    seen: set[str] = set()
    pending = [start]
    while pending:
        name = pending.pop(0)
        if name in seen or name not in classes:
            continue
        seen.add(name)
        node = classes[name]
        order.append(node)
        pending.extend(base.id for base in node.bases if isinstance(base, ast.Name))
    return order


def _field_call(statement: ast.AnnAssign) -> ast.Call | None:
    if isinstance(statement.value, ast.Call):
        func = statement.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "Field":
            return statement.value
    return None


def _wire_name(statement: ast.AnnAssign, attribute: str) -> str:
    """전선 위의 이름 — `Field(alias=...)`가 있으면 그것이다.

    `extra="forbid"` + `populate_by_name` 미설정이면 속성 이름은 **받아들여지지
    않는다**. 속성 이름으로 대조하면 게이트가 틀린 스펙을 통과시키고 옳은 스펙을
    red로 만든다(부호 반전 — 적대 리뷰 실측).
    """

    call = _field_call(statement)
    if call is not None:
        for keyword in call.keywords:
            if (
                keyword.arg in {"alias", "validation_alias"}
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return attribute


def _has_default(statement: ast.AnnAssign) -> bool:
    """생략 가능한가 — 기본값이 있거나 `Field(default…)`를 준다."""

    if statement.value is None:
        return False
    call = _field_call(statement)
    if call is None:
        return True
    if call.args:
        return True
    return any(
        keyword.arg in {"default", "default_factory"} for keyword in call.keywords
    )


def _annotation_allows_none(statement: ast.AnnAssign) -> bool:
    return "None" in ast.unparse(statement.annotation)


def _declared(node: ast.ClassDef) -> dict[str, bool]:
    """클래스 **본문에 직접 선언된** field → 필수 여부(전선 이름 기준).

    종전에는 `\\nclass X(` 부터 다음 `\\nclass ` 까지를 텍스트로 잘라 정규식을
    돌렸다. 두 클래스 사이에 있는 **모듈 수준 함수의 지역 주석까지 쓸어 담았고**,
    실제로 `try`가 "모델 필드"로 잡혔다(2026-09-06 적대 리뷰 실측). AST로 본문만 본다.
    """

    fields: dict[str, bool] = {}
    for statement in node.body:
        if not isinstance(statement, ast.AnnAssign) or not isinstance(
            statement.target, ast.Name
        ):
            continue
        name = _wire_name(statement, statement.target.id)
        if name == "model_config":
            continue
        fields[name] = not _has_default(statement) and not _annotation_allows_none(
            statement
        )
    return fields


def _create_request_fields() -> set[str]:
    """`AdminFeatureCreateRequest`와 **선언된 base들**의 필드 집합(전선 이름)."""

    fields: set[str] = set()
    for node in _mro(_CREATE_MODEL):
        fields |= set(_declared(node))
    return fields


def _create_required_fields() -> set[str]:
    """생략하면 `422 field required`가 되는 필드."""

    required: set[str] = set()
    optional: set[str] = set()
    for node in _mro(_CREATE_MODEL):
        for name, is_required in _declared(node).items():
            # 파생 클래스가 먼저 오므로 이미 본 이름은 override된 것으로 본다.
            if name in required or name in optional:
                continue
            (required if is_required else optional).add(name)
    return required


def _effective_extra() -> str | None:
    """`model_config = ConfigDict(extra=...)`의 **가장 파생된** 선언 값.

    종전에는 `ast.unparse(node)`에 `extra="forbid"`가 들어 있는지만 봤다. docstring에
    그 문구가 있어도 True였고, 서브클래스가 `extra="allow"`로 override해도 True였다
    (적대 리뷰 실측). 선언을 구조로 읽고 파생 우선으로 정한다.
    """

    for node in _mro(_CREATE_MODEL):
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or not isinstance(
                statement.value, ast.Call
            ):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "model_config"
                for target in statement.targets
            ):
                continue
            func = statement.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name != "ConfigDict":
                continue
            for keyword in statement.value.keywords:
                if keyword.arg == "extra" and isinstance(keyword.value, ast.Constant):
                    value = keyword.value.value
                    if isinstance(value, str):
                        return value
    return None


def _spec_create_bodies() -> list[set[str]]:
    """스펙이 `POST /v1/admin/features`에 보내는 **모든** body의 키.

    종전에는 `spec.index(...)`로 첫 호출만 봤다. 두 번째 create 호출이 무엇을 보내든
    게이트는 green이었다(적대 리뷰 실측).
    """

    spec = _SPEC.read_text(encoding="utf-8")
    bodies: list[set[str]] = []
    for match in re.finditer(re.escape(_CREATE_PATH), spec):
        start = spec.find("body: {", match.start())
        if start == -1:
            continue
        bodies.append(_object_keys(spec, start + len("body: ")))
    return bodies


def _matching_brace(text: str, opening: int) -> int:
    """`{`의 짝을 찾는다. 문자열 리터럴 안의 중괄호는 세지 않는다."""

    depth = 0
    index = opening
    quote: str | None = None
    while index < len(text):
        character = text[index]
        if quote is not None:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = None
        elif character in "\"'`":
            quote = character
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise AssertionError("스펙에서 body 객체의 닫는 중괄호를 찾지 못했다")


def _object_keys(text: str, opening: int) -> set[str]:
    """객체 리터럴의 **최상위** 키만 읽는다.

    종전에는 12칸 들여쓰기를 리터럴로 박고 닫는 위치도 `"\n          },"` 문자열로
    찾았다. 스펙을 다시 포맷하거나 create 호출을 하나 더 두면 그대로 어긋난다.
    중괄호를 맞춰 블록을 자르고 최상위 들여쓰기는 첫 키 줄에서 유도한다.
    """

    block = text[opening : _matching_brace(text, opening) + 1]
    indent: str | None = None
    keys: set[str] = set()
    for line in block.splitlines()[1:]:
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*")):
            continue
        match = _SPEC_KEY_LINE.match(line)
        if match is None:
            continue
        if indent is None:
            indent = match.group("indent")
        if match.group("indent") == indent:
            keys.add(match.group("name"))
    return keys


def _spec_create_body_keys() -> set[str]:
    bodies = _spec_create_bodies()
    return set().union(*bodies) if bodies else set()


def test_the_gate_reads_both_sides() -> None:
    """대조 양쪽이 실제로 읽혔는지부터 본다 — 비면 아래 단언이 공허하다."""

    fields = _create_request_fields()
    assert len(fields) >= 15, f"모델 필드를 {len(fields)}개만 읽었다 — 파서를 의심하라"
    bodies = _spec_create_bodies()
    assert bodies, "스펙에서 create body를 하나도 찾지 못했다 — 파서를 의심하라"
    for index, keys in enumerate(bodies):
        assert len(keys) >= 5, f"{index}번째 create body 키를 {len(keys)}개만 읽었다"
    # 모델이 실제로 `extra="forbid"`여야 이 대조가 의미를 갖는다.
    extra = _effective_extra()
    assert extra == "forbid", (
        f"create body 모델 계열의 유효 `extra`가 {extra!r}다 — 모델에 없는 키를 "
        "보내도 422가 아니게 되므로 이 게이트를 다시 판단하라"
    )
    # 텍스트 슬라이스가 쓸어 담던 비-필드가 사라졌는지 본다.
    assert "try" not in fields, (
        "필드 집합에 `try`가 있다 — 클래스 본문이 아니라 텍스트 구간을 읽고 있다"
    )
    assert _create_required_fields(), "필수 필드를 하나도 유도하지 못했다 — 파서를 의심하라"


def test_every_spec_create_key_exists_in_the_request_model() -> None:
    """스펙이 보내는 키가 전부 모델에 있어야 한다."""

    fields = _create_request_fields()
    for index, keys in enumerate(_spec_create_bodies()):
        unknown = sorted(keys - fields)
        assert unknown == [], (
            f"{index}번째 D2 create body가 `{_CREATE_MODEL}`에 없는 키를 보낸다: "
            f"{unknown}. 모델은 `extra=\"forbid\"`라 그 요청은 **422로 죽는다** — "
            "배포 스택 실행 도중에 알게 두지 마라. 서버가 정하는 값이면 보내지 말고, "
            "정말 필요한 입력이면 모델에 더해라."
        )


def test_every_required_model_field_is_in_the_spec_create_body() -> None:
    """**반대 방향** — 모델의 필수 필드를 스펙이 전부 보내야 한다.

    종전 결박은 단방향이었다. 모델에 기본값 없는 필드가 하나 늘면 스펙은 그것을
    보내지 않고 프로덕션은 `422 field required`가 된다 — 이 게이트가 막겠다고 한
    것과 정확히 같은 비용(배포 스택 실행 1회)이 그대로 발생한다(적대 리뷰 실측).
    """

    required = _create_required_fields()
    for index, keys in enumerate(_spec_create_bodies()):
        missing = sorted(required - keys)
        assert missing == [], (
            f"{index}번째 D2 create body가 `{_CREATE_MODEL}`의 필수 필드를 빠뜨린다: "
            f"{missing}. 기본값이 없으므로 그 요청은 **422 field required**로 죽는다. "
            "서버가 채워야 할 값이면 모델에 기본값을 주고, 아니면 스펙이 보내라."
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
