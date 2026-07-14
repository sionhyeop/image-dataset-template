"""CLIP 기반 로컬 zero-shot 라벨러 (API 키 불필요).

프롬프트는 **이 파일에 없다.** 데이터셋 루트의 `prompts.yaml` 에서 읽는다.
축 코드별 영문 문장은 도메인마다 사람이 새로 써야 하지만(CLIP 은 한글이나 코드명으로는
성능이 안 나온다), **코드 안에 있을 이유는 없다.** YAML 로 빼면 코드는 안 건드린다.

prompts.yaml 구조:
    axes:      축 → {코드: 영문 문장}          (taxonomy 의 축·코드와 1:1)
    filters:   이름 → {positive, negative, ...} (이진 판정 → 점수/거절)

임계값(멀티축에서 몇 개를 고를지)은 손으로 찍지 않는다:
    python scripts/check/tune_labels.py --write  → annotations/label_profile.json
"""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
import common as _common  # noqa: E402

# 정확도 우선: 대형 모델(ViT-H-14). 환경변수로 교체 가능. 기본값은 common.py SSOT
MODEL_NAME = _common.CLIP_MODEL
PRETRAINED = _common.CLIP_PRETRAINED

# 멀티축 코드 선택 임계 — **손으로 찍지 말 것.** 축마다 최적값이 다르다.
# check/tune_labels.py 가 사람 정답으로 측정해 label_profile.json 에 써두면 여기서 읽는다.
MULTI_REL, MULTI_ABS_MIN, MULTI_MAX = 0.5, 0.10, 3
GROUP_MIN = 0.55          # 배타 그룹 안에서 이 확률 미만이면 '판단 보류'
_EXCLUSIVE = (_common.load_yaml(_common.TAXONOMY_PATH) or {}).get("_exclusive", {})

PROMPTS_PATH = _common.DATASET_ROOT / "prompts.yaml"


def load_prompts() -> dict:
    """prompts.yaml → {axes: {축: {코드: 문장}}, filters: {이름: {...}}}"""
    if not PROMPTS_PATH.exists():
        return {"axes": {}, "filters": {}}
    d = _common.load_yaml(PROMPTS_PATH) or {}
    return {"axes": d.get("axes") or {}, "filters": d.get("filters") or {}}


_P = load_prompts()
AXIS_PROMPTS: dict = _P["axes"]        # 축 → {코드: 영문}
FILTERS: dict = _P["filters"]          # 이름 → {positive, negative, field?, reject?, scale?}


def _load_profile() -> dict:
    p = _common.ANNOTATIONS / "label_profile.json"
    if not p.exists():
        return {}
    import json
    try:
        return (json.loads(p.read_text(encoding="utf-8")) or {}).get("axes", {})
    except Exception:
        return {}


_PROFILE = _load_profile()


