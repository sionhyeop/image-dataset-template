"""템플릿화 회귀 테스트 — "설정만 갈아끼우면 코드는 안 건드린다"가 참인지 기계가 확인한다.

왜 필요한가:
    docs/TEMPLATE.md 는 오랫동안 "scripts/ 는 건드리지 않는다. 축 이름·코드가 코드에
    하드코딩돼 있지 않다"고 주장했다. **거짓이었다.** 인물과 무관한 taxonomy 를 넣으면
    검수앱은 로드 즉시 TypeError 로 죽고, rank/auto_label/validate/check_consistency 는
    KeyError 로 죽었다. 아무도 몰랐던 이유는 **아무도 시험해보지 않았기 때문**이다.

    문서의 주장은 테스트로 지켜야 한다. 안 그러면 다음에도 믿고 다음에도 배신당한다.

무엇을 하나:
    tests/fixtures/domain_min/ 의 가짜 도메인(관엽식물 — 인물 흔적 0)으로
    DATASET_DIR 을 바꿔 전 스크립트를 돌린다. **실제 이미지는 필요 없다.**
    축 이름을 리터럴로 잡는 코드가 있으면 여기서 걸린다.

실행:
    python -m pytest tests/test_template.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = "datasets/example_plants"


def run(*args: str, dataset: str = FIXTURE, timeout: int = 180):
    """가짜 도메인 환경에서 파이썬 코드를 별도 프로세스로 실행.

    common.py 가 import 시점에 taxonomy 를 읽으므로, 같은 프로세스에서 DATASET_DIR 을
    바꿔도 이미 로드된 모듈에는 안 먹힌다. 그래서 반드시 서브프로세스로 돌린다.
    """
    env = {**os.environ, "DATASET_DIR": dataset, "PYTHONPATH": str(ROOT / "scripts")}
    return subprocess.run([sys.executable, *args], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=timeout)


def py(code: str, dataset: str = FIXTURE, timeout: int = 180):
    return run("-c", code, dataset=dataset, timeout=timeout)


@pytest.fixture(scope="session")
def populated_fixture():
    """가짜 도메인의 검수앱을 빌드한다 (진짜 식물 사진 24장 · 442KB, 저장소에 커밋돼 있다).

    왜 데이터가 있어야 하는가:
        이 픽스처는 오랫동안 0행이었다. "이미지는 필요 없다 — 앱이 로드되는지만 본다"는
        생각이었는데, 그 결과 **앱이 로드는 되지만 틀리게 도는** 버그를 하나도 못 잡았다.
        인테리어 도메인을 손으로 돌리고서야 두 개가 나왔다:
          · 대시보드 타일이 "구도 카테고리 ≥10" 을 하드코딩 (도메인 이름·임계값 둘 다 거짓)
          · payload 가 단일축을 문자열로 싣는데 앱 테스트가 배열만 가정
        payload 가 비면 verify_app.js 는 검수 주입 대상을 못 골라 아예 못 돈다.
        **빈 테스트는 통과하는 게 아니라 아무것도 안 보는 것이다.**

    왜 단색 사각형이 아니라 진짜 사진인가:
        열어봐서 확인할 수 없는 산출물은 신뢰받지 못한다. 이미지는 위키미디어 공용에서
        **재배포 허용 라이선스만**(CC0/PD/CC BY(-SA)) 골라 받았고 출처·저작자가 CSV 에 있다.
        커밋돼 있으므로 테스트는 네트워크가 필요 없다.
        (재수집: python tests/fixtures/fetch_fixture_images.py)
    """
    fx = ROOT / FIXTURE
    imgs = sorted((fx / "data" / "01_curated" / "images").glob("*.jpg"))
    assert len(imgs) >= 20, (
        f"픽스처 이미지가 {len(imgs)}장뿐이다 — python tests/fixtures/fetch_fixture_images.py 로 받아라")
    (fx / "data" / "04_samples").mkdir(parents=True, exist_ok=True)

    build = run("scripts/review/app.py")
    assert build.returncode == 0, f"픽스처 앱 빌드 실패:\n{build.stderr[-800:]}"
    out = fx / "data" / "04_samples" / "index.html"
    assert out.exists()
    return out


# --- 1. 스키마 파생 ----------------------------------------------------------
def test_axes_derive_from_fixture():
    """축·모드·커버리지축이 전부 taxonomy 에서 파생되는가 (코드에 리터럴이 없는가)."""
    r = py("import common,json;print(json.dumps({'axes':common.LABEL_AXES,"
           "'comp':list(common.COMP_AXES),'multi':sorted(common.MULTI_AXES)}))")
    assert r.returncode == 0, r.stderr
    d = json.loads(r.stdout.strip().splitlines()[-1])
    assert d["axes"] == ["species", "setting", "lighting", "health"]
    assert d["comp"] == ["species", "setting", "lighting"]
    assert d["multi"] == ["lighting", "setting"]
    # 인물 축의 흔적이 남아 있으면 안 된다
    assert not any(a in d["axes"] for a in ("where", "pose_action", "gender", "person_count"))


def test_master_fields_derive_from_axes():
    """CSV 스키마(MASTER_FIELDS)도 축에서 파생돼야 한다.

    지금은 where/pose_action/gender/person_count/korean_prob/insta_prob 가 리터럴로 박혀
    있어, 새 도메인의 축(species 등)이 CSV 에 아예 저장되지 않는다.
    """
    r = py("import common;print(','.join(common.MASTER_FIELDS))")
    assert r.returncode == 0, r.stderr
    fields = r.stdout.strip().splitlines()[-1].split(",")
    for ax in ("species", "setting", "lighting", "health"):
        assert ax in fields, f"MASTER_FIELDS 에 새 도메인 축 '{ax}' 가 없다 — CSV 에 저장 안 됨"
    for ghost in ("where", "pose_action", "gender", "person_count"):
        assert ghost not in fields, f"MASTER_FIELDS 에 옛 도메인 축 '{ghost}' 가 남아 있다"


# --- 2. 전 스크립트이 import 되는가 ------------------------------------------
def test_all_scripts_import():
    """축 이름을 모듈 최상단에서 접근하는 코드가 있으면 여기서 터진다."""
    code = (
        "import importlib,pathlib,sys\n"
        "scripts=pathlib.Path('scripts'); sys.path.insert(0,str(scripts))\n"
        "bad=[]\n"
        "for p in sorted(scripts.rglob('*.py')):\n"
        "    if p.name=='__init__.py' or 'node_modules' in str(p): continue\n"
        "    mod='.'.join(p.relative_to(scripts).with_suffix('').parts)\n"
        "    sys.path.insert(0,str(p.parent))\n"
        "    try: importlib.import_module(mod)\n"
        "    except ModuleNotFoundError as e:\n"
        "        pass\n"                       # 외부 패키지 미설치는 무시
        "    except Exception as e: bad.append(f'{mod}: {type(e).__name__} {e}')\n"
        "    finally: sys.path.pop(0)\n"
        "print('BAD='+repr(bad))\n"
    )
    r = py(code)
    assert r.returncode == 0, r.stderr
    bad = eval(r.stdout.strip().splitlines()[-1].split("BAD=", 1)[1])
    assert not bad, "새 도메인에서 import 실패:\n  " + "\n  ".join(bad)


# --- 3. 정합성 검사기가 새 도메인을 이해하는가 --------------------------------
def test_check_consistency_no_crash():
    """check_consistency 가 축 이름을 리터럴로 잡으면 KeyError 로 죽는다.

    (프롬프트 불일치 등으로 '실패'하는 건 괜찮다 — 예외로 죽으면 안 된다는 뜻)
    """
    r = run("scripts/check/check_consistency.py")
    assert "KeyError" not in r.stderr and "Traceback" not in r.stderr, \
        f"check_consistency 가 새 도메인에서 예외:\n{r.stderr[-800:]}"


def test_validate_no_crash():
    r = run("scripts/check/validate.py")
    assert "KeyError" not in r.stderr and "Traceback" not in r.stderr, \
        f"validate 가 새 도메인에서 예외:\n{r.stderr[-800:]}"


# --- 4. 검수앱이 로드되는가 (가장 치명적) -------------------------------------
def test_app_builds_and_loads(tmp_path, populated_fixture):
    """앱을 node 로 실제 로드해 JS 에러가 없는지 본다.

    실제로 app.js 는 renderDash() 가 ['where','pose_action',...] 를 리터럴로 잡고
    TAX.where 에 접근해 **로드 즉시 TypeError** 로 죽었다. 화면이 통째로 안 떴다.

    (예전엔 여기서 master CSV 를 빈 배열로 덮어썼다. 그게 픽스처를 0행으로 만든 범인이고,
     그래서 이 테스트는 '로드는 되는데 틀리게 도는' 버그를 영영 못 봤다. 이제 안 덮어쓴다.)
    """
    out = populated_fixture

    # node 로 실제 실행 — 축 이름 하드코딩이 있으면 TypeError 가 난다
    harness = tmp_path / "load.js"
    harness.write_text(r"""
