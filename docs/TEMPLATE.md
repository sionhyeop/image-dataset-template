# 새 이미지셋에 이 시스템 적용하기 (템플릿 가이드)

이 저장소는 **"이미지 수집 → 자동 라벨링 → 팀 검수 웹앱 → 인덱스/추천"** 파이프라인의 템플릿이다.
구도/포즈 데이터셋에 맞춰 만들었지만, **데이터셋 고유 값은 설정 파일 3개에 격리**돼 있어
다른 이미지셋(예: 음식, 제품, 인테리어)에도 기능 누락 없이 적용할 수 있다.

## 1. 갈아끼우는 것 (설정 파일 몇 개)

도메인은 `datasets/<이름>/` 안에 산다. 코드(`scripts/`)와 설정(`config/`)은 공유한다.

| 파일 | 역할 | 새 데이터셋에서 |
|---|---|---|
| **`datasets/<이름>/taxonomy.yaml`** | **라벨 스키마 SSOT** — 축·코드·배타그룹·의미역할·큐레이션 | **필수** |
| **`datasets/<이름>/prompts.yaml`** | CLIP zero-shot 프롬프트 (코드마다 영문 한 줄) | **필수** |
| `datasets/<이름>/queries.yaml` | 크롤 검색어 (축·코드별) | 크롤을 쓸 때만 |
| `datasets/<이름>/seeds.csv` | Pinterest 시드 핀 | 시드 수집 시 |
| `config/firebase.json` | 실시간 협업 검수 (없으면 로컬 저장으로 동작) | 팀 검수 시 |

데이터셋은 `DATASET_DIR` 로 고른다 (기본값 `datasets/composition`):

```bash
DATASET_DIR=datasets/food python scripts/review/app.py
DATASET_DIR=datasets/food bash scripts/review/deploy.sh   # → sites/food/ 로 배포
```

**`file_path` 는 데이터셋 루트 기준 상대경로다.** 저장소 루트 기준으로 두면 데이터셋을 폴더째
옮기는 순간 전부 깨진다 — 실제로 `datasets/composition/` 으로 옮기며 드러난 문제다.
경로 결합은 `common.image_path()` / `common.raw_path()` 한 곳에만 있다. 새 스크립트를 쓸 때도
`common.ROOT / row["file_path"]` 로 직접 붙이지 마라.

### ⚠ 아직 사람이 새로 써야 하는 것 — `prompts.yaml`

> **축 코드마다 영문 zero-shot 문장이 하나씩 필요하다.**
> CLIP 은 한글 표시명("카페")으로는 성능이 안 나오고, 코드명을 기계적으로 문장화해도
> 품질이 급락한다. **이것만은 도메인마다 사람이 쓴다.**
>
> 하지만 **코드 안에 있을 이유는 없다.** `prompts.yaml` 에 있으므로 `scripts/` 는 안 건드린다.
> (`check_consistency.py` 가 taxonomy 와 1:1 인지 확인하고 누락된 축을 지목해준다)

```yaml
# prompts.yaml
axes:
  species:                       # taxonomy 의 축·코드와 1:1
    V01_monstera: "a photo of a monstera deliciosa houseplant with split leaves"

filters:                         # 이진 판정 → 점수/거절 (축이 아니다)
  subject_visible:               # **'이 도메인의 주 피사체'** 가 보이는가 (사람이 아니어도 된다)
    positive: "a photo clearly showing a potted houseplant"
    negative: "a photo with no plants in it"
    reject: {min: 0.5, note: "식물 미확인"}     # 생략하면 점수만 기록
  quality:
    positive: "a high quality sharp photograph"
    negative: "a low quality blurry photo"
    field: quality_score
    scale: {lo: 2, hi: 5}                        # 확률 0~1 → 2~5 점
```

`subject_visible` 이 인물 전용이 아닌 것이 핵심이다. 예전엔 *"사람이 안 보이면 버린다"* 가
`auto_label.py` 에 박혀 있어서, **인물 없는 도메인의 데이터가 전량 rejected 됐다.**
이제 그 도메인의 `prompts.yaml` 이 자기 피사체를 정의하고 거절 조건도 스스로 정한다.

