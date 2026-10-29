# Korean Document Style Harness

한국어 보고서·논문·발표자료에 반복되는 작성 피드백을 적용하고, 새 피드백을 필요한 기준에만 반영하는 두 스킬이다.

- `korean-document-style`: 보고서 또는 발표자료 기준만 선택해 읽는다.
- `evolve`: 사용자가 개선을 요청할 때 대상 스킬의 기록을 찾아 기준과 변경 이력을 갱신한다.

원본 파일 편집·렌더링은 실행 환경의 문서·PPT 도구가 담당한다. 이 패키지는 생성기, 모델, OMX 설정을 설치하지 않는다. 실행 모델은 호출한 에이전트의 현재 설정을 따른다.

## 설치

저장소 루트에서 Node.js 18 이상으로 실행한다.

```powershell
node bin/aaalab.js install korean-document-style-harness --agent-home "$env:USERPROFILE/.agents"
```

Codex 사용자 스킬 디렉터리 `~/.agents/skills/`에 두 스킬이 설치된다. 재시작 후 확인한다. GJC만 별도로 설치하려면 다음과 같이 지정한다.

```powershell
node bin/aaalab.js install korean-document-style-harness --agent-home "$env:USERPROFILE/.gjc/agent"
```

설치기는 같은 이름의 기존 스킬 폴더를 교체한다. 개인 피드백과 수정 지침이 이미 있다면 먼저 별도 백업하고, 새 기준과 병합한다. 개인 기록을 공개 저장소에 커밋하지 않는다.

Codex와 GJC가 같은 수정본을 사용하려면 위 Codex 설치를 기준으로, GJC에 없는 경로만 junction으로 연결한다. 기존 폴더가 있으면 덮어쓰지 말고 내용을 비교·병합한 뒤 연결한다.

```powershell
$sharedSkills = Join-Path $env:USERPROFILE '.agents/skills'
$gjcSkills = Join-Path $env:USERPROFILE '.gjc/agent/skills'
New-Item -ItemType Directory -Path $gjcSkills -Force | Out-Null
foreach ($name in @('korean-document-style', 'evolve')) {
    $link = Join-Path $gjcSkills $name
    if (Test-Path -LiteralPath $link) { throw "Existing skill: $name; compare before linking." }
    New-Item -ItemType Junction -Path $link -Target (Join-Path $sharedSkills $name)
}
```

junction은 Windows용이다. 다른 환경에서는 같은 원본을 가리키는 심볼릭 링크 또는 명시적 동기화를 사용한다. GJC의 스킬 디렉터리를 변경했다면 실제 설정 경로를 사용한다.

## 호출

Codex:

```text
$korean-document-style 첨부한 보고서의 문장과 구성을 수정해줘.
$korean-document-style 발표자료를 검토하고 수정 계획만 정리해줘.
$evolve korean-document-style 개선: 제목은 명사구로 쓰고 의미 없는 색상은 빼줘. 이번 피드백을 다음 작성에도 적용해줘.
```

GJC:

```text
/skill:korean-document-style 발표자료를 검토하고 수정 계획만 정리해줘.
/skill:evolve korean-document-style 개선: 보고서의 불필요한 작업 설명을 줄여줘.
```

스킬 이름과 피드백을 말하면 기록 위치는 스킬이 찾는다. 사용자가 `feedback.md` 경로를 기억할 필요는 없다. “검토·계획만” 요청했다면 원본 문서 수정까지 확대하지 않는다.

## 읽는 범위와 기록

일반 작성 때는 짧은 SKILL 지침과 해당 결과물의 기준만 읽고, 유지보수 기록은 읽지 않는다. 두 결과물이 함께 요청된 경우에만 두 기준을 읽는다. 스킬 목록의 이름·설명은 런타임의 발견 과정에 포함될 수 있으므로 토큰 사용이 완전히 없어지는 구조는 아니다.

- [보고서·논문 기준](skills/korean-document-style/references/reports.md)
- [발표자료 기준](skills/korean-document-style/references/slides.md)
- [작성 기준 피드백 기록](skills/korean-document-style/maintenance/feedback.md)
- [evolve 자체 변경 기록](skills/evolve/maintenance/feedback.md)
- [합성 평가 사례](EVALUATION.md)

공개 피드백 기록에는 일반화한 기준만 포함했다. 실제 문서, 세션, 개인 경로, 연구 수치와 원문 피드백은 포함하지 않는다. 설치 후 생기는 개인 기록은 로컬에서 관리한다.

## 검증

```powershell
python -X utf8 harnesses/korean-document-style-harness/scripts/validate_harness.py
node bin/aaalab.js validate korean-document-style-harness
```

검증기는 frontmatter, 상대 링크, 필수 파일 및 공개 패키지의 개인정보 패턴을 확인한다. 합성 사례의 기대 동작을 실제 호출 결과와 비교하는 작업은 별도다. 구조 검사만으로 문장 품질이나 피드백 감소 효과를 입증하지 않는다.

## 라이선스

Apache-2.0. [라이선스 검토](LICENSE_AUDIT.md)를 참고한다.
