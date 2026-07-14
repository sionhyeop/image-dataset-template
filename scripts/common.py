"""공용 유틸리티: 경로, 설정 로드, CSV 헬퍼, 이미지 검증."""
from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

import yaml

# --- 경로 -------------------------------------------------------------------
# scripts/common.py 기준으로 저장소 루트(composition_dataset/)를 계산.
# ROOT 는 **코드·설정의 루트**다(scripts/, config/, tests/). 데이터셋과 무관하다.
ROOT = Path(__file__).resolve().parent.parent

# DATASET_DIR — **데이터셋의 루트**(taxonomy·queries·data·annotations).
# 코드는 하나인데 데이터셋은 여러 개일 수 있으므로 둘을 가른다.
#   DATASET_DIR=datasets/food          python scripts/...      # 다른 데이터셋
#   DATASET_DIR=tests/fixtures/domain_min python scripts/...   # 가짜 도메인으로 회귀 테스트
# 지정하지 않으면 DEFAULT_DATASET.
DEFAULT_DATASET = "datasets/example_plants"
_ds = os.environ.get("DATASET_DIR", "").strip() or DEFAULT_DATASET
DATASET_ROOT = (Path(_ds) if Path(_ds).is_absolute() else ROOT / _ds).resolve()
DATASET = DATASET_ROOT.name

TAXONOMY_PATH = DATASET_ROOT / "taxonomy.yaml"
QUERIES_PATH = DATASET_ROOT / "queries.yaml"
DATA = DATASET_ROOT / "data"
RAW = DATA / "00_raw"
CURATED = DATA / "01_curated"
CURATED_IMAGES = CURATED / "images"
REJECTED = CURATED / "rejected"
PROCESSED = DATA / "02_processed"
SPLITS = DATA / "03_splits"
SAMPLES = DATA / "04_samples"
ANNOTATIONS = DATASET_ROOT / "annotations"

# 배포 루트도 데이터셋마다 하나씩. sites/<데이터셋>/ 안에 .vercel(프로젝트 링크)과
# vercel.json(COOP/COEP 헤더)이 산다. 앱은 여기로 복사돼 배포된다.
SITE = ROOT / "sites" / DATASET

RAW_META = ANNOTATIONS / "raw_metadata.csv"
MASTER_META = ANNOTATIONS / "master_metadata.csv"
# 자동라벨 불변 스냅샷: master 는 사람 검수로 덮이므로 '모델이 원래 뭐라 했는지'가 사라진다.
# 축 감사(check/axis_audit.py)가 모델을 채점하려면 이 원본이 반드시 있어야 한다.
AUTO_LABELS = ANNOTATIONS / "auto_labels.csv"

RAW_META_FIELDS = [
    "raw_file", "source", "category", "search_query",
    "source_url", "downloaded_at", "license", "author",
]

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

# --- 라벨 축 정의 (SSOT = taxonomy.yaml 의 `_axes`) ---------------------------
# 템플릿화: 새 이미지셋은 taxonomy.yaml 만 갈아끼우면 전 파이프라인·검수앱이 따라온다.
# (key, 표시명, 멀티선택). 멀티축 값은 CSV 에서 ';' 로 연결.
def _load_axis_schema():
    """taxonomy.yaml 의 _axes 에서 축 스키마를 읽는다.

    **폴백하지 않는다.** 예전엔 _axes 가 없으면 인물 도메인 축 7개로 되돌아갔다.
    그 폴백이 있으면 새 도메인이 taxonomy 를 잘못 써도 조용히 인물 축으로 돌아가서,
    "왜 내 라벨이 CSV 에 안 들어오지?" 를 며칠씩 헤매게 된다. 조용한 폴백보다 시끄러운 실패가 낫다.
    """
    try:
        with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
            tx = yaml.safe_load(f) or {}
    except OSError as e:
        raise SystemExit(f"taxonomy 를 읽을 수 없습니다: {TAXONOMY_PATH}\n  {e}\n"
                         f"  DATASET_DIR 이 맞습니까? (현재: {DATASET_ROOT})") from e
    meta = tx.get("_axes")
    if not meta:
        raise SystemExit(f"{TAXONOMY_PATH} 에 `_axes` 가 없습니다 — 축을 선언해야 합니다.\n"
                         f"  예시는 datasets/food/taxonomy.yaml 을 보세요.")
    axes = [(a["key"], a.get("name", a["key"]), bool(a.get("multi", False))) for a in meta]
    comp = [a["key"] for a in meta if a.get("composition")] or [a[0] for a in axes]
    modes = {a["key"]: a.get("mode", "assisted") for a in meta}
    return axes, comp, int(tx.get("_coverage_min", 10)), modes