const fs=require('fs'),vm=require('vm');
const html=fs.readFileSync(process.argv[2],'utf8');
const s=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
function el(){const e={style:{setProperty(){}},dataset:{},innerHTML:'',textContent:'',value:'',
  classList:{add(){},remove(){},toggle(){},contains:()=>false},querySelector:()=>el(),
  querySelectorAll:()=>[],appendChild(){},addEventListener(){},insertBefore(){},remove(){},
  insertAdjacentHTML(){},children:[],previousSibling:null,
  getContext:()=>new Proxy({},{get:(t,p)=>(p==='canvas'?e:()=>{})}),getBoundingClientRect:()=>({width:100,height:100})};
  return e;}
Object.assign(globalThis,{document:{getElementById:()=>el(),querySelector:()=>el(),
  querySelectorAll:()=>[],createElement:()=>el(),head:{appendChild(){}},body:{appendChild(){}},
  addEventListener(){}},window:globalThis,localStorage:{getItem:()=>null,setItem(){}},
  requestAnimationFrame:()=>{},innerWidth:1200,innerHeight:800,prompt:()=>'t',confirm:()=>true,
  atob:x=>Buffer.from(x,'base64').toString('binary')});
// node 의 navigator 는 getter 전용이라 Object.assign 으로 못 덮는다 — 따로 정의한다
Object.defineProperty(globalThis,'navigator',{value:{},configurable:true});
vm.runInThisContext(s[0]);
vm.runInThisContext(s[1]);   // 여기서 축 이름 하드코딩이 있으면 죽는다
console.log('OK');
process.exit(0);   // 앱이 건 타이머(Firebase presence 등)가 이벤트 루프를 붙잡지 않게
""", encoding="utf-8")
    n = subprocess.run(["node", str(harness), str(out)], capture_output=True, text=True, timeout=120)
    assert n.returncode == 0 and "OK" in n.stdout, \
        f"검수앱이 새 도메인에서 로드 실패 (축 이름 하드코딩):\n{n.stderr[-1200:]}"


def test_app_behaves_on_fake_domain(populated_fixture):
    """앱이 **로드되는 것과 맞게 도는 것은 다르다.**

    위 test_app_builds_and_loads 는 로드만 본다. 그것만으로는 잡히지 않은 버그들:
      · 대시보드 타일이 "구도 카테고리 ≥10" 하드코딩 — 도메인 이름도 임계값도 거짓
      · payload 는 단일축을 문자열로 싣는데 앱 테스트가 배열만 가정
    둘 다 인테리어 도메인을 손으로 돌려서야 나왔다. 이제 CI 가 잡는다.

    verify_app.js 는 대시보드·조합 패널을 실제 집계와 대조한다(16케이스).
    """
    n = subprocess.run(["node", "tests/js/verify_app.js", str(populated_fixture)],
                       cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert n.returncode == 0, (
        "가짜 도메인에서 앱이 틀리게 돈다 (축 이름 하드코딩 / 단일축 처리 누락):\n"
        + (n.stdout + n.stderr)[-1500:])
    assert "FAIL 0" in n.stdout or "FAIL" not in n.stdout, n.stdout[-1200:]


def test_dashboard_tile_has_no_domain_literal(populated_fixture):
    """대시보드가 도메인 이름·임계값을 지어내지 않는가.

    실제로 앱은 어떤 도메인에서든 "구도 카테고리 ≥10" 이라고 적고 있었다.
    식물 도메인의 _coverage_min 은 5 다 — 화면이 거짓말을 하면 안 된다.
    """
    # 타일은 브라우저에서 렌더되므로 정적 HTML 에는 템플릿 리터럴이 남는다.
    # 임계값이 `${TK_MIN}` 이면 taxonomy 를 따르는 것이고, 숫자면 지어낸 것이다.
    html = populated_fixture.read_text(encoding="utf-8")
    assert "구도 카테고리" not in html, "대시보드에 '구도'(인물 도메인 이름)가 박혀 있다"
    assert "충분한 코드 ≥${TK_MIN}" in html, \
        "커버리지 임계값이 상수로 박혀 있다 — taxonomy._coverage_min 을 따라야 한다"
    assert '"COVERAGE_MIN": 5' in html, "payload 가 이 도메인의 _coverage_min(5) 을 안 싣는다"


# --- 5. 인물 없는 도메인에서 파이프라인이 데이터를 버리지 않는가 ---------------
def test_no_person_gate_by_default():
    """인물 게이트가 taxonomy 선언 없이 켜지면, 인물 없는 도메인은 데이터가 전량 차단된다.

    실측: rank.py 는 사람이 없으면 person_score = -3 을 주고, _curation.gates 가
    person_score >= 1.0 을 요구하면 **전 이미지가 버려진다.**
    """
    r = py("import common;c=common.load_curation();"
           "print('GATES='+repr([g.get('signal') for g in c.get('gates',[])]))")
    assert r.returncode == 0, r.stderr
    gates = eval(r.stdout.strip().splitlines()[-1].split("GATES=", 1)[1])
    assert "person_score" not in gates, \
        "인물 게이트가 인물 없는 도메인에 켜져 있다 — 데이터가 전량 차단된다"


@pytest.mark.parametrize("script", [
    "scripts/label/enrich.py",          # YOLO person 검출 — 인물 전용
    "scripts/index/pose_match.py",      # BlazePose — 인물 전용
])
def test_person_only_scripts_skip_gracefully(script):
    """인물 전용 스크립트는 인물 없는 도메인에서 **정상 종료(0)** 해야 한다.

    지금 pose_match.py 는 'pose_ok 행이 없습니다' 를 찍고 return 1 한다. set -e 파이프라인이면
    거기서 멈춘다. '자동 비활성'이 아니라 '에러 종료'다.
    """
    r = run(script, timeout=300)
    assert r.returncode == 0, (
        f"{script} 가 인물 없는 도메인에서 비정상 종료(exit {r.returncode}).\n"
        f"자동으로 건너뛰어야 한다.\n{(r.stdout + r.stderr)[-600:]}")
