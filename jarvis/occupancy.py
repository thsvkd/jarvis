"""기기를 바꾸는 작업이 지금 자리를 차지했는지 답한다. 작업을 가진 모듈끼리 서로 import 하지 않도록 가운데에 둔다.

소유 모듈은 import 될 때 자기 답을 등록한다. 등록되지 않은 작업의 답은 None 이다: `python -m jarvis` 는 server 를 거쳐
소유 모듈을 모두 import 하므로 도는 프로세스에서는 생기지 않는다. 검사 순서·문구·상태 코드는 호출자마다 달라 호출자에 둔다.
"""

SELF_UPDATE = "self_update"  # 자체 갱신이 잠금을 쥐고 있다
MODULE_APPLY = "module_apply"  # 모듈 적용(이 기기 또는 SSH 기기)이 도는 중이다

_answers = {}


def register(work, answer):
    """answer: 인자 없는 함수. 점유 중이면 참인 값, 아니면 거짓인 값을 돌려준다."""
    _answers[work] = answer


def held(work):
    answer = _answers.get(work)
    return answer() if answer else None