이 문서는 오랫동안 *"scripts/ 는 건드리지 않는다"* 고 주장했다. **거짓이었다.**
인물과 무관한 taxonomy 를 넣으면 검수앱은 로드 즉시 TypeError 로 죽고, rank/auto_label/
validate/check_consistency 는 KeyError 로 죽었다. 아무도 몰랐던 이유는 **아무도 시험해보지
않았기 때문**이다. 지금은 `tests/test_template.py` 가 매번 확인한다 —
가짜 도메인(관엽식물, 인물 흔적 0)으로 전 스크립트와 검수앱을 실제로 돌려본다.

**문서의 주장은 테스트로 지켜야 한다.**

## 2. `taxonomy.yaml` 구조

```yaml
_axes:                    # 축 메타 → common.AXES / 앱 필터·편집창이 자동 생성됨
  - {key: where,        name: 장소,     multi: true,  composition: true,  mode: assisted}
  - {key: shot_size,    name: 프레이밍, multi: false, composition: true,  mode: assisted}
  - {key: expression,   name: 표정,     multi: false, composition: false, mode: human}
  - {key: person_count, name: 인원,     multi: false, composition: false, mode: derived}
  # multi:true  → CSV 에서 ';' 로 여러 코드 연결 (앱에서 다중 선택)
  # composition → 커버리지·부족라벨 전광판의 집계 대상 축
  # mode        → 누가 채우는가. check/axis_audit.py 의 판정을 그대로 옮긴다.
  #    assisted 자동라벨+사람검수(기본) · human 사람 전용(AI 제안 없음)
  #    derived  시스템이 채우고 사람은 안 본다(검수 화면에 칩이 안 뜸)
  #             — 축인 줄 알았는데 사실 '큐레이션 조건'이었던 것들이 여기로 온다

_coverage_min: 10         # 코드당 목표 최소 장수 (부족 라벨 기준)

_exclusive:               # (선택) 같은 줄에서 하나만 성립하는 코드 — 앱이 자동 해제
  pose_action:
    - [P01_sitting, P10_standing, P02_leaning]

_semantic:                # (선택) 포즈캠이 라이브 프레임과 매칭할 때 쓰는 의미 역할
  facing:   {axis: camera_style, front: S14_front_view, side: S11_side_view, back: S10_back_view}
  framing:  {axis: shot_size, full: F01_full_body, half: F02_half_body, closeup: F03_closeup}

where:                    # 축별 코드 → 표시명
  W01_cafe: "카페"
```

**`_semantic` 을 생략하면** 포즈캠의 방향/프레이밍 게이트만 비활성화되고 **나머지는 전부 정상 동작**한다
(색감·시각 유사·라벨 가중 추천은 그대로). 인물이 없는 데이터셋(제품·풍경)에서는 자연스럽게 생략하면 된다.

## 3. 인물 의존 기능 (있으면 켜지고, 없으면 자동 비활성)

| 기능 | 의존 | 인물 없는 데이터셋에서 |
|---|---|---|
| 포즈 인덱스·닮은 컷·겹쳐보기 | BlazePose 키포인트 | `pose_match.py` 산출물이 비면 자동 비활성 |
| 포즈캠 (카메라 추천) | 위 + `_semantic` | 시각/색감 매칭만으로 동작(🔵 분위기 매칭) |
| 시각 유사(MobileNet)·색감 | 없음 (범용) | **그대로 동작** |
| 검수·필터·전광판·대시보드 | `taxonomy.yaml` | **그대로 동작** |

## 4. 적용 순서

`/bootstrap` (Claude Code 스킬) 을 쓰면 0~2 단계 초안을 이미지를 보고 대신 써준다.