class ClipLabeler:
    def __init__(self, device: str | None = None) -> None:
        import open_clip
        import torch

        self.torch = torch
        # 장치 선택: 명시값 > CUDA(GPU) > CPU
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        if device == "cuda":
            print(f"  GPU 사용: {torch.cuda.get_device_name(0)}")
        else:
            print("  CPU 사용 (CUDA 미탐지)")

        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            MODEL_NAME, pretrained=PRETRAINED)
        self.model.eval()
        self.model.to(self.device)
        tokenizer = open_clip.get_tokenizer(MODEL_NAME)

        # 축별 텍스트 임베딩을 미리 계산 (코드 순서 보존).
        # 축 이름·코드를 코드가 알 필요가 없다 — prompts.yaml 이 전부 말해준다.
        self.axes = {ax: (list(pm), self._encode_text(tokenizer, list(pm.values())))
                     for ax, pm in AXIS_PROMPTS.items() if pm}

        # 이진 판정 프롬프트 — [positive, negative] 쌍의 softmax 확률이 곧 점수(0~1).
        self.filters = {name: self._encode_text(tokenizer, [f["positive"], f["negative"]])
                        for name, f in FILTERS.items()
                        if f.get("positive") and f.get("negative")}

    def _encode_text(self, tokenizer, prompts: list[str]):
        with self.torch.no_grad():
            toks = tokenizer(prompts).to(self.device)
            feats = self.model.encode_text(toks)
            feats /= feats.norm(dim=-1, keepdim=True)
        return feats

    def _encode_image(self, path):
        from PIL import Image
        with Image.open(path) as im:
            img = self.preprocess(im.convert("RGB")).unsqueeze(0).to(self.device)
        with self.torch.no_grad():
            feat = self.model.encode_image(img)
            feat /= feat.norm(dim=-1, keepdim=True)
        return feat

    def _probs(self, img_feat, text_feat):
        # 코사인 유사도 -> softmax 확률
        logits = (100.0 * img_feat @ text_feat.T).softmax(dim=-1)
        return logits[0]

    def classify_axes(self, path, axes: list[str]) -> dict | None:
        """지정한 축들만 argmax 로 분류. enrich.py 가 derived/human 축을 채울 때 쓴다.

        예전엔 classify_person() 이 gender/expression 을 이름으로 박아 반환했다.
        어느 축을 뽑을지는 **호출자가 정한다** — 축 이름을 라벨러가 알 필요가 없다.
        """
        try:
            img = self._encode_image(path)
        except Exception:
            return None
        out = {}
        for ax in axes:
            if ax not in self.axes:
                continue
            codes, feats = self.axes[ax]
            out[ax] = codes[int(self._probs(img, feats).argmax())]
        return out

    def _multi(self, img_feat, axis: str, codes: list, feats) -> list:
        """멀티축 코드 선택 — 배타 그룹은 그룹 안에서만 경쟁시킨다.

        한 축에 성격이 다른 개념이 섞여 있으면(예: camera_style = 앵글 + 방향 + 무드)
        14개를 한 softmax 에 넣는 순간 '정면'이 '흑백'과 확률을 나눠 갖는다. 그러면
        어떤 코드는 임계를 영원히 못 넘어 **한 번도 예측되지 않는다**(실제로 S14_front_view·
        P10_standing 이 그랬다). taxonomy._exclusive 는 이미 '이 코드들은 서로 배타적인
        선택지'라고 선언하고 있으므로, 그 그룹을 하나의 독립 결정으로 보고 따로 softmax 한다.
        """
        # 임계는 축별로 다르다 — label_profile.json(측정값) > 모듈 기본값 순으로 쓴다.
        cfg = _PROFILE.get(axis, {})
        gmin = float(cfg.get("group_min", GROUP_MIN))
        rel = float(cfg.get("rel", MULTI_REL))
        absmin = float(cfg.get("abs_min", MULTI_ABS_MIN))
        mx = int(cfg.get("max_extra", MULTI_MAX))

        picked: list[str] = []
        grouped: set[str] = set()
        for grp in _EXCLUSIVE.get(axis, []):
            idx = [i for i, c in enumerate(codes) if c in grp]
            if len(idx) < 2:
                continue
            grouped.update(codes[i] for i in idx)
            sub = self._probs(img_feat, feats[idx])      # 그룹 안에서만 경쟁
            best = int(sub.argmax())
            if float(sub[best]) >= gmin:
                picked.append(codes[idx[best]])

        # 그룹에 속하지 않은 코드(무드 등)는 자기들끼리 상대 임계로 고른다.
        rest = [i for i, c in enumerate(codes) if c not in grouped]
        if rest and mx > 0:
            sub = self._probs(img_feat, feats[rest])
            pmax = float(sub.max())
            order = sorted(range(len(rest)), key=lambda i: -float(sub[i]))
            picked += [codes[rest[i]] for i in order
                       if float(sub[i]) >= max(absmin, rel * pmax)][:mx]

        if not picked:                                    # 최소 1개는 보장
            picked = [codes[int(self._probs(img_feat, feats).argmax())]]
        return picked

    def label(self, path) -> dict | None:
        try:
            img_feat = self._encode_image(path)
        except Exception:
            return None

        result: dict = {}

        # --- 축 라벨 -----------------------------------------------------------
        # 축의 단일/다중 여부는 taxonomy._axes 가 정한다(common.MULTI_AXES).
        # 'assisted' 축만 자동라벨한다 — derived(시스템이 채움)·human(사람 전용)은 건드리지 않는다.
        for axis, (codes, feats) in self.axes.items():
            if _common.AXIS_MODE.get(axis, "assisted") != "assisted":
                continue
            if axis not in _common.MULTI_AXES:
                result[axis] = codes[int(self._probs(img_feat, feats).argmax())]
            else:
                result[axis] = self._multi(img_feat, axis, codes, feats)

        # --- 이진 판정(필터) ----------------------------------------------------
        # prompts.yaml 의 filters 블록이 전부 말해준다. 무엇이 '주 피사체'인지도 거기 있다.
        for name, feats in self.filters.items():
            cfg = FILTERS[name]
            p = float(self._probs(img_feat, feats)[0])       # positive 쪽 확률
            sc = cfg.get("scale")
            if sc:                                            # 점수 스케일 변환 (예: 0~1 → 2~5)
                lo, hi = float(sc.get("lo", 0)), float(sc.get("hi", 1))
                result[cfg.get("field", name)] = int(round(lo + (hi - lo) * p))
            else:
                result[cfg.get("field", name)] = round(p, 3)
            rj = cfg.get("reject")
            if rj:                                            # 이 점수가 낮으면 버린다
                result.setdefault("_reject", {})[name] = {
                    "ok": p >= float(rj.get("min", 0.5)),
                    "note": rj.get("note", f"{name} 미달"),
                }

        result["watermark"] = False   # CLIP 으로 신뢰성 있게 판단하기 어려움 → 기본 false
        result["notes"] = "CLIP 자동 라벨(검수 필요)"
        return result
