# GIT

## 원칙

- 논리적 변경 하나에 커밋 하나.
- 테스트와 구현은 같은 커밋에 넣는다.
- 커밋마다 테스트가 통과하는 상태를 유지한다.
- 히스토리를 바꾸는 경우 되돌릴 지점을 남긴다.
- 특별한 요청이 없는 경우 항상 워크트리 브랜치를 따서 작업한다.

## 커밋 메시지

`<type>(<scope>): <무엇을 왜 바꿨는지 한 줄>`

- 커밋 메시지는 항상 영어.
- Conventional Commits 준수.
- 커밋은 되도록 컴팩트하게 작성 -> 제목만으로 충분한 경우 본문 생략 가능.
- 필요시 본문에 상세히 무엇을 바꿨는지, 왜 필요한지, 어떤 근거로 바꿨는지를 설명.
- 이슈·PR 번호가 있으면 본문 끝에 붙인다.
- 사용자 이름·이메일은 `git config user.name` / `git config user.email` 출력 사용.
- `Co-Authored-By`: / `Made-with`: 금지. 트레일러는 아래 형식만:

  ```plain
  Signed-off-by: {user.name} <{user.email}>
  Assisted-by: {Model.name} ({reasoning level})
  ```
