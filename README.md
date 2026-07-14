# 이미지 데이터셋 템플릿 — 수집 · 자동라벨 · 팀 검수 · 온디바이스 추천

이미지 폴더 하나에서 출발해 **라벨링된 학습용 데이터셋**과 **팀이 함께 쓰는 검수·탐색 웹앱**까지
가는 파이프라인이다. 도메인은 설정 파일로 갈아끼운다.

**서버가 0대다.** 무거운 계산(CLIP 임베딩·포즈 추정·객체 검출)은 오프라인에서 미리 하고,
앱은 self-contained HTML 한 장으로 정적 호스팅에 올라간다. 실시간 협업 검수는 Firebase로,
웹캠 추천은 브라우저 안에서(transformers.js) 돈다.

## 2분 만에 화면 보기

```bash
git clone https://github.com/sionhyeop/image-dataset-template && cd image-dataset-template
uv venv .venv && uv pip install --python .venv/bin/python -r requirements-min.txt

.venv/bin/python scripts/review/app.py                       # 예제 데이터셋으로 앱 생성
xdg-open datasets/example_plants/data/04_samples/index.html  # macOS: open
```

**예제 데이터셋(관엽식물 24장)이 들어 있어 아무 설정 없이 바로 뜬다.**
`requirements-min.txt` 는 400MB 뿐이고 torch 를 안 받는다 — 검수앱·테스트가 다 돈다.
(`pytest -q` → 29개 통과)

자기 이미지를 **라벨링**하려면 그때 전체 설치를 한다:
```bash
uv pip install --python .venv/bin/python -r requirements.lock   # CLIP·포즈·검출 (수 GB)
```

## Claude Code 로 바로 시작하기

이 저장소는 **Claude Code 와 함께 쓰도록** 만들어졌다. 클론하면 두 가지가 딸려온다:

- **`CLAUDE.md`** — Claude Code 가 매 세션 읽는 운영 매뉴얼. 절대 규칙(축 이름을 코드에 박지
  마라 · 게이트를 손으로 찍지 마라 · 프록시로 최적화하지 마라)과 이 프로젝트가 실제로 밟은
  지뢰들이 들어 있다.
- **`/bootstrap` 스킬** — 이미지 폴더 + "이걸로 뭘 하고 싶은지" 한 문장을 주면,
  Claude Code 가 **이미지를 실제로 열어보고** `taxonomy.yaml`·`prompts.yaml`·`queries.yaml`
  초안을 쓴 뒤 데이터로 검증한다. API 키가 필요 없다 — 이미지를 보는 건 Claude Code 자신이다.

```
> /bootstrap
```

이미지는 위키미디어 공용에서 재배포 허용 라이선스(CC0/PD/CC BY(-SA))만 골라 받은 것이고,
출처·저작자가 `master_metadata.csv` 에 남아 있다.

## 이 템플릿으로 만든 것

