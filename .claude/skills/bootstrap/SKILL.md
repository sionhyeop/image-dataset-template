---
name: bootstrap
description: Use when someone has a folder of images and wants to turn it into a labeled dataset with this template — drafts taxonomy.yaml/prompts.yaml/queries.yaml for a new domain by actually looking at the images, then validates the draft against the data. Trigger: /bootstrap
---

# 새 도메인 부트스트랩

이미지 폴더 하나 + "이걸로 뭘 하고 싶은지" 한 문장에서 출발해
`datasets/<이름>/{taxonomy,prompts,queries}.yaml` 초안을 만들고 **데이터로 검증**한다.

API 키가 필요 없다. 이미지를 보는 건 너(Claude Code)다 — `Read` 로 이미지를 열면 실제로 보인다.

## 대전제

**초안은 틀린 게 정상이다.** 이 스킬의 산출물은 "정답 taxonomy" 가 아니라
*감사기에 넣어볼 수 있는 가설*이다. 사람이 30장쯤 검수한 뒤 `axis_audit.py` 가
"이 축은 CLIP 이 못 읽는다(dead)" 고 판정하면, 그때 축을 접거나 사람에게 넘긴다.

절대 하지 마라:
- 이미지를 안 보고 도메인 상식만으로 축을 짓는 것 (그게 프록시 최적화다)
- 축을 10개 넘게 만드는 것 — 검수 비용은 축 수에 비례한다. 4~6개로 시작해라.
- `scripts/` 를 수정하는 것 — 수정해야 한다면 그건 템플릿의 버그다. 사람에게 보고해라.

## 순서

### 1. 물어본다 (한 번에 하나씩)

- **데이터셋 이름** (영문 소문자, `datasets/<이름>/` 이 된다)
- **목적 한 문장** — "이 이미지들로 뭘 만들 건가?" 이게 taxonomy 의 방향을 정한다.
  예: "카페 음식 사진의 구도 레퍼런스" / "제품 사진의 배경·조명 유형 분류"
- **이미지 위치** — 이미 있는 폴더 경로.

### 1-B. 이미지가 없으면 — **임시 taxonomy 부터**

크롤로 모을 거라면 순서가 하나 더 있다. `crawl.py` 는 `queries.yaml` 의 첫 축으로 도는데,
그 카테고리 코드는 **taxonomy 에 이미 존재해야** 한다(`auto_label` 이 코드를 검증한다).
그러니 "queries 만 쓰고 크롤" 은 성립하지 않는다.

수집 축 하나만 담은 **임시 taxonomy** 를 먼저 쓰고, 크롤한 뒤, 이미지를 보고 나머지 축을 짓는다:

```bash
mkdir -p datasets/<이름>/{data/00_raw,annotations}
# taxonomy.yaml — 수집 축 하나만 (예: space). "임시"라고 주석에 박아둬라.
# queries.yaml  — 그 축의 코드별 검색어
DATASET_DIR=datasets/<이름> python scripts/collect/crawl.py --source bing --limit 40
DATASET_DIR=datasets/<이름> python scripts/curate/dedup.py
```

이 단계에서 축을 다 지으려 하지 마라. **아직 이미지를 한 장도 안 봤다.**

### 2. **이미지를 본다** — 건너뛰지 마라

폴더에서 **최소 20장**을 카테고리별로 고르게 뽑아 `Read` 로 실제로 열어본다.
(`ls` 로 목록만 보고 넘어가면 안 된다. 파일명은 거짓말을 한다.)

보면서 메모해라:
- 무엇이 **반복해서 다르게** 나타나는가? → 축 후보 (변이가 없으면 축이 아니다)
- 무엇이 **거의 모든 사진에 똑같이** 있는가? → 축이 아니다. 상수는 라벨링할 가치가 없다
- 한 사진에 **동시에 여러 개** 성립할 수 있는가? → `multi: true`
- 한 축의 값들이 **서로 배타적**인가? → `_exclusive` 그룹
- 주 피사체는 무엇인가? → `filters.subject_visible`, `_curation.detect.classes`

보고 나서 **본 것에 근거해 결정하라.** 실제로 이 스킬을 인테리어 도메인에 돌렸을 때
이미지를 봤기 때문에만 알 수 있었던 것들:
- 화면비가 16:9·3:2·1:1·3:4 로 전부 나온다 → "인테리어는 가로" 라는 상식이 틀렸다. aspect 점수를 완만하게.
- **3D 렌더·AI 생성물이 절반 가까이 섞인다** → 도메인 고유의 오염. 축(`render`)으로 세울 가치가 있다.
- 검색어가 거짓말한다 — "카페 인테리어" 결과에 사무실이 섞여 있었다.

### 3. 초안 세 개를 쓴다

참조 구현 세 개가 있다. 가까운 것을 베껴라:
- `datasets/food/` — 인물 개념 0. 주 피사체가 **물건**인 도메인.
- `datasets/interior/` — 주 피사체가 **공간**인 도메인(가구를 대리 지표로 검출).
- `datasets/composition/` — 인물 전용 기능(포즈캠)이 켜진 예.

