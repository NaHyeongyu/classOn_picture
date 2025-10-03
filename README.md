 # 학원 사진 자동 분류·추천 시스템 (MVP)

 간단히: 폴더에 사진을 넣고 파이프라인을 실행하면, 얼굴을 검출하고 임베딩(ArcFace/InsightFace) → HDBSCAN으로 동일 인물 클러스터링 → 웃음/선명도 점수로 각 인물의 Top-N을 추천하여 JSON과 HTML 리포트를 생성합니다.

## 빠른 시작

- 요구사항: Python 3.10+
- 권장: macOS/Windows/Linux (CPU 실행)

옵션 A) 자동 스크립트/Makefile 사용
```bash
make setup                 # .venv 생성 및 의존성 설치
make run                   # INPUT/OUT 기본값으로 파이프라인 실행
# 커스텀 실행
make run INPUT=data/input OUT=data/output TOPK=3 MCS=5
make preview OUT=data/output
```

옵션 B) 수동 설치/실행
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

 # 기본 실행 (예시)
 python scripts/run_pipeline.py --input data/input --out data/output --topk 3 --min-cluster-size 5
python scripts/preview_clusters.py --out data/output
```

### 모델 다운로드
 - InsightFace는 최초 실행 시 자동으로 ONNX 모델을 다운로드합니다(~ 수백 MB). 네트워크가 제한된 환경이라면, 다음을 고려하세요.
   - 인터넷이 가능한 환경에서 한 번 실행하여 `~/.insightface`에 모델 캐시를 받은 뒤, 동일 경로를 복사하여 사용
  - 또는 사내 미러/프록시 사용

### Apple Silicon 참고
- `scripts/setup_venv.sh`는 macOS arm64에서 `onnxruntime` 설치 실패 시 자동으로 `onnxruntime-silicon`을 설치하고 나머지 패키지를 재설치합니다.

 ## 기능 개요

 1) EXIF 회전 보정 및 리사이즈(최대 변 1600px)
 2) InsightFace(ArcFace) 기반 얼굴 검출/정렬/512D 임베딩
 3) HDBSCAN으로 동일 인물 비지도 클러스터링(코사인 거리 ≒ L2 on unit vectors)
 4) 웃음 점수(OpenCV Haar Smile)와 선명도(Variance of Laplacian, (옵션)BRISQUE)
 5) score = 0.6*smile + 0.4*sharpness 로 Top-N 대표컷 선정
 6) 결과물
    - `data/output/faces/*.jpg` 얼굴 썸네일
    - `data/output/clusters.json` 클러스터별 멤버/추천
    - `data/output/report.html` 부트스트랩 카드 그리드 리포트
    - `data/output/grouped_photos/` 원본 사진을 클러스터별(person_XXX)/noise/no_face로 정리

## 디렉터리 구조

 ```
 project/
   README.md
   requirements.txt
   src/
     clustering/hdbscan_cluster.py
     detectors/face_detector.py
     embeddings/face_embedder.py
     quality/brisque.py
     quality/sharpness.py
     utils/image.py
     utils/fs.py
     utils/logging.py
     pipeline.py
     viz/report.py
   scripts/
     run_pipeline.py
     preview_clusters.py
  data/
    input/
    output/
  PROGRESS.md          # 사람용 체크리스트(작업 규정 포함)
  status.json          # 머신/CI용 상태값 (동일 작업 id/name/status)
 ```

## 실행 예시

```bash
python scripts/run_pipeline.py --input data/input --out data/output --topk 3 --min-cluster-size 5
python scripts/preview_clusters.py --out data/output
 # 원본을 복사 대신 심볼릭 링크로 정리하려면
 python scripts/run_pipeline.py --input data/input --out data/output --link-originals
```

### 웹 UI로 실행 (HTML 업로드/출력)
```bash
make web  # http://127.0.0.1:8000 접속 → 이미지 업로드 → 실행
```
웹 UI 기능:
- 이미지 업로드(여러 장), 파라미터 입력(Top-K, min_cluster_size)
- 파이프라인 실행 후 결과 리포트 바로 보기
- 이전 세션 목록/JSON/썸네일 접근

## Windows EXE 배포(원클릭 실행)

GitHub에 이 저장소를 올리면, 자동으로 Windows용 실행 파일(.exe)을 빌드하고 아티팩트로 제공합니다.

1) GitHub Actions 활성화
- `.github/workflows/windows-exe.yml`가 포함되어 있습니다.
- 저장소를 GitHub에 푸시하면 수동(workflow_dispatch) 또는 태그(vX.Y.Z)로 빌드가 실행됩니다.

2) 아티팩트 다운로드
- GitHub → Actions → build-windows-exe → Artifacts에서 `ClassOnFace-windows-exe` 다운로드
- 압축 해제 후 `ClassOnFace.exe` 실행 → 브라우저에서 `http://127.0.0.1:8000/` 접속
- 데이터 폴더는 실행 파일이 있는 경로 기준 `data/input`, `data/output`에 생성/사용됩니다.

3) 로컬에서 직접 빌드(선택)
```bat
REM Windows PowerShell/CMD
scripts\packaging\build_exe.bat
```
빌드 산출물: `dist\ClassOnFace.exe`

### 전체 자동화 스크립트 (윈도우)
```powershell
# 관리자 PowerShell 권장
Set-Location <repository-root>
.\scripts\packaging\auto_build.ps1
```
동작 순서: Python 미설치 시 winget으로 자동 설치 → `.venv` 생성 → 의존성/pyinstaller 설치 → `dist\ClassOnFace.exe` 빌드. winget이 없거나 설치 권한이 없다면 Python을 수동 설치한 뒤 다시 실행하면 나머지 단계가 그대로 이어집니다.

참고
- PyInstaller one-file 모드로 패키징됩니다. InsightFace/ONNX는 제외한 라이트 요구사항(`scripts/packaging/requirements-lite.txt`)으로 빌드하여 용량/의존성을 줄였습니다. 정확도가 더 필요하면 `requirements.txt`로 빌드해도 됩니다.
- 방화벽 경고가 뜨면 로컬 네트워크 허용을 선택하세요.
- 실행 중 파일은 현재 작업 폴더(`data/`)에 저장됩니다. 용량 관리를 위해 UI의 “이전 사진 삭제” 기능을 사용하세요.

## 참고/주석
 - InsightFace/ArcFace 임베딩(512D) 기반 얼굴 표현.
 - HDBSCAN으로 라벨 없는 동일인 군집화.
 - BRISQUE/LoG로 선명도 평가 및 추천.

## 트러블슈팅
 - onnxruntime 설치 이슈(macOS Apple Silicon): `onnxruntime` 최신 버전은 arm64 wheel을 제공합니다. 문제가 있으면 `pip install onnxruntime-silicon`을 시도하거나, `pip uninstall onnxruntime` 후 재설치.
 - 모델 다운로드 실패: 프록시 환경 변수(`HTTP_PROXY`, `HTTPS_PROXY`)를 설정하거나, 캐시 디렉토리(`~/.insightface`)를 미리 채워주세요.
- HDBSCAN 속도: 얼굴 수가 많아지면 시간 증가. 필요 시 `min_cluster_size`를 크게 하거나, 사전 필터링/샘플링.

## 운영 규정(Progress/Status 업데이트)
- 모든 작업 단위 시작/완료 시, `PROGRESS.md`와 `status.json` 두 파일을 동시 업데이트합니다.
- 상태 값: `TODO` | `IN_PROGRESS` | `DONE` | `BLOCKED`
- 두 파일의 작업 `id`/`name`을 동일하게 유지하여 추적성을 보장합니다.
- 작업 단위 종료 시, 두 파일 업데이트 후 즉시 커밋합니다. (권장: Conventional Commits 형식)
