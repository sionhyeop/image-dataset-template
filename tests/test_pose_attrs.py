"""pose_match 자세 3단 폴백(geom/label/none) — 합성 키포인트 검증."""
import numpy as np

from index import pose_match as pm


def _kp(knee_y=None):
    """33×4 합성 키포인트. knee_y 지정 시 무릎 가시화, 나머지는 비가시."""
    kp = np.zeros((33, 4))
    if knee_y is not None:
        for i in (pm.L_KNEE, pm.R_KNEE):
            kp[i] = [0.5, knee_y, 0, 1.0]
    return kp


SH = np.array([0.5, 0.2])   # 어깨
HP = np.array([0.5, 0.5])   # 힙 (몸통길이 0.3)


def test_geom_stand():
    # 무릎이 힙보다 몸통 0.4배 이상 아래 → 서기
    p, src = pm._posture_fallback(_kp(knee_y=0.9), SH, SH, HP, HP, None)
    assert (p, src) == ("stand", "geom")


def test_geom_sit():
    # 무릎이 힙 높이 근처 → 앉기
    p, src = pm._posture_fallback(_kp(knee_y=0.55), SH, SH, HP, HP, None)
    assert (p, src) == ("sit", "geom")


def test_label_fallback():
    p, src = pm._posture_fallback(_kp(), SH, SH, HP, HP, "P01_sitting")
    assert (p, src) == ("sit", "label")


def test_label_multi_first_postural():
    # 멀티라벨: 자세 무관(P07) 건너뛰고 첫 자세성 코드(P03) 사용
    p, src = pm._posture_fallback(_kp(), SH, SH, HP, HP,
                                  "P07_using_props;P03_walking_snap")
    assert (p, src) == ("stand", "label")


def test_unknown_when_no_signal():
    p, src = pm._posture_fallback(_kp(), None, None, None, None, "P07_using_props")
    assert (p, src) == ("unknown", "none")


def test_geom_beats_label():
    # 기하 신호가 있으면 라벨보다 우선
    p, src = pm._posture_fallback(_kp(knee_y=0.9), SH, SH, HP, HP, "P01_sitting")
    assert (p, src) == ("stand", "geom")