```bash
# 0) 도메인 선언
mkdir -p datasets/<이름>/{data/00_raw,annotations}
vi datasets/<이름>/taxonomy.yaml    # 축·코드 정의 (필수)
vi datasets/<이름>/prompts.yaml     # CLIP 프롬프트 (필수)
vi datasets/<이름>/queries.yaml     # 크롤 검색어 (크롤 사용 시)
export DATASET_DIR=datasets/<이름>

# 1) 수집 → 정제 (크롤 대신 기존 이미지를 data/00_raw 에 넣어도 됨)
python scripts/collect/crawl.py
python scripts/curate/dedup.py            # pHash 중복·저품질 제거
python scripts/curate/promote.py   # 승인 → curated
python scripts/curate/resize.py           # 512/768/1024

# 2) 자동 라벨링 (CLIP zero-shot)
python scripts/label/auto_label.py

# 3) 인덱스 (범용)
python scripts/index/build_index.py    # CLIP 임베딩·pHash·품질
python scripts/index/embed_map.py            # 2D 좌표·군집·kNN
bash scripts/index/build_signal.sh dino 'dinov2-small' fp16 224   # 시각 인덱스(주 신호)
python scripts/index/color_index.py    # Lab 색감 인덱스
python scripts/index/pose_match.py           # (인물 데이터셋만) 포즈 인덱스

# 4) 검수 앱 생성 → 배포
python scripts/review/app.py
bash scripts/review/deploy.sh          # sites/<이름>/ 로 복사 후 vercel --prod

# 5) 검수 반영 루프
bash scripts/review/sync_reviews.sh
```

## 5. 검증 (교체 후 반드시)

### 5.1 구조 검증 — 깨지지 않았나
```bash
python scripts/check/check_consistency.py   # taxonomy ↔ 프롬프트 ↔ CSV ↔ 인덱스 정합성
python -m pytest tests/ -q            # 파이썬 18케이스
node tests/js/verify_app.js           # 앱 런타임 16케이스
node tests/js/bench_posecam.js        # 추천 품질 벤치(인물 데이터셋)
```

### 5.2 감사 — **taxonomy 초안이 좋은가** (검수 30장 이상 쌓인 뒤)

새 도메인의 taxonomy 초안은 **반드시 틀린다.** 어디가 틀렸는지는 사람이 아니라 데이터가 말한다.
아래 세 도구는 축 코드를 하나도 모른다 — 도메인이 바뀌어도 그대로 돈다.

```bash
python scripts/check/axis_audit.py --json     # 축이 살아있나?
python scripts/check/tune_labels.py --write   # 라벨러 임계값을 정답으로 측정 → label_profile.json
python scripts/check/labeler_score.py         # 라벨러를 고쳤다면 정말 나아졌나? (축별)
```

**`axis_audit.py` 가 내리는 판정과 처방** — 그대로 `taxonomy._axes.mode` 로 옮긴다:

| 판정 | 뜻 | 처방 |
|---|---|---|
| 🟢 `healthy` | 리프트 ≥ 15%p | `mode: assisted` — 자동라벨 + 사람 검수 |
| 🟡 `weak` | 리프트 10~15%p | 프롬프트 보강 · 코드 병합 |
| 🔴 `model_blind` | 분포는 고른데 모델이 못 읽음 | `mode: human` — 사람 전용(AI 제안 없음) |
| 🔴 `dead` | 한 코드가 80%↑ = 정보량 0 | `mode: derived` — **축이 아니라 큐레이션 조건이었다** |

추가로 잡아내는 것:
- **`never_predicted`** — 모델이 한 번도 출력하지 않은 코드. taxonomy 에 코드를 추가했는데
  `auto_label.py` 가 **이미 라벨된 이미지를 스킵**해서 안 붙은 것이다 → `scripts/label/relabel.py`
- **조건부 축** — 다른 축의 값에 따라 답할 수 있고 없고가 갈리는 축.
  (실측: 장소는 전신샷 79% / 얼빡샷 55% — 얼빡샷엔 배경이 안 보인다. 모델을 바꿔도 해결 안 된다)
- **혼동쌍 · 죽은 코드 · 과다예측 코드**

### 5.3 큐레이션·추천 감사 (Phase 1)

