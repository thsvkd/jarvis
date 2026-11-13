"""SSH 기기의 사용량·로그인·계정 조작 통로(herdr 의 remote-client-bridge 와 같은 방식).

core 는 `ssh <별칭> <python> -m jarvis.ipc` 를 띄워 HTTP 요청 하나를 표준 입력으로 보낸다. 이 bridge 는 그 요청을 같은
기기·같은 사용자의 게이트웨이 IPC 로 넘기고 응답을 표준 출력으로 돌려준다. 인증은 SSH 키와 IPC 의 권한이다.
- POSIX: <ROOT>/ipc/gateway.sock (디렉터리 0700, 소켓 0600)
- Windows: \\\\.\\pipe\\<ROOT>\\gateway.sock (DACL 은 SYSTEM 과 게이트웨이 사용자뿐, 원격 클라이언트 거절).
  Windows 의 CPython 은 AF_UNIX 가 없어 ctypes 로 named pipe 를 쓴다. 이름 공간이 기기 공용이므로 서버는 첫 인스턴스만
  만들고(남이 먼저 만들었으면 실패), bridge 는 파이프 소유자가 자기 SID 인지 확인한다.
루프백 TCP(9300)는 이 통로가 아니다: 그쪽은 지금처럼 core 전용 경로가 403 이다.
"""

import functools
import os
import sys
import threading

from . import launch

MAX_REQUEST = 64 << 10  # 사용량·로그인·계정 요청은 본문이 없다
WIN = sys.platform == "win32"


def address():
    if WIN:
        return "\\\\.\\pipe\\" + os.path.join(launch.ROOT, "gateway.sock")
    return os.path.join(launch.ROOT, "ipc", "gateway.sock")


