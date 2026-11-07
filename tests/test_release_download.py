"""리다이렉트 허용 호스트(check_file 은 test_orca_update 의 test_hash 가 본다)."""
import unittest
import urllib.error
import urllib.request

from jarvis import release_download


class RedirectTest(unittest.TestCase):
    def follow(self, url):
        req = urllib.request.Request("https://github.com/a/b/releases/download/v1/x")
        return release_download._GitHub().redirect_request(req, None, 302, "Found", {}, url)

    def test_only_release_hosts_are_followed(self):
        for host in release_download.HOSTS:
            self.assertEqual(self.follow(f"https://{host}/x").host, host)
        for url in ("https://example.com/x", "https://github.com.example.com/x", "https://example.com/github.com"):
            with self.assertRaisesRegex(urllib.error.URLError, "리다이렉트 호스트가 아닙니다"):
                self.follow(url)


if __name__ == "__main__":
    unittest.main()
