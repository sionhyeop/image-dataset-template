"""포즈 디스크립터 v0 레퍼런스 구현.

스펙: soma_camera_composition/docs/스펙_포즈디스크립터_v0.md (schema_version "0.1")
TS 런타임 구현과 동일 픽스처(fixtures/)로 패리티 테스트할 것 — 이 파일이 정답 기준.

실행: .venv/bin/python -m scripts.descriptor.pose_descriptor  (자체 테스트)
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SCHEMA_VERSION = "0.1"
N_FEATURES = 21
VIS_THRESHOLD = 0.5
MIN_VALID_FEATURES = 13          # 60% 미만이면 디스크립터 무효
MIN_COMMON_FEATURES = 10         # 비교 시 공통 유효 특징 하한

# BlazePose 인덱스 (COCO-17 교집합만 사용 → K2/K3/K4 호환)
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28

# 기본 가중치 (엔진 설정 — 디스크립터에 직렬화하지 않는다. E-001에서 튜닝)
DEFAULT_WEIGHTS = np.array([1.5] * 4 + [1.0] * 14 + [0.7] * 3)

# 미러 시 서로 스왑되는 특징 인덱스 쌍 (스펙 §6)
_MIRROR_SWAPS = [(0, 1), (2, 3), (4, 6), (5, 7), (8, 10), (9, 11), (12, 14), (13, 15)]
# 미러 시 부호 반전: 방향각 sin 성분(4,6,8,10,12,14)·머리 요(18). 몸통 sin(16)은 좌우 반전과 무관.
_MIRROR_NEGATE = [4, 6, 8, 10, 12, 14, 18]


@dataclass
class Descriptor:
    features: np.ndarray          # (21,) float32, 무효 칸은 nan
    valid: int                    # 21bit 마스크
    torso_deg: float
    model: str = "blazepose-lite"

    @property
    def is_valid(self) -> bool:
        return bin(self.valid).count("1") >= MIN_VALID_FEATURES

    def to_json(self, raw_keypoints_ref: str | None = None) -> dict:
        f = np.nan_to_num(self.features, nan=0.0)  # 직렬화만 0 — valid 마스크가 진실
        return {
            "schema_version": SCHEMA_VERSION,
            "model": self.model,
            "features": [round(float(x), 6) for x in f],
            "valid": self.valid,
            "torso_deg": round(self.torso_deg, 3),
            "raw_keypoints_ref": raw_keypoints_ref,
        }


def _pt(kp: np.ndarray, i: int) -> np.ndarray | None:
    """visibility 게이트 통과한 2D 좌표, 아니면 None."""
    return kp[i, :2] if kp[i, 3] >= VIS_THRESHOLD else None


def _interior_angle(a, b, c) -> float | None:
    """b를 꼭짓점으로 하는 내각 [0, π]."""
    if a is None or b is None or c is None:
        return None
    u, v = a - b, c - b
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return None
    return float(np.arccos(np.clip(np.dot(u, v) / (nu * nv), -1.0, 1.0)))


def _seg_angle(a, b) -> float | None:
    """몸통 좌표계에서 a→b 방향각."""
    if a is None or b is None:
        return None
    d = b - a
    if np.linalg.norm(d) < 1e-9:
        return None
    return float(math.atan2(d[0], -d[1]))  # 화면 아래가 +y이므로 직립 하향 세그먼트=π


def compute(keypoints: np.ndarray, model: str = "blazepose-lite") -> Descriptor:
    """keypoints: (33, 4) [x, y, z, visibility], 정규화 이미지 좌표."""
    kp = np.asarray(keypoints, dtype=np.float64)
    feats = np.full(N_FEATURES, np.nan)
    valid = 0

    ls, rs = _pt(kp, L_SHOULDER), _pt(kp, R_SHOULDER)
    lh, rh = _pt(kp, L_HIP), _pt(kp, R_HIP)
    if ls is None or rs is None or lh is None or rh is None:
        return Descriptor(feats.astype(np.float32), 0, float("nan"), model)

    shoulder_c, hip_c = (ls + rs) / 2, (lh + rh) / 2
    torso = shoulder_c - hip_c
    body_scale = float(np.linalg.norm(torso))
    if body_scale < 1e-6:
        return Descriptor(feats.astype(np.float32), 0, float("nan"), model)

    theta = math.atan2(torso[0], -torso[1])  # 직립=0
    cos_t, sin_t = math.cos(-theta), math.sin(-theta)
    rot = np.array([[cos_t, -sin_t], [sin_t, cos_t]])

    def norm_pt(i: int):
        p = _pt(kp, i)
        return None if p is None else rot @ ((p - hip_c) / body_scale)

    P = {i: norm_pt(i) for i in (NOSE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER, L_ELBOW,
                                 R_ELBOW, L_WRIST, R_WRIST, L_HIP, R_HIP, L_KNEE,
                                 R_KNEE, L_ANKLE, R_ANKLE)}

    def set_feat(idx: int, val: float | None):
        nonlocal valid
        if val is not None and math.isfinite(val):
            feats[idx] = val
            valid |= 1 << idx

    # 0–3 내각
    set_feat(0, (lambda a: None if a is None else a / math.pi)(
        _interior_angle(P[L_SHOULDER], P[L_ELBOW], P[L_WRIST])))
    set_feat(1, (lambda a: None if a is None else a / math.pi)(
        _interior_angle(P[R_SHOULDER], P[R_ELBOW], P[R_WRIST])))
    set_feat(2, (lambda a: None if a is None else a / math.pi)(
        _interior_angle(P[L_HIP], P[L_KNEE], P[L_ANKLE])))
    set_feat(3, (lambda a: None if a is None else a / math.pi)(
        _interior_angle(P[R_HIP], P[R_KNEE], P[R_ANKLE])))

    # 4–15 세그먼트 방향 (sin, cos)
    for base, (a, b) in zip(
        range(4, 16, 2),
        [(L_SHOULDER, L_ELBOW), (R_SHOULDER, R_ELBOW),
         (L_HIP, L_KNEE), (R_HIP, R_KNEE),
         (L_ELBOW, L_WRIST), (R_ELBOW, R_WRIST)],
    ):
        ang = _seg_angle(P[a], P[b])
        if ang is not None:
            set_feat(base, math.sin(ang))
            set_feat(base + 1, math.cos(ang))

    # 16–17 몸통 기울기
    set_feat(16, math.sin(theta))
    set_feat(17, math.cos(theta))

    # 18 머리 요 프록시 (몸통 좌표계 코 x / 어깨너비)
    if P[NOSE] is not None and P[L_EAR] is not None and P[R_EAR] is not None:
        sw = float(np.linalg.norm(P[L_SHOULDER] - P[R_SHOULDER]))
        if sw > 1e-6:
            set_feat(18, float(np.clip(P[NOSE][0] / sw, -1.0, 1.0)))

    # 19 어깨너비 비, 20 스탠스 폭
    set_feat(19, float(np.clip(np.linalg.norm(P[L_SHOULDER] - P[R_SHOULDER]), 0, 1.5) / 1.5))
    if P[L_ANKLE] is not None and P[R_ANKLE] is not None:
        set_feat(20, float(np.clip(abs(P[L_ANKLE][0] - P[R_ANKLE][0]), 0, 2.0) / 2.0))

    return Descriptor(feats.astype(np.float32), valid, math.degrees(theta), model)


def distance(a: Descriptor, b: Descriptor, weights: np.ndarray = DEFAULT_WEIGHTS) -> float | None:
    common = a.valid & b.valid
    idx = [i for i in range(N_FEATURES) if common >> i & 1]
    if len(idx) < MIN_COMMON_FEATURES:
        return None
    fa = a.features[idx].astype(np.float64)
    fb = b.features[idx].astype(np.float64)
    w = weights[idx]
    return float(math.sqrt(np.sum(w * (fa - fb) ** 2) / np.sum(w)))


def mirror(d: Descriptor) -> Descriptor:
    f = d.features.copy()
    v = d.valid
    for i, j in _MIRROR_SWAPS:
        f[i], f[j] = f[j], f[i]
        bi, bj = v >> i & 1, v >> j & 1
        v = (v & ~(1 << i) & ~(1 << j)) | (bj << i) | (bi << j)
    for i in _MIRROR_NEGATE:
        if v >> i & 1:
            f[i] = -f[i]
    return Descriptor(f, v, -d.torso_deg, d.model)


# ---------------- 자체 테스트 (스펙 §8의 1~3) ----------------

def _synth_pose(elbow_bend: float = math.pi, lean: float = 0.0) -> np.ndarray:
    """합성 직립 포즈. elbow_bend: 왼팔꿈치 내각(라디안), lean: 몸통 기울기."""
    kp = np.zeros((33, 4))
    kp[:, 3] = 1.0

    def put(i, x, y):
        kp[i, 0], kp[i, 1] = x, y

    put(NOSE, 0.50, 0.20); put(L_EAR, 0.53, 0.20); put(R_EAR, 0.47, 0.20)
    put(L_SHOULDER, 0.58, 0.30); put(R_SHOULDER, 0.42, 0.30)
    put(L_HIP, 0.55, 0.55); put(R_HIP, 0.45, 0.55)
    put(L_KNEE, 0.55, 0.75); put(R_KNEE, 0.45, 0.75)
    put(L_ANKLE, 0.55, 0.95); put(R_ANKLE, 0.45, 0.95)
    put(R_ELBOW, 0.40, 0.42); put(R_WRIST, 0.38, 0.54)
    # 왼팔: 전완은 상완 연장 방향을 (π - elbow_bend)만큼 회전 → 내각 = elbow_bend 정확 보장
    put(L_ELBOW, 0.60, 0.42)
    fore = 0.12
    upper = np.array([0.60 - 0.58, 0.42 - 0.30])
    upper /= np.linalg.norm(upper)
    a = math.pi - elbow_bend
    R2 = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    fdir = R2 @ upper
    put(L_WRIST, 0.60 + fore * fdir[0], 0.42 + fore * fdir[1])
    if lean != 0.0:  # 힙 센터 기준 전체 회전
        c = np.array([0.5, 0.55])
        R = np.array([[math.cos(lean), -math.sin(lean)], [math.sin(lean), math.cos(lean)]])
        kp[:, :2] = (kp[:, :2] - c) @ R.T + c
    return kp


def _self_test() -> None:
    rng = np.random.default_rng(42)
    base = compute(_synth_pose())
    assert base.is_valid and base.valid == (1 << N_FEATURES) - 1, "완전 포즈인데 무효 특징 존재"
    assert abs(base.features[0] - 1.0) < 1e-6, "펴진 팔꿈치 내각 != π"
    assert abs(base.torso_deg) < 1e-6, "직립인데 torso_deg != 0"

    # 1) 불변성: 평행이동·스케일·회전
    kp = _synth_pose()
    for name, tf in [
        ("translate", lambda k: k + np.array([0.3, -0.1, 0, 0])),
        ("scale", lambda k: np.concatenate([k[:, :2] * 0.4 + 0.1, k[:, 2:]], axis=1)),
    ]:
        d2 = compute(tf(kp.copy()))
        diff = np.nanmax(np.abs(d2.features - base.features))
        assert diff < 1e-5, f"{name} 불변성 위반: {diff}"
    rot = compute(_synth_pose(lean=0.4))
    non_torso = [i for i in range(N_FEATURES) if i not in (16, 17)]
    diff = np.nanmax(np.abs(rot.features[non_torso] - base.features[non_torso]))
    assert diff < 1e-5, f"회전 불변성 위반(몸통 특징 제외): {diff}"
    assert abs(math.radians(rot.torso_deg) - (-0.4)) < 1e-6 or abs(math.radians(rot.torso_deg) - 0.4) < 1e-6

    # 2) 단조성: 팔꿈치를 점점 굽히면 base와의 거리 단조 증가
    dists = [distance(base, compute(_synth_pose(elbow_bend=b)))
             for b in np.linspace(math.pi, math.pi / 3, 8)]
    assert all(dists[i] < dists[i + 1] + 1e-9 for i in range(len(dists) - 1)), f"단조성 위반: {dists}"

    # 3) 미러: 대칭 포즈는 미러 자기 자신과 거리 ~0, involution 성립
    m = mirror(base)
    assert distance(base, m) < 0.03, "좌우 대칭 포즈의 미러 거리가 0이 아님"
    mm = mirror(m)
    assert np.nanmax(np.abs(mm.features - base.features)) < 1e-6, "mirror(mirror(x)) != x"

    # 4) 결측: 하반신 전부 가림 → 무효 특징 표시 + 여전히 비교 가능/불가 판단
    occl = _synth_pose()
    for i in (L_KNEE, R_KNEE, L_ANKLE, R_ANKLE):
        occl[i, 3] = 0.0
    do = compute(occl)
    assert not (do.valid >> 2 & 1) and not (do.valid >> 20 & 1), "가린 특징이 유효로 표시됨"
    assert do.is_valid, "하반신만 가렸는데 전체 무효 처리됨"
    assert distance(base, do) is not None

    # 5) 직렬화 라운드트립 스키마
    j = base.to_json("kp/test.bin")
    assert j["schema_version"] == SCHEMA_VERSION and len(j["features"]) == N_FEATURES

    # 노이즈 강건성 리포트 (assert 없음 — 참고 수치)
    noise = [distance(base, compute(_synth_pose() + np.concatenate(
        [rng.normal(0, s, (33, 2)), np.zeros((33, 2))], axis=1)))
        for s in (0.002, 0.005, 0.01)]
    print(f"self-test OK · noise(σ=.002/.005/.01) → d={['%.4f' % x for x in noise]}")


def _dump_fixtures(out_dir: Path) -> None:
    """TS 패리티 테스트용 골든 픽스처 (스펙 §8-1)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = {
        "upright": _synth_pose(),
        "elbow_bent_90": _synth_pose(elbow_bend=math.pi / 2),
        "lean_23deg": _synth_pose(lean=math.radians(23)),
        "lower_body_occluded": (lambda k: ([k.__setitem__((i, 3), 0.0)
                                for i in (L_KNEE, R_KNEE, L_ANKLE, R_ANKLE)], k)[1])(_synth_pose()),
    }
    fixtures = []
    for name, kp in cases.items():
        d = compute(kp)
        fixtures.append({"name": name,
                         "keypoints": [[round(float(v), 6) for v in row] for row in kp],
                         "expected": d.to_json()})
    (out_dir / "descriptor_v0_golden.json").write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, "fixtures": fixtures},
                   indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"fixtures → {out_dir / 'descriptor_v0_golden.json'}")


if __name__ == "__main__":
    _self_test()
    _dump_fixtures(Path(__file__).parent / "fixtures")