| | 라이브 | 축 | 코드 | 승인 이미지 |
|---|---|---|---|---|
| 인물 구도 | [reviewsite-five.vercel.app](https://reviewsite-five.vercel.app) | 7 (장소·포즈·프레이밍·앵글·성별·인원·표정) | 46 | 1,773 |
| 음식 구도 | [reviewsitefood.vercel.app](https://reviewsitefood.vercel.app) | 5 (요리·앵글·플레이팅·조명·바닥) | 25 | 189 |

두 사이트는 **코드가 100% 같다.** 차이는 `datasets/<이름>/` 의 YAML 세 개뿐이다.
인물 사이트에는 포즈캠(웹캠으로 내 포즈와 비슷한 컷 추천)이 있고 음식 사이트에는 없다 —
축 선언에 사람이 없으니 **자동으로 꺼진다.**

이 저장소에는 각 도메인의 **설정만** 들어 있다. 수집한 이미지와 팀 검수 기록은 포함되지 않는다.

---

## 핵심 원칙: 측정이 설정을 만든다

도메인 지식을 코드 상수에 박지 않는다. 사람이 추측해서 넣던 값을 **데이터가 정하게** 한다.

```
사람의 추측  →  코드 상수                     ✗  (도메인이 바뀌면 전부 거짓말이 된다)
데이터  →  감사기(auditor)  →  profile.json  →  코드     ✓
```

감사기 세 개가 사람이 못 보는 것을 잰다:

| 감사기 | 재는 것 | 산출 |
|---|---|---|
| `check/axis_audit.py` | 이 축을 모델이 읽을 수 있나? (micro-F1 리프트 + 엔트로피) | 축별 `healthy`/`weak`/`model_blind`/`dead` 판정 → 축을 자동라벨에 맡길지 사람에게 맡길지 |
| `check/gate_audit.py` | 어떤 신호가 **차단**용이고 어떤 게 **정렬**용인가 | `curation_profile.json` — 버릴 컷의 48.3% 를 사람 눈에 닿기 전에 차단(살릴 컷 손실 2.1%) |
| `check/signal_audit.py` | 각 시각 신호가 실제로 뭘 보고 있나 | `signal_profile.json` — 추천 가중치 |

> ### ⚠ 대리 목표(proxy)로 최적화하지 마라 — 이 프로젝트에서 **네 번** 데였다
>
> | 프록시가 한 말 | 실제 파이프라인이 한 말 |
> |---|---|
> | signal_audit: 색 가중치 0 | 실제 랭킹에선 0.46 |
> | 리서치 문서: q4f16 은 14MB, 안전 | dtype 불일치로 코사인 0.86 붕괴 |
> | 라벨 채점: '교집합 ≥1이면 정답' | 많이 찍을수록 점수가 오름 → micro-F1 이어야 함 |
> | gate_audit: 리프트 비율 clip=0.79 | 실제로 정렬해 보니 3.0 |
>
> **벤치가 곧 목표 함수다.** 최적화는 반드시 *진짜 파이프라인을 통과시켜서* 해라.
> 자세한 건 [`docs/TEMPLATE.md`](docs/TEMPLATE.md) §5.4.

---

## 구조

```
scripts/          코드 — 도메인 이름이 한 글자도 없어야 한다
config/           모델 가중치 · firebase 자격증명(각자 채운다)
datasets/         ← 도메인이 사는 곳. 자기완결적이다.
  example_plants/   ✅ 바로 도는 예제 (이미지 24장 포함 · 기본 데이터셋)
  composition/      인물 구도 — 설정만 (인물 전용 기능이 켜진 예)
  food/             음식     — 설정만 (주 피사체가 '물건')
  interior/         인테리어 — 설정만 (주 피사체가 '공간')
sites/example/    배포 루트 (COOP/COEP 헤더 — SharedArrayBuffer 용)
tests/            pytest 29 + node 런타임 검증 4스위트
```

데이터셋 선택은 환경변수 하나다:

```bash
python scripts/review/app.py                            # 기본 = datasets/example_plants
DATASET_DIR=datasets/food python scripts/review/app.py  # 다른 도메인
```

**`file_path` 는 데이터셋 루트 기준 상대경로다.** 저장소 루트 기준으로 두면 데이터셋을
폴더째 옮기는 순간 전부 깨진다. 경로 결합은 `common.image_path()` 한 곳에만 있다.

---

## 새 도메인 적용

`/bootstrap` **Claude Code 스킬**이 이미지를 실제로 보고 초안을 써준다(API 키 불필요).
직접 하려면:

```bash
mkdir -p datasets/plants/{data/00_raw,annotations}
$EDITOR datasets/plants/{taxonomy,prompts,queries}.yaml   # datasets/food/ 를 베껴라
export DATASET_DIR=datasets/plants

python scripts/collect/crawl.py        # 또는 기존 이미지를 data/00_raw/ 에 부어라
python scripts/curate/dedup.py
python scripts/label/auto_label.py     # CLIP zero-shot — 키 불필요. 진단을 찍어준다
python scripts/curate/promote.py --auto && python scripts/curate/resize.py
python -m scripts.index.build_index && python scripts/index/embed_map.py
python scripts/review/app.py && bash scripts/review/deploy.sh
```

**여기까지는 초안이다.** 팀이 30장쯤 검수한 뒤가 진짜 시작이다:

```bash
python scripts/check/axis_audit.py      # 어떤 축이 죽었나?
python scripts/check/gate_audit.py      # 뭘로 걸러야 하나?
python scripts/label/relabel.py --axes <좋아진 축만>
```

축 감사가 `dead` 라고 하면 그 축은 CLIP 이 못 읽는 것이니 사람에게 넘기거나 축을 접어라.

### 인물 의존 기능은 선언으로 켜진다

별도 플래그가 없다. `taxonomy._semantic` 에 `facing`/`framing`/`count` 가 **있으면**
포즈캠·인물 속성 백필이 켜지고, 없으면 조용히 꺼진다. **선언이 곧 능력이다.**

---

## 검증

```bash
python -m pytest -q                         # 29개
python scripts/check/check_consistency.py   # taxonomy↔프롬프트↔앱↔CSV↔parquet↔파일수량
node tests/js/verify_app.js                 # 앱 JS 런타임 (브라우저 불필요)
```

`datasets/example_plants/` 는 **인물 흔적이 0인 도메인**이다. 축 이름을 코드에 박아 넣는 순간
테스트가 깨진다 — 템플릿이 템플릿으로 남게 하는 장치다. CI 가 푸시마다 돌린다.

---

## 필요한 것

- Python 3.12 (`.python-version`) · Node 18+ (앱 검증·시각 인덱스용)
- **API 키는 전부 선택이다.** 수집(Bing/Pinterest)도 자동라벨(로컬 CLIP)도 키 없이 돈다.
  스톡 API(Unsplash/Pexels/Pixabay)를 쓸 때만 `.env` 에 키를 넣는다.
- 모델 가중치는 저장소에 없고 첫 실행 때 받아온다:
  CLIP ViT-H/14(~4GB) · MediaPipe Pose(30MB) · RT-DETR(~80MB)
- 팀 협업 검수를 쓰려면 `config/firebase.example.json` → `config/firebase.json`
  ([`docs/FIREBASE.md`](docs/FIREBASE.md)). 없으면 앱이 로컬 모드로 동작한다.

## 더 읽기

- [`docs/TEMPLATE.md`](docs/TEMPLATE.md) — 새 도메인 적용 전문 (taxonomy 구조, 감사 해석, 신호 추가)
- [`docs/DATASET.md`](docs/DATASET.md) — 데이터셋 설계 원칙
- [`docs/FIREBASE.md`](docs/FIREBASE.md) — 서버 없는 실시간 협업 검수
- [`docs/crawl/`](docs/crawl/) — 수집 방법론 (Pinterest 추천엔진 활용 · 대조검증)

## 라이선스

코드는 [MIT](LICENSE).

**이미지는 포함되지 않는다** (예제 데이터셋의 재배포 허용 24장 제외). 크롤 수집분은
`license=unknown` · `usage_allowed=experiment_only` 로 기록되니 **내부 실험용으로만** 쓰고,
상용이 필요하면 스톡 API 수집분으로 재구성해라. 출처는 `raw_metadata.csv` 에 남는다.

기본 검출기는 RT-DETR(Apache-2.0)이다. YOLOv8 백엔드도 있지만 **AGPL-3.0 이라 기본값이 아니다** —
AGPL 은 네트워크 배포에도 전염되므로 공개 웹앱을 만든다면 켜지 마라.
