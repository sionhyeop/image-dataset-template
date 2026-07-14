"""포즈 정밀 매칭 인덱스 — reference_index.parquet 소비.

CLIP(이미지 느낌) 유사도가 아닌 **실제 포즈 일치**를 위한 인덱스.
정답 기준 디스크립터(scripts/index/descriptor/pose_descriptor.py, 21차원 불변·occlusion·mirror)를
그대로 재사용한다. parquet 의 pose_kp → pose_descriptor.compute() → 마스크 가중 L2 로
pose-knn + k-means pose-clusters + 원시 keypoint 기반 속성(서기/앉기·팔·정면·기울기).

관계맵(embed_map.py)이 CLIP embed 를 소비하듯, 이 스크립트는 포즈 채널을 만든다.
매칭 대상: 유효 특징 >= MIN_MATCH_FEATURES(11) 인 포즈만(부실한 부분포즈 매칭 방지).

출력: annotations/pose_index.json =
  { meta, knn:{id:[[nid,dist],..]}, clusters:[{idx,size,rep,label,posture,facing}],
    cluster_of:{id:idx}, attr:{id:{p,a,f,l}} }

사용:
    python scripts/index/pose_match.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

# 포즈 축은 taxonomy._semantic.pose 가 정한다. 선언이 없으면 라벨 힌트 없이 기하학만 쓴다.
POSE_AXIS = (common.load_semantic().get("pose") or {}).get("axis")
from index.descriptor import pose_descriptor as pdz  # noqa: E402

INDEX_PATH = common.ANNOTATIONS / "reference_index.parquet"
OUT_PATH = common.ANNOTATIONS / "pose_index.json"

MIN_MATCH_FEATURES = 11   # 이 개수 미만 유효특징이면 매칭에서 제외(부실 부분포즈)
K = 24                    # pose-knn 후보 수 — 앱이 라벨 가중 재랭킹 후 상위만 표시(A안, 표시수는 앱에서 슬라이스)
DIST_MAX = 0.35           # 이 거리 초과 이웃은 약한 매칭 → 숨김
N_CLUSTERS = 10           # pose k-means 군집 수

# BlazePose 인덱스 (raw keypoint 속성 계산용)
NOSE = 0
L_EAR, R_EAR = 7, 8
L_SHOULDER, R_SHOULDER = 11, 12
L_ELBOW, R_ELBOW = 13, 14
L_WRIST, R_WRIST = 15, 16
L_HIP, R_HIP = 23, 24
L_KNEE, R_KNEE = 25, 26
L_ANKLE, R_ANKLE = 27, 28
VIS = pdz.VIS_THRESHOLD


# ---------------- 원시 keypoint 기반 속성 (해석 가능·강건) ----------------

def _v(kp, i):
    """visibility 게이트 통과한 2D 좌표(np.array) 또는 None."""
    return kp[i, :2] if kp[i, 3] >= VIS else None


def _interior(a, b, c):
    if a is None or b is None or c is None:
        return None
    import numpy as np
    u, v = a - b, c - b
    nu, nv = np.linalg.norm(u), np.linalg.norm(v)
    if nu < 1e-9 or nv < 1e-9:
        return None
    return math.degrees(math.acos(max(-1.0, min(1.0, float(u @ v) / (nu * nv)))))


# pose_action(오토라벨 단일값) → 자세 매핑. 소품/머리 등 자세 무관 라벨은 제외(미상 유지).
_LABEL_POSTURE = {"P01": "sit", "P02": "mid", "P03": "stand", "P04": "stand", "P10": "stand"}
_GEOM_TH = 0.40   # 허벅지 수직낙차비 임계(measured 캘리브). >= 서기, < 앉기.


def _posture_fallback(kp, ls, rs, lh, rh, pose_action):
    """무릎내각 불가(발목 미검출 등) 시 자세 추정. (posture, source) 반환."""
    import numpy as np
    # 2) geom: 발목 없이 무릎만 보일 때 — 허벅지 낙차비 = (무릎y - 힙y) / 몸통길이.
    kns = [p for p in (_v(kp, L_KNEE), _v(kp, R_KNEE)) if p is not None]
    shs = [p for p in (ls, rs) if p is not None]
    hps = [p for p in (lh, rh) if p is not None]
    if kns and shs and hps:
        hip_mid, sh_mid, kn_mid = np.mean(hps, 0), np.mean(shs, 0), np.mean(kns, 0)
        torso = float(np.linalg.norm(sh_mid - hip_mid))
        if torso > 1e-6:
            ratio = (kn_mid[1] - hip_mid[1]) / torso   # y-down: 양수=무릎이 힙보다 아래
            return ("stand" if ratio >= _GEOM_TH else "sit"), "geom"
    # 3) label: CLIP pose_action 코드 매핑(있고 자세성 라벨일 때만).
    #    멀티라벨(;)이면 자세 판정 가능한 첫 코드를 쓴다.
    if pose_action is not None:
        for part in str(pose_action).split(";"):
            code = part.strip().split("_")[0]
            if code in _LABEL_POSTURE:
                return _LABEL_POSTURE[code], "label"
    return "unknown", "none"


def attributes(kp, torso_deg, pose_action=None):
    """kp:(33,4). 자세/팔/정면/기울기를 원시 좌표에서 최대한 강건하게 추정.

    반환: {"p":서기|앉기|중간|미상, "ps":자세 출처(measured|geom|label|none),
           "a":내림|한팔올림|양팔올림|팔짱|미상, "f":정면|측면|미상, "l":몸통기울기(deg, 정수)}

    자세(p)는 3단 폴백(§자세미상 보강 2026-07-10):
      measured — 힙-무릎-발목 무릎내각(가장 신뢰). geom — 발목 없이 무릎만 보일 때
      허벅지 수직낙차비로 서기/앉기 추정(measured 276장 캘리브, th=0.40, 일치 86%).
      label — 위 둘 실패 시 CLIP pose_action 매핑(앉기→sit, 걷기/점프→stand, 기대기→mid).
    """
    import numpy as np
    ls, rs = _v(kp, L_SHOULDER), _v(kp, R_SHOULDER)
    lh, rh = _v(kp, L_HIP), _v(kp, R_HIP)

    # 자세 1) measured: 무릎 내각(양쪽 관측되면). 큰각=곧은다리=서기, 작은각=굽힘=앉기.
    ka = [x for x in (_interior(lh, _v(kp, L_KNEE), _v(kp, L_ANKLE)),
                      _interior(rh, _v(kp, R_KNEE), _v(kp, R_ANKLE))) if x is not None]
    if ka:
        m = sum(ka) / len(ka)
        posture, p_src = ("stand" if m > 155 else "sit" if m < 115 else "mid"), "measured"
    else:
        posture, p_src = _posture_fallback(kp, ls, rs, lh, rh, pose_action)

    # 팔: 손목이 어깨보다 위(y 작음)면 올림. 팔짱: 손목이 반대편으로 교차.
    lw, rw = _v(kp, L_WRIST), _v(kp, R_WRIST)
    sh_y = np.mean([p[1] for p in (ls, rs) if p is not None]) if (ls is not None or rs is not None) else None
    if sh_y is None:
        arms = "unknown"
    else:
        up = 0
        for w in (lw, rw):
            if w is not None and w[1] < sh_y - 0.03:
                up += 1
        crossed = (lw is not None and rw is not None and ls is not None and rs is not None
                   and lw[0] < rs[0] and rw[0] > ls[0])
        arms = "crossed" if crossed else ("both_up" if up == 2 else "one_up" if up == 1 else "down")

    # 정면: 양쪽 귀 보이고 어깨너비/몸통길이 비가 넓으면 정면, 아니면 측면.
    facing = "unknown"
    if ls is not None and rs is not None and lh is not None and rh is not None:
        torso_len = float(np.linalg.norm((ls + rs) / 2 - (lh + rh) / 2))
        sw = float(np.linalg.norm(ls - rs))
        ears = sum(1 for e in (_v(kp, L_EAR), _v(kp, R_EAR)) if e is not None)
        if torso_len > 1e-6:
            ratio = sw / torso_len
            facing = "front" if (ears == 2 and ratio > 0.75) else "side"

    lean = int(round(torso_deg)) if torso_deg is not None and math.isfinite(torso_deg) else 0
    return {"p": posture, "ps": p_src, "a": arms, "f": facing, "l": lean}


_POSTURE_KO = {"stand": "서기", "sit": "앉기", "mid": "중간자세", "unknown": "자세미상"}
_FACING_KO = {"front": "정면", "side": "측면", "unknown": "방향미상"}


def main() -> int:
    # 포즈 개념이 없는 도메인(음식·제품·풍경)에서는 할 일이 없다 — **정상 스킵**이다.
    # taxonomy._semantic 에 facing/framing 이 선언돼 있어야 인물 포즈 도메인이다.
    if not common.has_capability("pose"):
        print("포즈 도메인이 아닙니다 (taxonomy._semantic 에 facing/framing 없음) — 건너뜁니다.")
        return 0

    import numpy as np
    import pandas as pd

    if not INDEX_PATH.exists():
        print(f"{INDEX_PATH.name} 가 없습니다. 먼저 build_index 를 실행하세요.")
        return 1

    approved = {r["image_id"] for r in common.read_rows(common.MASTER_META)
                if r.get("status") == "approved"}
    df = pd.read_parquet(INDEX_PATH)
    df = df[df.image_id.isin(approved) & (df.pose_ok == True)].reset_index(drop=True)  # noqa: E712
    if len(df) == 0:
        # 포즈 데이터가 없다 = 인물 없는 도메인이거나 아직 build_index 를 안 돌린 것.
        # 어느 쪽이든 **에러가 아니다.** exit 1 을 내면 set -e 파이프라인이 여기서 멈춘다.
        print("승인·pose_ok 행이 없습니다 — 포즈 인덱스를 건너뜁니다.")
        return 0

    ids, F, M, attr, desc = [], [], [], {}, {}
    w = pdz.DEFAULT_WEIGHTS.astype(np.float64)
    for _, r in df.iterrows():
        kp = np.asarray(r["pose_kp"], dtype=float).reshape(33, 4)
        d = pdz.compute(kp)
        if bin(d.valid).count("1") < MIN_MATCH_FEATURES:
            continue
        ids.append(r["image_id"])
        f0 = np.nan_to_num(d.features.astype(np.float64), nan=0.0)
        F.append(f0)
        M.append([bool(d.valid >> i & 1) for i in range(pdz.N_FEATURES)])
        attr[r["image_id"]] = attributes(kp, d.torso_deg, r.get(POSE_AXIS) if POSE_AXIS else None)
        # 라이브 카메라 매칭용: 앱이 브라우저에서 같은 수식으로 거리 계산할 수 있게 디스크립터 동봉
        desc[r["image_id"]] = {"f": [round(float(x), 4) for x in f0], "v": int(d.valid)}

    n = len(ids)
    if n < 2:
        print("매칭 가능한 포즈가 2개 미만입니다.")
        return 1
    F = np.asarray(F)                    # (n,21) 무효칸 0
    M = np.asarray(M, dtype=bool)        # (n,21) 유효 마스크
    print(f"매칭 대상 {n}장 (승인·pose_ok {len(df)}장 중 유효특징>={MIN_MATCH_FEATURES})")

    # pose-knn: 마스크 가중 L2 (디스크립터 distance() 와 동일 수식, 벡터화)
    print("포즈 최근접 이웃...")
    knn = {}
    for i in range(n):
        cm = M & M[i]                    # (n,21) 공통 유효
        ws = (cm * w).sum(1)             # (n,)
        cnt = cm.sum(1)
        d2 = ((F - F[i]) ** 2 * w * cm).sum(1)
        dist = np.where(ws > 0, np.sqrt(d2 / np.where(ws > 0, ws, 1.0)), np.inf)
        dist[cnt < pdz.MIN_COMMON_FEATURES] = np.inf
        dist[i] = np.inf
        order = np.argsort(dist)[:K]
        knn[ids[i]] = [[ids[j], round(float(dist[j]), 4)]
                       for j in order if dist[j] <= DIST_MAX]

    # pose-clusters: 결측은 열평균 대체 후 sqrt(w) 스케일 → 가중 유클리드 = 표준 유클리드
    print("포즈 군집(k-means)...")
    col_mean = np.array([F[M[:, c], c].mean() if M[:, c].any() else 0.0
                         for c in range(pdz.N_FEATURES)])
    Fi = np.where(M, F, col_mean)                 # 결측 대체
    Xs = Fi * np.sqrt(w)
    from sklearn.cluster import KMeans
    k = min(N_CLUSTERS, n)
    km = KMeans(n_clusters=k, n_init=10, random_state=42).fit(Xs)
    lab = km.labels_
    cluster_of = {ids[i]: int(lab[i]) for i in range(n)}

    clusters = []
    for c in range(k):
        idx = np.where(lab == c)[0]
        if len(idx) == 0:
            continue
        d2c = ((Xs[idx] - km.cluster_centers_[c]) ** 2).sum(1)
        rep = ids[idx[int(d2c.argmin())]]
        pc = Counter(attr[ids[j]]["p"] for j in idx).most_common(1)[0][0]
        fc = Counter(attr[ids[j]]["f"] for j in idx).most_common(1)[0][0]
        clusters.append({"idx": int(c), "size": int(len(idx)), "rep": rep,
                         "posture": pc, "facing": fc,
                         "label": f"{_POSTURE_KO[pc]}·{_FACING_KO[fc]}"})
    clusters.sort(key=lambda x: -x["size"])

    out = {
        "meta": {"schema": "pose-index-0.2", "n": n, "k": K,
                 "min_features": MIN_MATCH_FEATURES, "dist_max": DIST_MAX,
                 "n_clusters": len(clusters), "descriptor": pdz.SCHEMA_VERSION},
        "knn": knn, "clusters": clusters, "cluster_of": cluster_of, "attr": attr,
        "desc": desc,   # {id:{f:[21], v:validmask}} — 포즈캠 실시간 매칭용
    }
    common.write_json_atomic(OUT_PATH, json.dumps(out, ensure_ascii=False))
    matched = sum(1 for v in knn.values() if v)
    ps = Counter(a["ps"] for a in attr.values())
    unk = sum(1 for a in attr.values() if a["p"] == "unknown")
    print(f"완료: {n}장 · 군집 {len(clusters)}개 · 이웃보유 {matched}장 → {OUT_PATH.name}")
    print(f"  자세출처: {dict(ps)} · 자세미상 {unk}/{n} = {unk/n*100:.0f}% "
          f"(보강 전 304/658=46%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
