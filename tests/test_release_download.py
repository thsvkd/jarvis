"""리다이렉트 허용 호스트와 그 규칙이 실제 opener 에 걸려 있는지."""
import importlib.util
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from jarvis import release_download


class RedirectTest(unittest.TestCase):
    def follow(self, url):
        req = urllib.request.Request("https://github.com/a/b/releases/download/v1/x")
        return release_download._GitHub().redirect_request(req, None, 302, "Found", {}, url)

    def test_only_release_hosts_are_followed(self):
        for host in release_download.HOSTS:
            self.assertEqual(self.follow(f"https://{host}/x").host, host)
        for url in ("https://example.com/x", "https://github.com.example.com/x", "https://example.com/github.com", "https://evilgithub.com/x"):
            with self.assertRaisesRegex(urllib.error.URLError, "리다이렉트 호스트가 아닙니다"):
                self.follow(url)

    def test_the_allowed_hosts_are_exactly_these(self):
        """보안 범위라 목록을 자기 자신과 비교하지 않고 글자로 고정한다"""
        self.assertEqual(release_download.HOSTS, {"github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
                                                  "github-releases.githubusercontent.com"})


class OpenerTest(unittest.TestCase):
    def test_the_real_opener_refuses_a_redirect_to_another_host(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(302)
                self.send_header("Location", "http://evil.example.test/x")
                self.end_headers()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with self.assertRaisesRegex(urllib.error.URLError, "리다이렉트 호스트가 아닙니다: evil.example.test"):
            release_download.open_url(f"http://127.0.0.1:{server.server_port}/x", timeout=5)

    def test_the_opener_ignores_proxy_settings(self):
        """opener 는 import 할 때 만들어지므로 프록시 환경 변수를 둔 채 사본을 읽는다"""
        spec = importlib.util.spec_from_file_location("release_download_copy", release_download.__file__)
        copy = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9"}):
            spec.loader.exec_module(copy)
        # 프록시가 빈 ProxyHandler 는 처리할 프로토콜이 없어 opener 의 목록에 들어가지 않는다
        proxies = [h.proxies for h in copy.open_url.__self__.handlers if isinstance(h, urllib.request.ProxyHandler)]
        self.assertEqual(proxies, [])


if __name__ == "__main__":
    unittest.main()
