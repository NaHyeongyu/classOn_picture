# PROGRESS.md — 사람용 작업 체크리스트

이 문서는 사람이 읽고 업데이트하는 작업 진행 체크리스트입니다. 모든 작업(기능 추가/수정/문서/실험 등)을 수행할 때마다 이 파일과 `status.json` 둘 다를 함께 업데이트합니다.

## 업데이트 규정
- 업데이트 대상: PROGRESS.md(사람용), status.json(머신/CI용)
- 업데이트 시점: 작업 시작 시 상태를 `IN_PROGRESS`로, 작업 완료 시 `DONE`으로 두 파일 동시 변경
- 상태 값: `TODO` | `IN_PROGRESS` | `DONE` | `BLOCKED`
- 기록 방식(권장):
  - PROGRESS.md: 체크박스/섹션에 상태와 간단 코멘트
  - status.json: 동일 작업의 `id`, `name`, `status`를 일관되게 유지
- 단위: 의미 있는 단위(feature/bugfix/doc/데이터 준비 등)로 쪼개고, 작업 `id`는 짧고 고유하게 유지

## 현재 상태(초기화)

- [x] 1-setup: 기본 세팅 (requirements/구조/README/스크립트)
- [x] 2-detect-embed: 얼굴 검출·임베딩 (InsightFace + 오프라인 대체)
- [x] 3-hdbscan: 클러스터링(HDBSCAN, 코사인=L2 on unit vec)
- [x] 4-quality: 품질 지표(LoG 샤프니스, (옵션)BRISQUE)
- [x] 5-smile: 웃음/표정(OpenCV Smile Cascade)
- [x] 6-topn-report: Top‑N 추천 & HTML 리포트
- [ ] 7-demo: 테스트 데이터 & 데모(샘플 이미지 준비 및 e2e 검증)
 - [x] 8-venv-run: 가상환경 설치 스크립트 및 Makefile 실행 타겟 추가

## 변경 로그(Summary)

- DONE: 초기 MVP 파이프라인 구성 및 실행 스크립트 구현
  - run: `python scripts/run_pipeline.py --input data/input --out data/output`
  - preview: `python scripts/preview_clusters.py --out data/output`
- PENDING: 샘플 데이터 10~30장 추가 및 경계 사례 로그 수집(7-demo)
 - DONE: venv/실행 자동화 (`scripts/setup_venv.sh`, `Makefile`의 `setup/run/preview`)