```bash
python scripts/check/gate_audit.py --write     # 뭘로 걸러야 하나 → curation_profile.json
python scripts/check/signal_audit.py --write   # 어떤 신호가 쓸 만한가 → signal_profile.json
node   scripts/check/tune_camw.js --write      # 추천 가중치 → signal_profile.json 의 live 블록
```

**`gate_audit.py`** 는 사람의 keep/discard 판정으로 **차단(gate)과 정렬(rank)을 갈라준다.**
둘은 다른 신호가 맡아야 한다 — 바꿔 쓰면 좋은 사진이 같이 죽는다.

| 역할 | 성질 | 실측 (사람 판정 892건) |
|---|---|---|
| **차단** | 임계 하나로 '버릴 것'만 골라 죽는 신호 | 객체검출: 버릴 것 48.3% 차단 / 살릴 것 2.1% 손실 |
| **정렬** | 점수가 연속적이라 자를 경계가 없는 신호 | CLIP 유사도: 게이트 통과분 74.0% → 83.1% |
| ❌ | CLIP 을 **차단**에 쓰면 | 버릴 것 69% 차단하는 대신 **살릴 것 37%도 죽는다** |

결과는 `taxonomy._curation` 의 `gates` / `rank` 에 옮긴다. 파이프라인 전체 효과:
**keep 밀도 60.1% → 74.0% → 83.1%, 검수 노동 21% 감소.**

### 5.4 ⚠ 대리 목표(proxy)로 최적화하지 말 것 — **이 프로젝트에서 네 번 데였다**

| # | 프록시가 한 말 | 실제 파이프라인의 답 |
|---|---|---|
| 1 | `signal_audit`: "색감 가중치는 0 이 최적" | **0.46 이 최적** (정반대) |
| 2 | 조사 문서: "q4f16(14MB) 권장" | fp32 인덱스와 짝이면 **코사인 0.86 — 붕괴** |
| 3 | '교집합≥1' 라벨 채점 | 코드를 많이 뱉을수록 이긴다 → **micro-F1 이어야** |
| 4 | `gate_audit`: "clip 가중치를 0.79 로 낮춰라" | **3.0 이 최적** (정반대) |

전부 **"내가 재기 편한 것"을 목표 함수로 삼았다가** 배신당한 경우다.
목표 함수는 **"내가 실제로 개선하려는 것"** 이어야 한다.

- 추천 가중치 → `camMatch` 를 그대로 호출하며 파라미터만 주입 (`tune_camw.js`)
- 검수 순서 가중치 → 실제 `final_score` 로 정렬해 상위 절반의 keep 밀도를 잰다 (`gate_audit.py`)
- 라벨러 → 사람 정답에 대해 축별 micro-F1 (`labeler_score.py`)



`signal_audit.py` 는 **임베딩끼리의 거리**로 가중치를 맞춘다. 하지만 실제 추천(`camMatch`)은
하드필터를 먼저 걸고, 다른 정규화를 쓰고, 페널티·보너스를 더한다. 오프라인 최적값을
그대로 넣었더니 **실제로는 나빠졌다**:

| | 종합 | 프레이밍 | 색감 가중치 |
|---|---|---|---|
| 손으로 찍음 | 70.2% | 68.4% | 0.22 |
| 오프라인 최적 (`signal_audit`) | **60.8% ❌** | **65.2% (합격선 미달)** | **0.00** |
| **실제 파이프라인 최적 (`tune_camw`)** | **71.2% ✅** | **73.1%** | **0.46** |

오프라인 지표는 "색감 가중치 0이 최적"이라 했지만, **실제 파이프라인에서는 0.46이 최적**이었다.
정반대다. **벤치가 곧 목표 함수다** — 앱에 넣을 값은 반드시 실제 코드 경로를 통과시켜 찾는다.

`signal_audit.py` 의 쓸모는 따로 있다: **"어떤 신호를 갈아끼워야 하는가."**
실측(시각 39.6% / 색감 7.6% / 포즈 6.2%)이 말해주듯 시각 신호가 전부를 지고 있으므로,
지렛대는 가중치 조정이 아니라 **시각 신호 교체**(MobileNet → DINOv2, +8.6%p)다.

