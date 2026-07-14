"""레퍼런스 인덱스(IDX) 빌드 — 기술검토 §1.1, 결정 D-003 (유일한 P0).

승인(approved) 이미지 전체에 대해 오프라인 사전 계산:
  meta        7축 라벨·품질점수·pHash·출처·수집일 (master_metadata에서)
  pose        MediaPipe Pose heavy 33 키포인트 [x,y,z,visibility]
  descriptor  포즈 디스크립터 v0 21차원 (scripts/index/descriptor/pose_descriptor.py)
  embed       OpenCLIP 이미지 임베딩 float16 (기본 ViT-H-14 — clip_label.py와 통일, §1 결정 B)
  quality     선명도(Laplacian 분산)·밝기 (768px 처리본 기준)
  stats       X5 통계 레이어 — 임베딩 k-means 클러스터ID·클러스터 크기(인기도)·라벨 희귀도

산출: annotations/reference_index.parquet (한 이미지 = 한 행, 열 스키마는 INDEX_VERSION로 관리)
미구현(후속): 외곽선 벡터 패스(BiRefNet/SAM2 미설치), saliency 요약, 썸네일 3종.

증분 실행: 이미 인덱스에 있는 image_id는 건너뛴다(--force로 전체 재계산).
stats 단계는 매 실행 전체 재계산(값싸고 전체 분포에 의존하므로).

사용:
    .venv/bin/python -m scripts.index.build_index                # 전체 (증분)
    .venv/bin/python -m scripts.index.build_index --limit 20     # 스모크
    .venv/bin/python -m scripts.index.build_index --stages pose,descriptor
    CLIP_MODEL=ViT-B-32 CLIP_PRETRAINED=laion2b_s34b_b79k ...    # 빠른모드로 되돌리려면(비권장)
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
from index.descriptor import pose_descriptor as pdesc  # noqa: E402

INDEX_VERSION = "0.1"
INDEX_PATH = common.ANNOTATIONS / "reference_index.parquet"
PROCESSED_768 = common.PROCESSED / "768"

# §1 결정 B(2026-07-10): 라벨(clip_label.py)과 임베딩 공간 통일 → 기본 ViT-H-14.
# 임베딩(GPU 자동)은 ClipEmbedder에서 cuda 사용. 되돌리려면 env로 ViT-B-32 지정.
CLIP_MODEL = common.CLIP_MODEL            # 라벨러(clip_label.py)와 계열 일치 — SSOT
CLIP_PRETRAINED = common.CLIP_PRETRAINED

LABEL_AXES = common.LABEL_AXES            # 축 정의 SSOT (common.py)
META_COLS = ["image_id", "file_path", "width", "height", "source",
             "downloaded_at", "usage_allowed", "quality_score", "phash"] + LABEL_AXES

N_CLUSTERS = 16          # L2 포즈 유형 테스트(16클러스터)와 공유
CHECKPOINT_EVERY = 200   # 중단 대비 부분 저장 주기


def _img_768(file_path: str) -> Path:
    """원본 file_path와 같은 basename의 768px 처리본 (포즈·임베딩·품질 공통 입력)."""
    return PROCESSED_768 / Path(file_path).name


# --- 단계별 계산기 ------------------------------------------------------------
POSE_TASK_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
                 "pose_landmarker_heavy/float16/latest/pose_landmarker_heavy.task")
POSE_TASK_PATH = common.ROOT / "config" / "models" / "pose_landmarker_heavy.task"


class PoseExtractor:
    """MediaPipe Tasks PoseLandmarker heavy(IMAGE 모드). 이미지당 33×[x,y,z,visibility]."""

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        if not POSE_TASK_PATH.exists():
            print(f"포즈 모델 다운로드 → {POSE_TASK_PATH.name}")
            import urllib.request
            POSE_TASK_PATH.parent.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(POSE_TASK_URL, POSE_TASK_PATH)
        self._mp = mp
        self._lm = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(POSE_TASK_PATH)),
            running_mode=vision.RunningMode.IMAGE,
            min_pose_detection_confidence=0.3))

    def __call__(self, img_bgr) -> np.ndarray | None:
        import cv2
        mp_img = self._mp.Image(image_format=self._mp.ImageFormat.SRGB,
                                data=cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
        res = self._lm.detect(mp_img)
        if not res.pose_landmarks:
            return None
        return np.array([[lm.x, lm.y, lm.z, lm.visibility]
                         for lm in res.pose_landmarks[0]], dtype=np.float32)

    def close(self):
        self._lm.close()


class ClipEmbedder:
    """OpenCLIP 이미지 임베딩(L2 정규화, float16). 런타임 쿼리 모델과 계열 일치 필수(§1.1 주의)."""

    def __init__(self):
        import open_clip
        import torch
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            CLIP_MODEL, pretrained=CLIP_PRETRAINED)
        self.model.eval()
        self.model.to(self.device)

    def __call__(self, pil_img) -> np.ndarray:
        with self.torch.no_grad():
            t = self.preprocess(pil_img).unsqueeze(0).to(self.device)
            v = self.model.encode_image(t)[0]
            v = v / v.norm()
        return v.cpu().numpy().astype(np.float16)


def quality_stats(img_bgr) -> tuple[float, float]:
    """(선명도 = 그레이 Laplacian 분산, 밝기 = 그레이 평균/255)."""
    import cv2
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var()), float(gray.mean() / 255.0)


# --- X5 통계 레이어 (전체 행에 대해 매번 재계산) --------------------------------
def add_stats(df: pd.DataFrame) -> pd.DataFrame:
    # 라벨 희귀도: 축별 역빈도(1/freq)를 [0,1] 정규화 후 축 평균 — 도감(L1) 별 개수의 근거.
    # 멀티축(;)은 코드별로 분리 집계(행 단위 조합 문자열이 아니라), 행 값은 코드 역빈도의 평균.
    from collections import Counter
    rar = np.zeros(len(df))
    used = 0
    for axis in LABEL_AXES:
        codes = df[axis].map(lambda v: common.split_codes(axis, v) or ["_none"])
        cnt = Counter(c for cs in codes for c in cs)
        total = sum(cnt.values())
        inv = codes.map(lambda cs: float(np.mean([total / cnt[c] for c in cs])))
        rng = inv.max() - inv.min()
        if rng > 1e-9:
            rar += ((inv - inv.min()) / rng).to_numpy()
            used += 1
    df["rarity"] = np.round(rar / max(used, 1), 4)

    # 임베딩 k-means: cluster_id(유형)·cluster_size(인기도 프록시)
    has_e = df["embed"].map(lambda v: v is not None and len(v) > 0)
    df["cluster_id"], df["cluster_size"] = -1, 0
    if has_e.sum() >= N_CLUSTERS:
        from sklearn.cluster import KMeans
        X = np.stack(df.loc[has_e, "embed"].map(np.asarray)).astype(np.float32)
        ids = KMeans(n_clusters=N_CLUSTERS, n_init=4, random_state=42).fit_predict(X)
        df.loc[has_e, "cluster_id"] = ids
        sizes = pd.Series(ids).value_counts()
        df.loc[has_e, "cluster_size"] = [int(sizes[c]) for c in ids]
    return df


# --- 메인 --------------------------------------------------------------------
def build(limit: int | None, stages: set[str], force: bool) -> int:
    df = pd.read_csv(common.MASTER_META)
    ap = df[df.status == "approved"].copy()
    print(f"approved {len(ap)}장 · 단계 {sorted(stages)} · 인덱스 {INDEX_PATH.name}")

    prev = None
    if INDEX_PATH.exists() and not force:
        prev = pd.read_parquet(INDEX_PATH)
        # master 재동기화: 검수로 승인 취소(discard 등)된 행 제거 + 메타/라벨 갱신.
        # 증분은 기존 행을 그대로 보존하므로, 이 동기화 없이는 stale 라벨/유령 행이 영구 잔존한다.
        ok = prev.image_id.isin(set(ap.image_id))
        if (~ok).any():
            print(f"동기화: master 에서 승인 아님 {int((~ok).sum())}행 제거")
        prev = prev[ok].reset_index(drop=True)
        cur = ap.set_index("image_id")
        for col in META_COLS:
            if col != "image_id" and col in cur.columns:
                prev[col] = prev.image_id.map(cur[col])
        done = set(prev.image_id) & set(ap.image_id)
        ap = ap[~ap.image_id.isin(done)]
        print(f"증분: 기존 {len(done)}장 유지, 신규 {len(ap)}장 계산")
    if limit:
        ap = ap.head(limit)

    pose_ex = PoseExtractor() if "pose" in stages and len(ap) else None
    embed_ex = ClipEmbedder() if "embed" in stages and len(ap) else None

    import cv2
    from PIL import Image

    rows, t0 = [], time.time()
    for n, (_, m) in enumerate(ap.iterrows(), 1):
        row = {c: m.get(c) for c in META_COLS}
        row.update(pose_kp=None, pose_ok=False, desc=None, desc_valid=0,
                   torso_deg=np.nan, desc_ok=False, embed=None,
                   sharpness=np.nan, brightness=np.nan)
        p768 = _img_768(m.file_path)
        src = p768 if p768.exists() else common.image_path(m.file_path)
        img = cv2.imread(str(src))
        if img is None:
            print(f"  [경고] 읽기 실패: {src}")
            rows.append(row)
            continue

        if "quality" in stages:
            row["sharpness"], row["brightness"] = quality_stats(img)
        if pose_ex is not None:
            kp = pose_ex(img)
            if kp is not None:
                row["pose_kp"], row["pose_ok"] = kp.reshape(-1), True
                if "descriptor" in stages:
                    d = pdesc.compute(kp)
                    row.update(desc=np.nan_to_num(d.features, nan=0.0),
                               desc_valid=int(d.valid), torso_deg=float(d.torso_deg),
                               desc_ok=bool(d.is_valid))
        if embed_ex is not None:
            row["embed"] = embed_ex(Image.open(src).convert("RGB"))

        rows.append(row)
        if n % 25 == 0 or n == len(ap):
            rate = n / (time.time() - t0)
            print(f"  {n}/{len(ap)} ({rate:.1f} img/s, 남은 ~{(len(ap)-n)/rate/60:.0f}분)")
        if n % CHECKPOINT_EVERY == 0:
            _save(pd.DataFrame(rows), prev, partial=True)

    if pose_ex:
        pose_ex.close()

    out = pd.DataFrame(rows) if rows else pd.DataFrame(columns=META_COLS)
    _save(out, prev, partial=False, stats="stats" in stages)
    return 0


def _save(new: pd.DataFrame, prev: pd.DataFrame | None, partial: bool, stats: bool = False):
    full = pd.concat([prev, new], ignore_index=True) if prev is not None else new.copy()
    if len(full) == 0:
        print("저장할 행 없음")
        return
    if stats and not partial:
        full = add_stats(full)
    # 새 행에만 스탬프 (기존 행의 빌드 이력 보존)
    stamp = {"index_version": INDEX_VERSION,
             "embed_model": f"{CLIP_MODEL}/{CLIP_PRETRAINED}",
             "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for col, val in stamp.items():
        if col not in full:
            full[col] = val
        else:
            full[col] = full[col].fillna(val)
    tmp = INDEX_PATH.with_suffix(".parquet.tmp")
    full.to_parquet(tmp, index=False)
    tmp.replace(INDEX_PATH)
    tag = "체크포인트" if partial else "완료"
    print(f"[{tag}] {INDEX_PATH} · {len(full)}행 · {INDEX_PATH.stat().st_size/1e6:.1f}MB")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--limit", type=int, help="앞 N장만 (스모크 테스트)")
    # 기본 단계는 **도메인이 정한다.** 포즈 개념이 없는 도메인에 BlazePose 를 돌리면
    # 30MB 모델을 받아 전 이미지에 추론하고 전부 None 을 얻는다 — 순수 낭비다.
    _default = ("pose,descriptor,embed,quality,stats" if common.has_capability("pose")
                else "embed,quality,stats")
    ap.add_argument("--stages", default=_default,
                    help=f"쉼표 구분: pose,descriptor,embed,quality,stats (이 도메인 기본: {_default})")
    ap.add_argument("--force", action="store_true", help="증분 무시, 전체 재계산")
    a = ap.parse_args()
    if a.force and a.limit:
        # --force 는 기존 인덱스를 버리고 새 계산분만 저장하므로 --limit 과 조합하면
        # 전체 인덱스가 N행으로 교체된다(실사고 이력 있음).
        ap.error("--force 와 --limit 동시 사용 금지: 전체 인덱스가 N행으로 교체됩니다. "
                 "스모크 테스트는 --limit 단독(증분), 전체 재계산은 --force 단독으로.")
    return build(a.limit, set(a.stages.split(",")), a.force)


if __name__ == "__main__":
    raise SystemExit(main())