**taxonomy.yaml** — 축·코드와 세 개의 메타 블록:
- `_axes`: `{key, name, multi, composition, mode}` — 처음엔 전부 `mode: assisted`
- `_semantic`: 의미 역할. **`facing`/`framing`/`count` 를 선언하면 인물·포즈 스택이 켜진다.**
  사람 도메인이 아니면 절대 넣지 마라 (선언이 곧 능력이다).
  사람 도메인이라도 처음엔 `place`/`pair` 만 넣고 시작해도 된다.
- `_curation`: `purpose`(목적 문장 그대로), `intake`(min_side/max_aspect/phash_hamming),
  `score.aspect`(이 도메인의 표준 비율 — 음식은 정사각, 인물은 세로다. **틀리기 쉬우니 본 걸로 판단해라**),
  `detect.classes`(COCO 클래스명), `gates: []`, `weights`
  - ⚠ `gates` 와 `weights` 는 **손으로 찍지 마라.** 검수가 쌓인 뒤 `gate_audit.py` 가 측정해서 채운다.
    초안에는 `gates: []` 와 균일 가중치를 두고 넘어가라.

**prompts.yaml** — 코드마다 CLIP 프롬프트 한 줄 (영어, `"a photo of ..."` 형태).
`filters.subject_visible` 은 "사람이 보이는가" 가 아니라 **"이 도메인의 주 피사체가 보이는가"** 다.
이걸 잘못 쓰면 전량 rejected 된다 (음식 도메인에서 실제로 겪은 사고다).

**queries.yaml** — 축 코드별 수집 검색어. 기존 이미지만 쓸 거면 최소한만.

### 4. 검증한다 — **여기가 본체다**

```bash
export DATASET_DIR=datasets/<이름>

# ① 구조: taxonomy ↔ prompts ↔ 앱 ↔ CSV 가 맞물리는가
python scripts/check/check_consistency.py

# ② 라벨러가 실제로 돌아가는가 (소량 — 카테고리별로 고르게 표본을 뽑는다)
python scripts/label/auto_label.py --limit 30
```

②가 **진단을 직접 찍어준다.** 그 세 줄을 읽어라:
- **전량 rejected 인가?** → `filters.subject_visible` 이 틀렸다. 프롬프트를 고쳐라.
- **0회 코드가 있나?** → CLIP 이 못 찾거나 데이터에 없는 코드다. 프롬프트를 고치거나 코드를 지워라.
- **한 코드가 90% 이상(💀)인가?** → 축에 정보가 없다. 쪼개거나 접어라.

> ⚠ **표본으로 축의 생사를 판정하지 마라.** `--limit` 은 카테고리별로 고르게 뽑지만
> 그래도 30장은 30장이다. 💀 판정이 뜨면 **전량 라벨링(`--limit` 없이) 후 다시 보라.**
> 인테리어 부트스트랩에서 30장 표본이 `space: 거실 97%` 라고 해서 축이 죽은 줄 알았는데,
> 208장 전량에서는 6개 공간이 14~18% 로 고르게 나왔다. 표본이 편향됐던 것뿐이다.

고쳤으면 ②를 다시 돌려라. 이 루프가 초안의 절반을 걷어낸다.

### 5. 넘긴다

```bash
python scripts/curate/promote.py --auto && python scripts/curate/resize.py
python -m scripts.index.build_index && python scripts/index/embed_map.py
python scripts/review/app.py && bash scripts/review/deploy.sh
```

그리고 사람에게 **이 말을 반드시 해라**:

> 초안이 나왔고 앱이 떴습니다. 하지만 **지금 라벨은 가설입니다.**
> 30~50장을 검수하신 뒤 `python scripts/check/axis_audit.py` 를 돌리면,
> 어떤 축이 실제로 쓸 만한지(healthy/weak/model_blind/dead) 데이터가 알려줍니다.
> 거기서부터 taxonomy 를 고치는 게 진짜 시작입니다.

## 회귀 방지

작업이 끝나면 반드시:

```bash
python -m pytest -q                                    # 27개 — 가짜 도메인 회귀 포함
DATASET_DIR=datasets/<이름> node tests/js/verify_app.js  # 새 도메인에서 앱이 실제로 도는가
```

`tests/test_template.py` 가 깨졌다면 `scripts/` 에 도메인 이름을 박아 넣은 것이다.
설정으로 밀어내라.

**새 도메인에서 `verify_app.js` 를 꼭 돌려라.** pytest 는 앱이 *로드되는지*만 보고,
`verify_app.js` 는 앱이 *맞게 도는지* 본다. 인테리어 부트스트랩에서 이 테스트가
앱의 하드코딩 두 개를 잡아냈다(대시보드 타일의 "구도 카테고리 ≥10" — 도메인 이름과
임계값이 둘 다 거짓이었다).
