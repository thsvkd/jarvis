"""호환 이름표. 구현은 claude_accounts 에 있다(read 는 저장소 캐시 어댑터 claude_accounts._store_read)."""
from .claude_accounts import alias

alias(__name__, {"read": "_store_read"})