### 5.5 시각 신호 추가 (새 모델 붙이기)

```bash
bash scripts/index/build_signal.sh dino 'dinov2-small(transformers.js)'
python scripts/check/signal_audit.py --write     # 새 신호가 쓸 만한가
python scripts/check/degrade_probe.py            # 저화질 입력에서도 버티는가
node   scripts/check/tune_camw.js --write        # 가중치 재측정
python scripts/review/app.py                     # 인덱스는 자동 발견된다
```

**임베더는 반드시 Node(브라우저와 같은 런타임)로 돌린다.** 갤러리 벡터와 라이브 벡터가
같은 공간에 있어야 거리를 비교할 수 있는데, 전처리가 조금만 달라도 어긋난다.
(Python transformers 로 뽑은 벡터는 Node transformers.js 와 코사인 0.90~0.98 로 어긋났다)

**dtype 은 인덱스와 브라우저가 반드시 같아야 한다.** 양자화 오차를 양쪽이 똑같이 겪어야
같은 공간에 남는다. `<signal>_index.json` 에 `dtype`·`input_size` 를 기록하고 앱이 그대로 따른다.
**안 맞으면 폴백하지 말고 그 신호를 꺼라** — 조용히 망가지는 것보다 낫다.

| 조합 | CLIP 재현율 | 다운로드 | 판정 |
|---|---|---|---|
| 인덱스 fp32 + 브라우저 **q4f16** | — (코사인 0.86) | 14MB | ❌ **다른 공간 — 붕괴** |
| 인덱스 fp16 + 브라우저 fp16 | 46.1% | 44MB | ✅ |
| **인덱스 q4f16 + 브라우저 q4f16** | **44.9%** (−1.2%p) | **14MB** | ✅ **채택** |
| int8 | — | — | ❌ onnxruntime-web 에 ConvInteger 미구현 |

즉 **"q4f16 은 위험"이 아니라 "dtype 불일치가 위험"** 이었다. 양쪽을 맞추면 3배 작아진다.

**WASM 멀티스레드를 켜라 (2배 빨라진다).** `vercel.json` 에 COOP/COEP 헤더를 넣으면
`crossOriginIsolated` 가 켜지고 SharedArrayBuffer 로 멀티스레드가 동작한다.
`COEP: credentialless` 를 쓰면 CDN 스크립트(tfjs·MediaPipe·Firebase)를 깨지 않는다 — 실측 확인.

```json
{"headers":[{"source":"/(.*)","headers":[
  {"key":"Cross-Origin-Opener-Policy","value":"same-origin"},
  {"key":"Cross-Origin-Embedder-Policy","value":"credentialless"}]}]}
```

**추출 간격은 기기가 정하게 하라.** 고정값을 쓰면 빠른 기기(WebGPU 40ms)도 오래 기다리고
느린 기기(WASM 400ms)는 큐가 밀린다. 실제 추론 시간을 재서 그 2배로 맞춘다(`dinoGap()`).

**WebGPU 는 `navigator.gpu` 존재만으로 판단하면 안 된다.** 어댑터를 실제로 요청해 성공할
때만 쓰고, 아니면 WASM 으로 떨어뜨린다(헤드리스·GPU 없는 기기에서 로드가 통째로 실패한다).

**`degrade_probe.py` 를 반드시 돌린다.** 벤치는 '라이브 추출이 완벽하다'고 가정한다
(갤러리 임베딩을 그대로 라이브 입력으로 재사용). 실제 카메라는 그렇지 않으므로,
열화 입력에서 이웃을 얼마나 유지하는지 따로 재야 한다.
실측: DINOv2 76.7% / MobileNet 60.1% — DINOv2 가 품질도 강인함도 앞선다.

### 5.6 라벨러를 고쳤을 때의 철칙

**축별로 채점하고 축별로 반영한다.** 전체 평균으로 판단하면 안 된다 — 실측에서 한 번의 수정이
`camera_style` +7.3%p / `pose_action` −7.5%p 로 갈렸고, 전체 평균은 +1.3%p 라 "개선"으로 보였다.

