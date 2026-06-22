# PC search all file

이 폴더는 내 PC 안의 파일을 더 빠르게 찾기 위한 파일 검색 도구입니다.

일반적인 파일 검색처럼 매번 모든 폴더를 처음부터 뒤지는 방식이 아니라, 먼저 PC의 파일 목록을 한 번 정리해 둔 뒤 그 목록에서 빠르게 검색하는 구조입니다.

## 이 폴더가 하는 일

이 도구는 크게 네 가지 일을 합니다.

1. PC의 드라이브가 검색 가능한 상태인지 확인합니다.
2. 선택한 위치에 파일과 폴더가 몇 개 있는지 셉니다.
3. 파일 이름, 확장자, 경로 정보를 색인 파일로 저장합니다.
4. 저장된 색인을 이용해 파일을 빠르게 찾습니다.

여기서 색인이란 파일의 위치를 빨리 찾기 위해 미리 만들어 두는 목록입니다. 실제 파일을 복사하거나 옮기는 것이 아니라, 파일 이름과 경로 같은 검색용 정보만 정리합니다.

## 사용 목적

이 폴더는 다음과 같은 상황을 위해 만들어졌습니다.

- PC 전체 또는 특정 폴더에서 파일을 빠르게 찾고 싶을 때
- 파일 개수가 많아서 매번 검색이 오래 걸릴 때
- 검색 전에 C 드라이브나 D 드라이브가 실제로 접근 가능한지 확인하고 싶을 때
- 검색 과정에서 접근 권한이 없는 폴더가 있어도 전체 작업이 멈추지 않게 하고 싶을 때

## 전체 흐름

기본 흐름은 아래 순서입니다.

1. `diskcheck`: C 드라이브, D 드라이브 같은 저장 장치가 보이는지 확인합니다.
2. `counting`: 검색할 위치의 파일 또는 폴더 개수를 셉니다.
3. `search`: 파일 목록 색인을 만들고, 그 색인에서 파일을 찾습니다.
4. `require`: 위 과정을 정해진 순서대로 실행하는 상위 실행 흐름입니다.

즉, 이 폴더의 핵심 설계는 "요청 확인 -> 개수 확인 -> 검색 실행"입니다.

## 폴더 구성

```text
logic/
  diskcheck/  PC 드라이브가 검색 가능한 상태인지 확인
  counting/   파일과 폴더 개수 계산
  search/     파일 색인 생성 및 검색
  require/    검색 요청을 검증하고 전체 실행 순서를 관리
```

## 각 기능 설명

### `logic/diskcheck`

PC의 드라이브 상태를 확인합니다.

예를 들어 D 드라이브가 없는 PC에서는 D 드라이브를 검색하려고 시도하기 전에 "보이지 않는 드라이브"로 판단할 수 있습니다. USB, 네트워크 드라이브, CD-ROM 같은 장치는 기본 검색 대상에서 제외될 수 있습니다.

### `logic/counting`

선택한 위치에 파일이 몇 개 있는지 셉니다.

기본적으로 Windows 시스템 폴더 일부는 제외합니다. 예를 들어 `C:\Windows`, `C:\Program Files`, `C:\ProgramData` 같은 폴더는 일반 파일 검색 목적에서는 필요하지 않거나 접근 오류가 많기 때문입니다.

### `logic/search`

실제 파일 검색의 중심 기능입니다.

처음에는 지정한 폴더를 훑어서 파일 목록 색인을 만듭니다. 이후에는 그 색인을 사용해 파일 이름, 확장자, 경로 조각으로 검색합니다.

검색할 수 있는 예시는 다음과 같습니다.

- 파일 이름에 `report`가 들어간 파일
- `.pdf` 확장자를 가진 파일
- 특정 폴더 경로 안에 있는 파일
- 정확히 같은 파일 이름을 가진 파일

### `logic/require`

사용자의 검색 요청을 먼저 확인하고, 정해진 순서대로 실행합니다.

이 폴더는 아래 순서를 보장합니다.

```text
요청 확인 -> 파일 개수 확인 -> 검색 실행
```

상위 프로그램이나 화면이 이 검색 기능을 사용할 때는 이 흐름을 통하는 것이 가장 안전합니다.

## 실행 예시

아래 명령은 PowerShell에서 실행하는 예시입니다.

현재 폴더 안에서 `report`라는 이름이 들어간 파일을 찾습니다.

```powershell
python logic\require\engine.py "report" "." --json
```

현재 폴더의 파일 개수를 셉니다.

```powershell
python logic\counting\counter.py "."
```

검색용 색인을 만듭니다.

```powershell
python logic\search\searcher.py index "." --output search-index.json
```

저장된 색인에서 `report`를 찾습니다.

```powershell
python logic\search\searcher.py find "report" --index search-index.json
```

확장자가 PDF인 파일을 찾습니다.

```powershell
python logic\search\searcher.py find ".pdf" --mode extension --index search-index.json
```

## 안전 동작

이 도구는 검색과 확인을 위한 도구입니다.

- 파일을 삭제하지 않습니다.
- 파일을 이동하지 않습니다.
- 파일 내용을 수정하지 않습니다.
- 접근할 수 없는 폴더가 있어도 전체 검색을 중단하지 않고 오류 목록에 기록합니다.
- 폴더 바로가기를 따라가며 무한 반복되는 상황을 피하도록 설계되어 있습니다.
- 기본 설정에서는 Windows 설치 및 시스템 폴더 일부를 제외합니다.

## 주의할 점

- 처음 색인을 만들 때는 파일 수가 많을수록 시간이 걸릴 수 있습니다.
- 색인을 만든 뒤 파일이 새로 생기거나 삭제되면 색인이 오래된 상태가 될 수 있습니다.
- 저장된 색인을 사용하는 검색은 필요할 때 자동으로 새로 고칠 수 있습니다.
- 이 도구는 파일 이름, 확장자, 경로 중심 검색입니다. 문서 안의 본문 내용까지 검색하는 도구는 아닙니다.
- 접근 권한이 없는 폴더는 검색 결과에 포함되지 않을 수 있습니다.

## 검증 상태

각 기능 폴더에는 테스트 파일이 함께 있습니다.

```text
logic/diskcheck/test_diskcheck.py
logic/counting/test_counter.py
logic/search/test_searcher.py
logic/require/test_engine.py
```

테스트는 드라이브 판별, 파일 개수 세기, 색인 저장과 검색, 오래된 색인의 새로 고침, 검색 실행 순서 등을 확인합니다.

테스트 실행 예시는 다음과 같습니다. 이 테스트들은 각 기능 폴더 안에서 실행하는 방식으로 작성되어 있습니다.

```powershell
cd logic\diskcheck
python -m unittest test_diskcheck.py

cd ..\counting
python -m unittest test_counter.py

cd ..\search
python -m unittest test_searcher.py

cd ..\require
python -m unittest test_engine.py
```

## 한 줄 요약

이 폴더는 PC의 파일을 안전하게 확인하고, 미리 만든 색인을 이용해 파일 이름과 경로를 빠르게 찾기 위한 로컬 파일 검색 도구입니다.
