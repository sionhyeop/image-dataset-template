# 이 저장소에서 일하는 법

이미지 폴더 → 라벨링된 데이터셋 + 팀 검수 웹앱. **도메인은 `datasets/<이름>/` 의 YAML 3개로 갈아끼운다.**
서버는 0대다: 무거운 계산은 오프라인, 앱은 self-contained HTML 한 장.

사용자가 "내 이미지로 시작하고 싶다"고 하면 → **`/bootstrap` 스킬을 먼저 호출하라.**

---

## 절대 규칙 세 개

### 1. `scripts/` 에 도메인 이름을 쓰지 마라
새 도메인은 **설정만** 바꾼다. `scripts/` 를 고쳐야 한다면 그건 템플릿의 버그다 — 사용자에게 보고하라.

축 이름·코드는 `taxonomy.yaml` 에서 파생한다: `common.LABEL_AXES` · `common.MULTI_AXES` ·
`common.COMP_AXES` · `common.load_semantic()`. 리터럴로 `"where"`, `"pose_action"` 같은 걸 쓰면
CI 가 실패한다(`.github/workflows/ci.yml`).

경로도 마찬가지다. `common.image_path(row)` / `common.raw_path(row)` 를 써라.
`common.ROOT / row["file_path"]` 로 직접 붙이지 마라 — **`file_path` 는 데이터셋 루트 기준**이다.

### 2. 게이트·가중치를 손으로 찍지 마라
`taxonomy._curation.gates` 와 `weights` 는 **감사기가 측정해서 채운다.**
초안에는 `gates: []` 와 균일 가중치를 두고 넘어가라. 검수가 30~50장 쌓이면:

| 감사기 | 무엇을 정하나 |
|---|---|
| `scripts/check/axis_audit.py` | 축이 살아있나 (healthy/weak/model_blind/dead) → 자동라벨에 맡길지 사람에게 맡길지 |
| `scripts/check/gate_audit.py` | 무엇으로 **차단**하고 무엇으로 **정렬**할지 → `curation_profile.json` |
| `scripts/check/signal_audit.py` | 각 시각 신호의 추천 가중치 → `signal_profile.json` |

### 3. ⚠ 대리 목표(proxy)로 최적화하지 마라 — 이 프로젝트에서 **네 번** 데였다

| 프록시가 한 말 | 실제 파이프라인이 한 말 |
|---|---|
| signal_audit: 색 가중치 0 | 실제 랭킹에선 0.46 |
| 리서치 문서: q4f16 은 안전 | dtype 불일치로 코사인 0.86 붕괴 |
| 라벨 채점: '교집합 ≥1이면 정답' | 많이 찍을수록 점수가 오름 → micro-F1 이어야 함 |
| gate_audit: 리프트 비율 clip=0.79 | 실제로 정렬해 보니 3.0 |

**벤치가 곧 목표 함수다.** 최적화는 반드시 *진짜 파이프라인을 통과시켜서* 해라
(`tests/js/bench_posecam.js`, `scripts/check/tune_camw.js` 가 그 방식이다).

---

## 명령

```bash
# 파이썬은 항상 .venv 로
.venv/bin/python scripts/review/app.py

# 데이터셋 선택은 환경변수 하나 (기본 datasets/example_plants)
DATASET_DIR=datasets/food .venv/bin/python scripts/review/app.py
export DATASET_DIR=datasets/<이름>          # 세션 내내 쓸 거면
```

파이프라인은 폴더 순서 그대로다:
`collect/` → `curate/` → `label/` → `index/` → `review/` → `check/`

```bash
python scripts/collect/crawl.py             # Bing/Pinterest 검색 (키 불필요)
python scripts/collect/hf_import.py --search "<주제>"   # 또는 HF 에서 라벨째 가져오기
python scripts/curate/dedup.py
python scripts/label/auto_label.py          # CLIP zero-shot. **진단을 찍어준다 — 그걸 읽어라**
python scripts/curate/promote.py --auto && python scripts/curate/resize.py
python -m scripts.index.build_index && python scripts/index/embed_map.py
python scripts/review/app.py && bash scripts/review/deploy.sh
```

## 검증 (코드를 고쳤으면 반드시)

```bash
.venv/bin/python -m pytest -q                          # 29개
.venv/bin/python scripts/check/check_consistency.py    # taxonomy↔프롬프트↔앱↔CSV↔parquet
node tests/js/verify_app.js                            # 앱 JS 런타임 (브라우저 불필요)
DATASET_DIR=datasets/food node tests/js/verify_app.js  # 다른 도메인에서도 도는가
```

`tests/test_template.py` 가 깨졌다면 `scripts/` 에 도메인 이름을 박은 것이다. 설정으로 밀어내라.

---

## 이 저장소에서 실제로 밟은 지뢰 (같은 실수 반복 금지)

- **이미지를 보지 않고 판단하지 마라.** 파일명은 거짓말한다. 라이선스만 보고 24장을 받았더니
  13장이 스캔된 고서였고, Bing 인테리어 결과는 **절반이 AI 렌더**였다. 컨택트시트로 **눈으로** 확인하라.
- **게이트를 좁게 잡으면 진짜 데이터도 같이 죽는다.** '초록 픽셀 10%' 조건 하나가 고무나무를
  전멸시켰다(적자색 품종). 차단은 최소한만, 나머지는 정렬로 해결하라.
- **표본으로 축의 생사를 판정하지 마라.** `--limit 30` 이 "거실 97% — 축이 죽었다"고 했지만,
  전량 208장에서는 6개 공간이 14~18% 로 고르게 나왔다. 표본이 편향됐을 뿐이었다.
- **빈 테스트는 통과하는 게 아니라 아무것도 안 보는 것이다.** 픽스처가 0행이던 시절,
  "앱이 로드된다" 테스트는 통과했지만 **앱이 틀리게 도는** 버그를 하나도 못 잡았다.
- **조용한 폴백보다 시끄러운 실패가 낫다.** `_axes` 가 없으면 인물 축으로 되돌아가던 폴백이
  새 도메인 사용자를 며칠씩 헤매게 했다.

## 인물 의존 기능은 선언으로 켜진다

별도 플래그가 없다. `taxonomy._semantic` 에 `facing`/`framing`/`count` 가 **있으면**
포즈캠·인물 속성 백필이 켜지고, 없으면 조용히 꺼진다. **선언이 곧 능력이다.**
사람 도메인이 아니면 이 셋을 절대 넣지 마라.

## 더 읽기

`docs/TEMPLATE.md`(새 도메인 적용 전문) · `docs/DATASET.md` · `docs/FIREBASE.md` ·
`docs/crawl/`(수집 방법론)
