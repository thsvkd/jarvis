"""공식 릴리스 받기: 리다이렉트를 GitHub 와 그 릴리스 저장소로만 따르는 opener 와 받은 파일의 크기·해시 확인."""
import urllib.error
import urllib.parse
import urllib.request

HOSTS = {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
         "github-releases.githubusercontent.com"}


class _GitHub(urllib.request.HTTPRedirectHandler):
    """릴리스 다운로드는 GitHub 와 그 릴리스 저장소로만 따라간다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urllib.parse.urlsplit(newurl).hostname
        if host not in HOSTS:
            raise urllib.error.URLError(f"릴리스 리다이렉트 호스트가 아닙니다: {host}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


open_url = urllib.request.build_opener(urllib.request.ProxyHandler({}), _GitHub).open
