# resume.md — 현재 진척도와 다음 한 작업

## 2026-09-06 — M01 활성화 완료, D2는 증거 계약 마지막 하나까지 닫고 재실행 대기

| 항목 | 상태 |
|---|---|
| `T-VN-M01` 활성화 | **완료** — ACL 55/55(rebuild 앞뒤 두 번) · 거부 축 4/4 403 · witness 8관계 zero-write · 성공 축 201 |
| kill-switch | `KOR_TRAVEL_MAP_API_ADMIN_MANUAL_FEATURE_CREATE_ENABLED=true` (2026-09-05T20:27:59Z) |
| D2 스펙 | **in-lane 통과** — main·recovery 각 `{"counts":{"passed":2},"result":"passed"}` |
| D2 잔여 | 증거 계약 결함 열둘을 순서대로 닫았다. 마지막(`summary_run_ids`)은 #1166 |
| 게이트 | 결함마다 `tests/lint/` 탐지기, 전부 변이로 red 확인 |
| pinset | Map `ab3640f8` + PinVi #535 — rebuild `48166bd2…`, generation `56d331a7…` |

### 다음 한 작업

**그 pinset에서 D2를 완주시킨다.** executor 이미지 재빌드 → 스냅샷 재설치(repin) →
ACL preflight 재측정 → D1 → D2 순이고 전 과정이 스크립트로 남아 있다. D2가 `passed`를
쓰면 receipt 승격이 따라온다.

그 뒤 순서는 `T-VN-41C` → `T-VN-41F1D-E`다. `GM-17`은 소유자 지시로 **가장 마지막**이다.
`T-VN-D2-API-AUDIT`(helper의 `api-audit`/`purge` 경로가 한 번도 실행된 적 없음)은 D2 완주와
분리했다 — 고치려면 clone lane의 content digest 계약까지 함께 판단해야 한다.

### 이번에 확인된 운영 사실

- **증거 계약 위반은 스펙이 통과한 뒤에야 드러난다.** `_validate_evidence`가 정확한
  파일명 집합과 action별 키를 요구하는데 그 검증이 스펙 통과 뒤에 돌기 때문이다. 그래서
  결함이 병렬로 안 보이고 배포 스택 실행 한 번에 하나씩 직렬로 나온다 — 열두 번을 그렇게
  썼다. 게이트를 로컬에서 유도해 미리 깨뜨리는 것이 그 비용의 유일한 대안이다.
- **executor 이미지를 같은 `:local` 태그로 다시 빌드하면 핀이 가리키던 이미지가 사라진다.**
  live attestation이 image ID를 exact로 들고 있어 재빌드 순서를 지켜야 한다.
- **`rolinherit=false`라 privilege 확인에 `::regclass`를 쓸 수 없다.** Map 역할 전부가
  NOINHERIT이므로 preflight는 catalog join으로만 판정한다.


## 2026-09-05 — 새 pinset에서 D1 통과, D2는 helper 결함 셋을 고치고 재실행 대기

`af6d7061`(Map `c72456f6` + PinVi `f4401659`)로 rebuild를 마쳤고, attestation을 재발행해
verifier가 PASS했다. D1은 통과했다. D2는 seed에서 죽었고 원인 셋을 전부 고쳐 실 DB에
대고 seed → cleanup → audit을 통과시켰다.

| 항목 | 상태 |
|---|---|
| pinset | **`af6d7061`** = Map `c72456f6` + PinVi `f4401659` |
| rebuild | 성공 (`2acd8e97…`, generation `31622c79…`) |
| host attestation v4 | 재발행 `10ad0f0f…` — **verifier PASS** |
| C7 executor image | `sha256:f760bf6c…` (라벨 `c72456f6`) |
| `T-VN-41F1D-D1` | **통과** — 데이터 비의존 live UI 11/11, 33.2초, 핀 자신의 스펙 바이트로 실행 |
| `T-VN-41F1D-D2` | helper 결함 셋 수정 완료, 실 DB 사이클 통과. **재실행 대기** |
| D2 lane 상태 | `BLOCKED` 해제 — 잔여물 0 실측 후 `clear-blocked`, 증거는 `adjudicated-…`에 보존 |

### 다음 한 작업

**helper 수정을 머지한 뒤 pinset을 한 번 더 돌린다.** 설치 스냅샷 디렉터리 이름이
`E2E_C7_EXPECTED_GIT_COMMIT`에 결박돼 있고 그것이 attestation의 `repository_commit`·
generation의 `map_source_revision`과 exact여야 하므로, Map revision이 바뀌면
rotate-pair → rebuild → attestation 재발행 → 스냅샷 재설치 → executor 이미지 재빌드 →
D1 → D2가 따라온다. 전 과정이 이번에 스크립트로 남았다.

그 뒤 순서는 `T-VN-41C` → `T-VN-41F1D-E`다. `GM-17`(Manager production compose
required-set 완화)은 소유자 지시로 **가장 마지막**이다.

### 이번에 확인된 운영 사실

- **out-of-band DB 패치는 다음 rebuild에 증발한다.** 어제 배포 DB에 손으로 준
  `GRANT SELECT ON public.alembic_version TO ktm_feature_migrator`가 rebuild로 사라졌고,
  그래서 helper의 진짜 결함이 드러났다. DB가 선언된 계약으로 수렴하는 건 좋은 성질이지만
  그런 패치에 기댄 green은 근거가 되지 못한다.
- **rebuild가 `BLOCKED` lane을 가로지르면 `recover`가 구조적으로 불가능하다.**
  `begin-recovery`가 BLOCKED의 execution identity와 현재 identity의 일치를 요구하는데
  rebuild가 여섯 필드를 전부 바꾼다. 이때의 정본 경로는 잔여물을 직접 측정해 0임을 확인한
  뒤 `clear-blocked`로 정리하고 증거를 남기는 것이다.

### 남아 있는 소유자 판정

- `docker/*.py` 여섯 파일이 `mypy --strict` clean인데도 검사 밖이다(n150 실측). 프로덕션
  기동을 막는 `application-schema-final-permit.py`가 그중 하나다. 편입 비용은 0이지만
  `application-schema-fresh-finalize.py`(5건)·`dagster-storage-migrate.py`(4건)는
  정리가 필요해 경계를 어디에 둘지가 판단이다.
- CI의 mypy는 핀이 없다(`mypy>=1.10`). 새 mypy 릴리스가 검사를 조이면 무관한 PR에서
  `lint` job이 붉어질 수 있다 — 기존 세 스텝도 같은 노출을 갖는다.
