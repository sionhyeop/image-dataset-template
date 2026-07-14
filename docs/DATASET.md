# 구도 레퍼런스 데이터셋 — 구조와 보는 법

인물 사진을 **구도(장소·포즈·프레이밍·앵글) + 인물 속성(성별·인원·표정)** 7축으로
분류한 레퍼런스 데이터셋. "파일은 평평하게, 의미는 메타데이터로" 원칙을 따른다.

## 한눈에 보기 (여기 하나만 열면 됩니다)

**`data/04_samples/index.html`** — 통합 앱 하나. 헤더 탭으로 전부 이동:

| 탭 | 용도 |
|---|---|
| 📊 대시보드 | 전체 통계·7축 분포 막대 |
| 🏷️ 검수 | 7축 조합 필터 + 이미지별 라벨 편집·검수·버리기(실시간 협업 옵션) |
| 🎯 커버리지 | 카테고리별 상위 이미지 |
| 🗺️ 관계맵 | CLIP 임베딩 2D 지도(군집·희귀도·밀도·유사도·영역선택) |
| 🔀 조합 | 축 교차 히트맵(부족 조합 발견) |

> 정본 UI는 `app.py`(→ index.html) 하나다. 구 `gallery.py`·`dashboard.py`는 이 앱에
> 흡수된 레거시다(파일 감사: [`docs/AUDIT.md`](docs/AUDIT.md)).
> `scripts/review/app.py` 재실행으로 갱신, 관계맵은 `scripts/index/embed_map.py` 먼저 실행.

## 분류 체계 (7축 43코드) — `taxonomy.yaml`

| 축 | 코드 수 | 다중 | 라벨 방식 |
|---|---|---|---|
| where 장소 | 9 | 단일 | CLIP (ViT-H/14) |
| pose_action 포즈 | 9 | 단일 | CLIP |
| shot_size 프레이밍 | 4 | 단일 | CLIP |
| camera_style 앵글·무드 | 11 | **다중** | CLIP |
| gender 성별 | 4 | 단일 | CLIP |
| person_count 인원 | 3 | 단일 | **YOLOv8** (사람 검출 수) |
| expression 표정 | 3 | 단일 | CLIP |

**라벨링 정확도**: 구도·성별·표정은 대형 CLIP(ViT-H/14) zero-shot, 인원수는 YOLO
사람 검출로 각 축에 가장 적합한 모델을 썼다. 모든 라벨은 자동 초안이며,
`gallery.html`에서 사람이 검수·수정한다(검수자·시각 기록).

## 데이터 위치

```
data/00_raw/{날짜}/{소스}/{카테고리}/raw_NNNNNN.jpg   # 크롤링 원본 (출처 폴더)
data/01_curated/images/img_NNNNNN.jpg                # 승인 원본 (평평)
data/01_curated/rejected/{duplicate,low_quality}/    # 걸러진 것
data/02_processed/768/img_NNNNNN.jpg                 # 학습용 리사이즈
data/03_splits/{train,val,test}.csv                  # 학습 분할
annotations/master_metadata.csv                      # ★ 이미지별 7축 라벨 + 검수 (핵심)
```

**중요**: `00_raw`의 폴더는 "어느 검색어로 받았나"일 뿐, 최종 분류가 아니다.
진짜 7축 분류는 `master_metadata.csv`에 있고, 한 이미지가 여러 축 라벨을 동시에 가진다.

## 파이프라인 (scripts/)

```
crawl/crawl.py         멀티소스 수집 (Bing/Pinterest/스톡)
crawl/seeds.py         시드핀 연관핀 수집 (희소 카테고리 보강)
crawl/autoseed.py      검색결과에서 시드핀 자동 확보
dedup.py   pHash 중복 제거 + 저품질 필터
label/auto_label.py    구도 4축 CLIP 라벨 + 한국인/인스타 스타일 필터
label/enrich.py        성별/인원/표정 백필 (CLIP + YOLO)
curate/promote.py      승인분 → curated 승격
curate/apply_review.py 검수 CSV → master 반영
curate/cleanup.py      고아 파일 정리
resize.py       512/768/1024 리사이즈
make_splits/           train/val/test 분할
validate_dataset/      라벨 무결성·커버리지 검증
index/build_index.py   레퍼런스 인덱스 빌드(포즈·임베딩·희귀도·클러스터 → parquet)
embed_map.py           관계맵 데이터 생성 (인덱스 parquet 소비)
app.py                 ★ 통합 앱 index.html 생성 (정본 UI)
─ 앱(app.py)에 흡수되어 삭제된 레거시 CLI: gallery · dashboard · contact_sheet · coverage_check · search_similar
```

## 검수 워크플로 (정본)

1. `python scripts/index/embed_map.py && python scripts/review/app.py` 로 앱 생성 → `index.html` 열기
2. 헤더 "🏷️ 검수" 탭 → 검수자 이름 입력 → 필터/관계맵/조합으로 대상 좁히기
3. 썸네일 클릭 → 라벨 칩 수정 → "검수 완료(A)" 또는 "버리기(D)" (다시 누르면 취소)
4. "CSV 내보내기" → `review_decisions.csv` → `apply_review.py` 로 master 반영
5. 실시간 협업은 `config/firebase.json` 설정 시 자동 활성 ([`docs/FIREBASE.md`](docs/FIREBASE.md))

## 저작권

Pinterest·Bing 수집분은 `license=unknown`, `usage_allowed=experiment_only`.
**내부 참고용**으로만 사용하고, 상용/외부공개는 하지 않는다. `source_url`로 출처 추적 가능.