AXES, COMP_AXES, COVERAGE_MIN, AXIS_MODE = _load_axis_schema()
LABEL_AXES = [k for k, _, _ in AXES]
MULTI_AXES = {k for k, _, m in AXES if m}

# 축 운영 모드 (taxonomy._axes.mode) — check/axis_audit.py 판정이 그대로 대응된다.
#   assisted 자동라벨+검수 · human 사람 전용(AI 제안 없음) · derived 시스템이 채움(검수 UI 제외)
REVIEW_AXES = [k for k, _, _ in AXES if AXIS_MODE.get(k, "assisted") != "derived"]
DERIVED_AXES = [k for k, _, _ in AXES if AXIS_MODE.get(k) == "derived"]

# 자동라벨 스냅샷 스키마 — 축은 taxonomy 에서 파생되므로 도메인이 바뀌어도 따라온다.
AUTO_LABEL_FIELDS = ["image_id", *LABEL_AXES, "quality_score", "status", "labeled_at"]


def _load_extra_fields() -> list[str]:
    """taxonomy._extra_fields — 이 도메인에만 있는 CSV 컬럼(예: korean_prob).

    도메인 특정 스코어를 CSV 스키마에 박아두면 다른 도메인에서 죽은 컬럼이 된다.
    필요한 도메인이 스스로 선언하게 한다.
    """
    try:
        with open(TAXONOMY_PATH, "r", encoding="utf-8") as f:
            tx = yaml.safe_load(f) or {}
    except OSError:
        return []
    return [str(x) for x in (tx.get("_extra_fields") or [])]


EXTRA_FIELDS = _load_extra_fields()

# master CSV 스키마 — **축은 taxonomy 에서 파생된다.**
# 예전엔 where/pose_action/gender/korean_prob 가 리터럴로 박혀 있어, 새 도메인의 축이
# CSV 에 아예 저장되지 않았다(라벨을 붙여도 사라진다). 가장 조용하고 치명적인 실패였다.
MASTER_FIELDS = [
    "image_id", "file_path", "source", "source_url", "downloaded_at",
    "width", "height",
    *LABEL_AXES,                              # ← taxonomy._axes 에서
    "quality_score", "status", "notes",
    "license", "author", "usage_allowed", "phash",
    *EXTRA_FIELDS,                            # ← taxonomy._extra_fields 에서
    "reviewed", "reviewer", "reviewed_at",
]



# CLIP 모델 계열 (라벨러·인덱스가 반드시 같은 계열이어야 함 — AUDIT.md §1)
CLIP_MODEL = os.environ.get("CLIP_MODEL", "ViT-H-14")
CLIP_PRETRAINED = os.environ.get("CLIP_PRETRAINED", "laion2b_s32b_b79k")


def image_path(row_or_path) -> Path:
    """CSV 의 file_path → 실제 파일 경로.

    **file_path 는 데이터셋 루트 기준 상대경로다**(예: "data/01_curated/images/img_1.jpg").
    저장소 루트 기준으로 두면 데이터셋을 폴더째 옮기는 순간 전부 깨진다 —
    실제로 datasets/composition/ 으로 옮길 때 그 문제가 드러났다.
    데이터셋은 자기완결적이어야 한다.
    """
    fp = row_or_path["file_path"] if isinstance(row_or_path, dict) else str(row_or_path)
    return DATASET_ROOT / fp


def raw_path(row_or_path) -> Path:
    """raw_metadata.csv 의 raw_file → 실제 파일 경로. image_path 와 같은 규칙."""
    fp = row_or_path["raw_file"] if isinstance(row_or_path, dict) else str(row_or_path)
    return DATASET_ROOT / fp


def rel(p) -> str:
    """로그 표시용 상대경로. 데이터셋이 저장소 밖에 있어도 죽지 않는다."""
    p = Path(p)
    for base in (DATASET_ROOT, ROOT):
        try:
            return str(p.relative_to(base))
        except ValueError:
            continue
    return str(p)


def split_codes(axis: str, value) -> list[str]:
    """CSV 라벨 값 → 코드 리스트. 멀티축은 ';' 분리, 단일축은 1개(빈 값은 [])."""
    if value is None:
        return []
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return []
    return [c.strip() for c in s.split(";") if c.strip()] if axis in MULTI_AXES else [s]


def today() -> str:
    return date.today().isoformat()


def load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_taxonomy() -> dict:
    """축→코드맵만 반환. `_axes`·`_coverage_min` 같은 메타(_ 접두)는 제외."""
    tx = load_yaml(TAXONOMY_PATH) or {}
    return {k: v for k, v in tx.items() if not k.startswith("_")}


