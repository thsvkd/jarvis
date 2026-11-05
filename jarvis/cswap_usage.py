"""호환 이름표. 구현은 claude_accounts 에 있다(read 는 hswap CLI 어댑터 claude_accounts.live)."""
from .claude_accounts import alias

alias(__name__, {"read": "live"})