```bash
python scripts/check/labeler_score.py                    # 축별 F1 비교 → 나아진 축만 알려준다
python scripts/label/relabel.py --axes camera_style      # 그 축만 반영
```

멀티축은 **반드시 micro-F1** 으로 잰다. '교집합≥1' 로 세면 코드를 많이 뱉을수록 점수가 올라
(전부 찍으면 100%) 과다 예측이 보상받는다.

## 6. 알려진 데이터셋 종속 지점 (교체 시 확인)

1. **`scripts/label/clip_label.py`** — 코드별 영문 zero-shot 프롬프트. 축을 바꾸면 여기도 작성 필요.
2. **`scripts/index/descriptor/pose_descriptor.py`** — 인물 포즈 전용(21d). 인물 없는 데이터셋에선 미사용.
3. **`scripts/index/visual_embed/`** — MobileNet 임베딩 빌드(Node). 이미지 경로만 주면 도메인 무관.

## 7. 구조

```
composition_dataset/
├─ README.md · LICENSE(MIT)
├─ config/       firebase.json · models/{rtdetr, pose_landmarker.task}   ← 코드의 설정
├─ datasets/     ← 도메인이 사는 곳. 데이터셋마다 하나씩, 자기완결적이다.
│   ├─ composition/   taxonomy·prompts·queries·seeds + data/ + annotations/
│   └─ food/          (같은 구조 — 인물 개념 0. 템플릿의 도그푸딩)
├─ sites/        ← 배포 루트. 데이터셋마다 하나씩(.vercel 링크 + COOP/COEP 헤더)
│   ├─ composition/   → reviewsite-five.vercel.app
│   └─ food/          → reviewsitefood.vercel.app
├─ scripts/      ← 코드. 도메인 이름이 한 글자도 없어야 한다.
│   ├─ common.py                      ← 경로·CSV·taxonomy 파생 축 (SSOT)
│   ├─ collect/   crawl · seeds · autoseed · sources/                     ① 수집
│   ├─ curate/    dedup · rank · promote · resize · make_splits · cleanup ② 정제
│   ├─ label/     auto_label · clip_label · detect · enrich · relabel     ③ 라벨
│   ├─ index/     build_index · embed_map · pose_match · color_index ·
│   │             embed_pca · build_signal.sh · dino_embed/               ④ 인덱스
│   ├─ review/    app.py + ui/{index.html,app.css,app.js} ·
│   │             pull/apply_review · backup_reviews ·
│   │             sync_reviews.sh · deploy.sh                             ⑤ 검수앱·반영
│   └─ check/     check_consistency · validate ·
│                 axis_audit · gate_audit · signal_audit ·
│                 tune_labels · tune_camw · labeler_score · detector_score ⑥ 검증·감사
├─ tests/        pytest 27 (가짜 도메인 회귀 포함) · js 4스위트
└─ docs/
```

**폴더가 곧 파이프라인 순서다.** 새 데이터셋에서 건너뛸 단계는 폴더 단위로 스킵하면 된다:
- 이미 이미지가 있다 → `collect/` 스킵 (`data/00_raw` 에 직접 투입)
- 인물이 없다 → 아무것도 안 해도 된다. `_semantic` 에 facing/framing/count 가 없으면
  pose_match·enrich·포즈캠이 **스스로** 꺼진다 (선언이 곧 능력이다)
- 팀 검수 없이 혼자 → `config/firebase.json` 생략 (앱이 localStorage 로 동작)

### UI 분리 (`scripts/review/ui/`)
`app.py` 는 **데이터 조립(180줄)** 만 하고, 프론트엔드는 `ui/` 3개 파일이다.
빌드 시 `index.html` 의 `/*CSS*/`·`/*JS*/`·`/*DATA*/` 자리에 주입 → 산출물은 단일 self-contained HTML.
**도메인이 바뀌어도 파이썬을 건드리지 않고 UI만 교체**할 수 있고, 반대도 성립한다.
