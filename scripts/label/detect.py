"""객체 검출 — 큐레이션 게이트의 심장. 백엔드 교체 가능, 대상 클래스는 도메인이 정한다.

역할:
    "이 도메인의 주 피사체가 몇 개 있는가"를 센다. 이 하나로:
      · 수집 게이트 (person_score) — 버릴 것의 48.3% 를 사람 눈에 닿기 전에 차단
      · 개수 축 (person_count) — taxonomy._semantic.count 가 코드로 매핑

두 가지가 도메인 종속인데, 둘 다 설정으로 뺐다:
    ① 무엇을 셀 것인가 → taxonomy._curation.detect.classes  (person / cake / potted plant …)
    ② 배경의 자잘한 것을 셀 것인가 → min_area
       실측: YOLO 는 배경 행인까지 세서 과다계수 65건 vs 과소계수 2건으로 한쪽으로만 틀렸다.
       박스가 프레임의 5% 미만이면 배경으로 보고 세지 않는다 → 84.9% → 89.9%.
       **모델이 틀린 게 아니라, 모델이 답하는 질문과 우리가 묻는 질문이 달랐다.**

백엔드 (taxonomy._curation.detect.backend):
    rtdetr  — RT-DETR (Apache-2.0). **기본값.** 공개 배포에 안전하다.
    yolo    — YOLOv8n (ultralytics). ⚠ **AGPL-3.0** — 공개 저장소 + 웹배포면 전염 위험.
              설치돼 있을 때만 동작한다(requirements 에 없다).

사용:
    from detect import Detector
    det = Detector()                       # 백엔드·클래스·임계 전부 taxonomy 에서
    n = det.count(path)                    # 주 피사체 개수 (검출 불가면 None)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

# 기본값 — taxonomy._curation.detect 가 선언하면 그쪽이 이긴다.
# **classes 에는 기본값이 없다.** 'person' 을 기본으로 주면 식물·음식 도메인이 조용히
# 사람을 세게 된다. 무엇이 주 피사체인지는 도메인만 안다 — 선언이 없으면 검출을 끈다.
DEFAULT_BACKEND = "rtdetr"
DEFAULT_MIN_AREA = 0.05    # 프레임 면적 대비 (배경의 자잘한 객체 배제)
DEFAULT_CONF = 0.4

RTDETR_MODEL = "PekingU/rtdetr_r18vd_coco_o365"   # Apache-2.0
YOLO_WEIGHTS = "config/models/yolov8n.pt"          # AGPL-3.0 (선택)


def config() -> dict:
    """taxonomy._curation.detect — 무엇을, 얼마나 크게, 어느 모델로."""
    d = (common.load_curation().get("detect") or {})
    return {
        "backend": d.get("backend", DEFAULT_BACKEND),
        "classes": [str(c).lower() for c in (d.get("classes") or [])],   # 없으면 검출 비활성
        "min_area": float(d.get("min_area", DEFAULT_MIN_AREA)),
        "conf": float(d.get("conf", DEFAULT_CONF)),
    }


class Detector:
    """객체 검출기. 로드 실패하면 available=False 로 남고 count() 는 None 을 준다.

    실패를 예외로 올리지 않는 이유: 검출은 **선택 기능**이다. 없으면 그 점수만 0 이 되고
    나머지 파이프라인은 계속 돈다. 검출기 하나 때문에 수집·라벨링이 멈추면 안 된다.
    """

    def __init__(self, backend: str | None = None) -> None:
        c = config()
        self.classes = c["classes"]
        self.min_area = c["min_area"]
        self.conf = c["conf"]
        self.backend = backend or c["backend"]
        self.available = False
        self._impl = None
        if not self.classes:
            print("  taxonomy._curation.detect.classes 가 없습니다 — 객체 검출을 건너뜁니다.")
            return
        try:
            if self.backend == "yolo":
                self._load_yolo()
            else:
                self._load_rtdetr()
            self.available = True
        except Exception as e:
            print(f"  객체 검출 사용 불가 ({self.backend}): {type(e).__name__} {e}")

    # --- 백엔드 -------------------------------------------------------------
    def _load_rtdetr(self) -> None:
        import torch
        from transformers import AutoImageProcessor, AutoModelForObjectDetection
        self.torch = torch
        self.dev = "cuda" if torch.cuda.is_available() else "cpu"
        self.proc = AutoImageProcessor.from_pretrained(RTDETR_MODEL)
        m = AutoModelForObjectDetection.from_pretrained(RTDETR_MODEL).to(self.dev).eval()
        self._impl = m
        # 클래스 이름 → id (모델마다 id 가 다르므로 이름으로 찾는다)
        self.ids = {i for i, n in m.config.id2label.items()
                    if str(n).lower() in self.classes}
        if not self.ids:
            raise ValueError(f"모델에 없는 클래스: {self.classes} "
                             f"(가능한 것 예: {list(m.config.id2label.values())[:8]})")

    def _load_yolo(self) -> None:
        from ultralytics import YOLO       # ⚠ AGPL-3.0
        import torch
        self.torch = torch
        self.dev = 0 if torch.cuda.is_available() else "cpu"
        m = YOLO(str(common.ROOT / YOLO_WEIGHTS))
        self._impl = m
        names = {str(v).lower(): k for k, v in m.names.items()}
        self.ids = {names[c] for c in self.classes if c in names}
        if not self.ids:
            raise ValueError(f"모델에 없는 클래스: {self.classes}")

    # --- 추론 ---------------------------------------------------------------
    def count(self, path) -> int | None:
        """주 피사체 개수. 검출기가 없으면 None(=모름), 있으면 0 이상."""
        if not self.available:
            return None
        try:
            return (self._count_yolo(path) if self.backend == "yolo"
                    else self._count_rtdetr(path))
        except Exception:
            return None

    def _count_rtdetr(self, path) -> int:
        from PIL import Image
        with Image.open(path) as im:
            im = im.convert("RGB")
            W, H = im.size
            inp = self.proc(images=im, return_tensors="pt").to(self.dev)
        with self.torch.no_grad():
            out = self._impl(**inp)
        res = self.proc.post_process_object_detection(
            out, target_sizes=self.torch.tensor([[H, W]]), threshold=self.conf)[0]
        area = float(W * H)
        n = 0
        for sc, lb, box in zip(res["scores"], res["labels"], res["boxes"]):
            if int(lb) not in self.ids:
                continue
            x0, y0, x1, y1 = [float(v) for v in box]
            if (x1 - x0) * (y1 - y0) / area >= self.min_area:
                n += 1
        return n

    def _count_yolo(self, path) -> int:
        res = self._impl(str(path), verbose=False, device=self.dev,
                         conf=self.conf, classes=sorted(self.ids))
        n = 0
        for r in res:
            h, w = r.orig_shape
            area = float(h * w)
            n += sum(1 for b in r.boxes.xyxy.tolist()
                     if (b[2] - b[0]) * (b[3] - b[1]) / area >= self.min_area)
        return n
