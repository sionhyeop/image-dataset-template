# 실시간 협업 검수 설정 (Firebase) — 서버 없이

통합 앱(`data/04_samples/index.html`)은 **Firebase 설정이 있으면 실시간 협업**,
없으면 로컬 모드(CSV 내보내기)로 동작한다. Firebase는 우리가 서버를 돌리지 않는
관리형 서비스(무료 티어)이고, 클라이언트 SDK만으로 팀원들의 검수가 실시간 동기화된다.

## 무엇이 실시간으로 되나
- 팀원 A가 라벨을 고치거나 "검수완료/버리기"를 누르면, **다른 팀원 화면에 즉시 반영**된다.
- 상단에 **현재 접속자 수·이름**이 표시된다.
- CSV 내보내기 없이 결정이 Firestore에 바로 저장된다(각자 취합 불필요).

## 설정 (약 5분, 최초 1회)

1. https://console.firebase.google.com 에서 **프로젝트 생성** (무료 Spark 요금제로 충분).
2. 좌측 **빌드 → Firestore Database → 데이터베이스 만들기** (테스트 모드로 시작).
3. 프로젝트 설정(⚙️) → **내 앱 → 웹 앱 추가(</>)** → 앱 등록 →
   보여주는 `firebaseConfig` 객체의 값을 복사.
4. 그 값을 **`config/firebase.json`** 으로 저장 (형식은 `config/firebase.example.json` 참고).
5. 앱 재생성: `python scripts/review/app.py` → "실시간 협업: ON" 이 뜨면 성공.
6. `data/04_samples/index.html`(또는 `sites/<이름>/`)을 배포/공유.

## Firestore 보안 규칙

기본 테스트 모드는 30일 후 막힌다. 내부 팀 검수용으로 아래처럼
`reviews`/`presence` 컬렉션만 열어두면 된다 (콘솔 → Firestore → 규칙):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{db}/documents {
    match /reviews/{id}   { allow read, write: if true; }
    match /presence/{id}  { allow read, write: if true; }
  }
}
```

주의: 위 규칙은 **URL·설정을 아는 사람은 누구나 쓰기 가능**하다(인증 없음).
내부 링크 공유용이면 무방하지만, 외부 노출이 걱정되면:
- Vercel **Deployment Protection**(비밀번호/SSO)으로 접근 자체를 제한하거나,
- Firebase **Anonymous Auth**를 켜고 규칙을 `if request.auth != null` 로 바꾼다.

## 데이터 구조 (참고)
- `reviews/{image_id}` = `{ labels, decision(keep|discard), reviewer, at }`
- `presence/{sessionId}` = `{ reviewer, ts }` (60초 이상 무응답이면 오프라인 처리)

## 검수 결과를 데이터셋에 반영
실시간이든 로컬이든, 최종 반영은 CSV로:
- 로컬 모드: 앱에서 **CSV 내보내기** → `apply_review.py review_decisions.csv`
- Firebase 모드: Firestore `reviews` 컬렉션을 내보내거나, 아무 브라우저에서 앱을 열어
  (동기화된 상태에서) **CSV 내보내기** → `apply_review.py`
`apply_review.py`가 라벨 수정·검수완료·버리기(status=discarded)를 master에 반영한다.