def load_semantic() -> dict:
    """taxonomy._semantic — 어느 축이 무슨 뜻인지. 앱·파이프라인이 여기에 물어본다."""
    try:
        tx = load_yaml(TAXONOMY_PATH) or {}
    except OSError:
        return {}
    return tx.get("_semantic") or {}


def has_capability(name: str) -> bool:
    """이 데이터셋에 해당 능력이 있는가 — **선언이 곧 능력이다.**

    별도의 `_capabilities` 플래그를 두지 않는다. taxonomy._semantic 에 그 역할이
    선언돼 있으면 그 기능을 쓸 수 있는 도메인이고, 없으면 아니다. 플래그를 따로 두면
    선언과 어긋날 수 있고, 사람이 둘 다 관리해야 한다.

        person : 피사체가 사람인가 (facing/framing/count 중 하나라도 선언)
        pose   : 포즈(관절) 개념이 있는가 (facing 또는 framing)

    인물 없는 도메인(음식·제품·풍경)에서는 이 함수가 False 를 돌려주고,
    enrich·pose_match·build_index 의 포즈 단계가 **스스로 건너뛴다.**
    예전엔 스위치가 아예 없어서 음식 사진 1만 장에도 BlazePose 를 돌려 전부 None 을 얻었고,
    pose_match.py 는 exit 1 로 죽어 파이프라인을 멈췄다.
    """
    sem = load_semantic()
    if name == "person":
        return any(k in sem for k in ("facing", "framing", "count"))
    if name == "pose":
        return any(k in sem for k in ("facing", "framing"))
    return name in sem


def load_curation() -> dict:
    """taxonomy._curation — 무엇을 모으는가(목적·차단 게이트·정렬 신호).

    임계값은 손으로 찍은 것이 아니라 check/gate_audit.py 가 사람의 keep/discard 판정으로
    측정한 값이다. gates(차단)와 rank(정렬)는 역할이 다르므로 절대 바꿔 쓰지 않는다.
    """
    try:
        tx = load_yaml(TAXONOMY_PATH) or {}
    except OSError:
        return {}
    c = tx.get("_curation") or {}
    return {
        "purpose": c.get("purpose", ""),
        "intake": c.get("intake") or {},     # 수집 필터 (dedup.py — 최소 해상도·종횡비·중복)
        "score": c.get("score") or {},       # 검수 우선순위 점수 규칙 (rank.py)
        "detect": c.get("detect") or {},     # 객체 검출 (label/detect.py — 백엔드·대상 클래스)
        "gates": c.get("gates", []),         # 차단 (gate_audit 가 측정)
        "rank": c.get("rank", []),           # 정렬 (gate_audit 가 측정)
        "balance": c.get("balance", list(COMP_AXES)),
    }


def load_queries() -> dict:
    return load_yaml(QUERIES_PATH)


def all_taxonomy_codes(taxonomy: dict) -> dict[str, set[str]]:
    """축 이름 -> 코드 집합. (메타 키 `_*` 는 무시)"""
    return {axis: set(codes.keys()) for axis, codes in taxonomy.items()
            if not axis.startswith("_") and isinstance(codes, dict)}


# --- CSV 헬퍼 ---------------------------------------------------------------
BACKUPS = ANNOTATIONS / "_backups"


def backup_csv(path: Path) -> Path | None:
    """덮어쓰기 전 타임스탬프 백업을 _backups/ 에 남긴다. 원본 없으면 None."""
    if not path.exists():
        return None
    from datetime import datetime
    import shutil
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dst = BACKUPS / f"{path.stem}.{datetime.now():%Y%m%d_%H%M%S}{path.suffix}"
    shutil.copy2(path, dst)
    return dst


def write_json_atomic(path: Path, text: str) -> None:
    """임시파일 쓰기 후 원자적 교체 — 중단돼도 기존 파일 무손상."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def append_row(path: Path, fields: list[str], row: dict) -> None:
    """헤더가 없으면 만들고 한 행 추가."""
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})


def read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_rows(path: Path, fields: list[str], rows: list[dict]) -> None:
    """임시파일에 쓴 뒤 원자적 교체 — 중단돼도 기존 파일 무손상."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
    os.replace(tmp, path)


# --- 이미지 검증 -------------------------------------------------------------
def image_dimensions(path: Path):
    """정상 이미지면 (w, h), 손상/미지원이면 None."""
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.verify()
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def load_env() -> None:
    """루트의 .env 로드 (있으면). python-dotenv 없으면 조용히 스킵."""
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except Exception:
        pass


def env(key: str) -> str | None:
    v = os.environ.get(key, "").strip()
    return v or None