def serve(handler, server):
    """handler(BaseHTTPRequestHandler, ipc=True)로 IPC 연결을 받는 데몬 스레드를 띄운다. 실패해도 게이트웨이는 계속 돈다.
    POSIX 는 서버(테스트가 닫는다)를, 그 밖에는 None 을 준다."""
    s = None
    try:
        if WIN:
            h = _Pipe.create(first=True)
            threading.Thread(target=_pipe_loop, args=(handler, server, h), name="jarvis-ipc", daemon=True).start()
            _write_launcher()
        else:
            import socketserver
            path = address()
            os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
            os.chmod(os.path.dirname(path), 0o700)  # 이미 있던 디렉터리도 좁힌다
            try:
                os.unlink(path)  # 지난 기동이 남긴 소켓(9300 을 먼저 잡았으므로 다른 게이트웨이의 것이 아니다)
            except FileNotFoundError:
                pass

            class Unix(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True

                def finish_request(self, request, client_address):
                    handler(request, ("ipc", 0), server)

            s = Unix(path, handler)
            os.chmod(path, 0o600)
            threading.Thread(target=s.serve_forever, name="jarvis-ipc", daemon=True).start()
        print(f"[jarvis] SSH bridge IPC: {address()}", flush=True)
    except Exception as e:
        print(f"[jarvis] SSH bridge IPC 를 열지 못했다(SSH 기기의 사용량·로그인 조작이 안 된다): {type(e).__name__}: {e}", flush=True)
    return s


def _write_launcher():
    """Windows: core 가 부를 %LOCALAPPDATA%\\jarvis\\bridge.cmd. 원격 셸이 cmd 든 PowerShell 이든 `cmd /c` 로 같게 부른다.
    pythonw 에는 표준 입출력이 없으므로 같은 폴더의 python.exe 를 쓴다."""
    py = os.path.join(os.path.dirname(sys.executable), "python.exe")
    code = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.makedirs(launch.ROOT, exist_ok=True)
    tmp = os.path.join(launch.ROOT, "bridge.cmd.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(f'@cd /d "{code}" && "{py if os.path.isfile(py) else sys.executable}" -m jarvis.ipc\n')
    os.replace(tmp, os.path.join(launch.ROOT, "bridge.cmd"))


class _Conn:
    """BaseHTTPRequestHandler 가 소켓처럼 쓰는 파일 기술자(Windows 파이프 핸들)."""

    def __init__(self, fd):
        self.fd = fd

    def makefile(self, mode, buffering=-1):
        return open(self.fd, mode, buffering=buffering if buffering > 0 else -1, closefd=False)

    def sendall(self, b):
        view = memoryview(b)
        while view:
            view = view[os.write(self.fd, view):]

    def settimeout(self, t):  # 파이프는 시간 상한이 없다: 같은 사용자만 열 수 있어 느린 클라이언트를 막지 않는다
        pass


def _pipe_loop(handler, server, h):
    """인스턴스 하나가 늘 남아 있게 돈다: 이름이 잠깐이라도 비면 다른 사용자가 같은 이름의 파이프를 먼저 만들 수 있다."""
    import time
    slots = threading.BoundedSemaphore(8)
    while True:
        try:
            _Pipe.connect(h)
        except OSError:  # 연결 직후 끊긴 클라이언트(core 의 시간 초과 등): 같은 인스턴스를 비우고 다시 기다린다
            _Pipe.disconnect(h)
            continue
        while True:  # 다음 연결을 받을 인스턴스를 만든 뒤에야 지금 것을 넘긴다
            try:
                nxt = _Pipe.create(first=False)
                break
            except OSError as e:
                print(f"[jarvis] SSH bridge IPC 인스턴스를 만들지 못했다, 1초 뒤 다시: {e}", flush=True)
                time.sleep(1)

        def one(h=h):
            try:
                import msvcrt
                fd = msvcrt.open_osfhandle(h, 0)
            except Exception as e:
                print(f"[jarvis] SSH bridge IPC 연결 처리 실패: {type(e).__name__}", flush=True)
                _Pipe.close(h)
                slots.release()
                return
            try:
                handler(_Conn(fd), ("ipc", 0), server)
            except Exception as e:
                print(f"[jarvis] SSH bridge IPC 요청 처리 실패: {type(e).__name__}", flush=True)
            finally:
                _Pipe.flush_disconnect(h)
                os.close(fd)
                slots.release()

        if slots.acquire(blocking=False):
            threading.Thread(target=one, daemon=True).start()
        else:
            _Pipe.close(h)
        h = nxt


class _Pipe:
    """Windows named pipe(ctypes). 서버는 create·connect, bridge 는 open_client."""

    @staticmethod
    @functools.cache  # DLL 과 함수 서명은 한 번만 맞춘다
    def _k():
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        k.CreateNamedPipeW.restype = wintypes.HANDLE
        k.CreateNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                       wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        k.CreateFileW.restype = wintypes.HANDLE
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.HANDLE]
        for f in ("ConnectNamedPipe", "DisconnectNamedPipe", "FlushFileBuffers", "CloseHandle"):
            getattr(k, f).argtypes = [wintypes.HANDLE] + ([ctypes.c_void_p] if f == "ConnectNamedPipe" else [])
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.LocalFree.argtypes = [ctypes.c_void_p]
        a.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        a.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.DWORD)]
        a.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
        a.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                      ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        a.EqualSid.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        return ctypes, wintypes, k, a

    @classmethod
    def _sid(cls):
        """이 프로세스 사용자의 SID 문자열."""
        ctypes, wintypes, k, a = cls._k()
        tok = wintypes.HANDLE()
        if not a.OpenProcessToken(k.GetCurrentProcess(), 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            buf, n = ctypes.create_string_buffer(256), wintypes.DWORD()
            if not a.GetTokenInformation(tok, 1, buf, 256, ctypes.byref(n)):  # TokenUser
                raise ctypes.WinError(ctypes.get_last_error())
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]  # TOKEN_USER.User.Sid
            s = wintypes.LPWSTR()
            if not a.ConvertSidToStringSidW(psid, ctypes.byref(s)):
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                return s.value
            finally:
                k.LocalFree(s)
        finally:
            k.CloseHandle(tok)

    @classmethod
    def create(cls, first):
        ctypes, wintypes, k, a = cls._k()
        sid = cls._sid()
        sd = ctypes.c_void_p()
        # 소유자는 게이트웨이 사용자, DACL 은 상속 없이 SYSTEM 과 그 사용자만(herdr 의 파이프와 같다)
        if not a.ConvertStringSecurityDescriptorToSecurityDescriptorW(f"O:{sid}D:P(A;;GA;;;SY)(A;;GA;;;{sid})", 1,
                                                                      ctypes.byref(sd), None):
            raise ctypes.WinError(ctypes.get_last_error())

        class SA(ctypes.Structure):
            _fields_ = [("nLength", wintypes.DWORD), ("lpSecurityDescriptor", ctypes.c_void_p), ("bInheritHandle", wintypes.BOOL)]

        sa = SA(ctypes.sizeof(SA), sd, False)
        try:
            # PIPE_ACCESS_DUPLEX | (FILE_FLAG_FIRST_PIPE_INSTANCE), PIPE_TYPE_BYTE|READMODE_BYTE|WAIT|REJECT_REMOTE_CLIENTS
            h = k.CreateNamedPipeW(address(), 0x3 | (0x80000 if first else 0), 0x8, 255, 65536, 65536, 0, ctypes.byref(sa))
        finally:
            k.LocalFree(sd)
        if h in (None, wintypes.HANDLE(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        return h

    @classmethod
    def connect(cls, h):
        ctypes, _, k, _ = cls._k()
        if not k.ConnectNamedPipe(h, None) and ctypes.get_last_error() != 535:  # ERROR_PIPE_CONNECTED
            raise ctypes.WinError(ctypes.get_last_error())

    @classmethod
    def flush_disconnect(cls, h):
        _, _, k, _ = cls._k()
        k.FlushFileBuffers(h)
        k.DisconnectNamedPipe(h)

    @classmethod
    def disconnect(cls, h):
        cls._k()[2].DisconnectNamedPipe(h)

    @classmethod
    def close(cls, h):
        cls._k()[2].CloseHandle(h)

    @classmethod
    def open_client(cls):
        """bridge 쪽: 파이프를 열고 소유자가 이 사용자인지 확인한 파일 기술자."""
        import msvcrt
        ctypes, wintypes, k, a = cls._k()
        # GENERIC_READ|WRITE, OPEN_EXISTING, SECURITY_SQOS_PRESENT|SECURITY_IDENTIFICATION: 소유자를 확인하기 전의
        # 서버가 이 사용자로 가장(impersonate)하지 못하게 한다
        h = k.CreateFileW(address(), 0xC0000000, 0, None, 3, 0x00110000, None)
        if h in (None, wintypes.HANDLE(-1).value):
            raise ctypes.WinError(ctypes.get_last_error())
        owner, sd = ctypes.c_void_p(), ctypes.c_void_p()
        if a.GetSecurityInfo(h, 6, 0x1, ctypes.byref(owner), None, None, None, ctypes.byref(sd)):  # SE_KERNEL_OBJECT, OWNER
            k.CloseHandle(h)
            raise OSError("파이프 소유자를 읽지 못했다")
        try:
            s = wintypes.LPWSTR()
            ok = a.ConvertSidToStringSidW(owner, ctypes.byref(s)) and s.value == cls._sid()
            if s:
                k.LocalFree(s)
        finally:
            k.LocalFree(sd)
        if not ok:  # 다른 사용자가 먼저 만든 같은 이름의 파이프에 로그인 코드를 넘기지 않는다
            k.CloseHandle(h)
            raise OSError("게이트웨이 사용자가 만든 파이프가 아니다")
        return msvcrt.open_osfhandle(h, 0)


def bridge():
    """표준 입력의 요청 하나(입력이 닫힐 때까지)를 IPC 로 보내고 응답을 표준 출력으로. 한 연결에서 읽기·쓰기를 번갈아 하지 않는다
    (Windows 의 동기 파이프 핸들은 한 스레드의 ReadFile 이 다른 스레드의 WriteFile 을 막는다)."""
    if WIN:
        import msvcrt
        for fd in (0, 1):
            msvcrt.setmode(fd, os.O_BINARY)
    req = b""
    while chunk := os.read(0, 65536):
        req += chunk
        if len(req) > MAX_REQUEST:
            print("요청이 너무 큽니다", file=sys.stderr)
            return 2
    try:
        if WIN:
            fd = _Pipe.open_client()
            read, close = (lambda: os.read(fd, 65536)), (lambda: os.close(fd))
            _Conn(fd).sendall(req)
        else:
            import socket
            s = socket.socket(socket.AF_UNIX)
            s.connect(address())
            s.sendall(req)
            s.shutdown(socket.SHUT_WR)
            read, close = (lambda: s.recv(65536)), s.close
    except OSError as e:
        print(f"게이트웨이 IPC 에 닿지 못했습니다: {e.strerror or e}", file=sys.stderr)
        return 92
    try:
        while True:
            try:
                b = read()
            except OSError:  # Windows: 서버가 응답을 다 쓰고 끊으면 EINVAL·EPIPE 다(잘린 응답은 core 가 Content-Length 로 안다)
                if not WIN:
                    raise
                break
            if not b:
                break
            os.write(1, b)
    finally:
        close()
    return 0


if __name__ == "__main__":
    sys.exit(bridge())
