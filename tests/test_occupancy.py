"""작업 점유 답(occupancy): 소유 모듈의 답과, 호출자마다 다른 검사 순서·문구·상태 코드."""
import threading
import unittest
from unittest import mock

from jarvis import modules_api, occupancy, self_update, server  # noqa: F401 (server: 도는 프로세스의 import)

RUNNING = {"state": "running"}
ALL = (occupancy.SELF_UPDATE, occupancy.MODULE_APPLY)


class AnswersTest(unittest.TestCase):
    def test_the_update_answer_does_not_wait_for_the_update_lock(self):
        """modules_api._busy 가 _job_lock 을 쥔 채 묻고 start 는 _lock 을 쥔 채 _job_lock 을 잡는다: 답이 _lock 을 잡으면 교착이다"""
        holding, release, answered = threading.Event(), threading.Event(), threading.Event()

        def hold():
            with self_update._lock:
                holding.set()
                release.wait(5)

        def ask():
            occupancy.held(occupancy.SELF_UPDATE)
            answered.set()

        threading.Thread(target=hold, daemon=True).start()
        self.addCleanup(release.set)
        self.assertTrue(holding.wait(5))
        threading.Thread(target=ask, daemon=True).start()
        self.assertTrue(answered.wait(2))

    def test_an_unregistered_work_is_not_held(self):
        with mock.patch.dict(occupancy._answers, clear=True):
            self.assertIsNone(occupancy.held(occupancy.MODULE_APPLY))
            occupancy.register(occupancy.MODULE_APPLY, lambda: "값")
            self.assertEqual(occupancy.held(occupancy.MODULE_APPLY), "값")

    def test_the_server_process_has_every_owner_registered(self):
        self.assertEqual(set(occupancy._answers), set(ALL))

    def test_the_self_update_lock_answers(self):
        self.assertFalse(occupancy.held(occupancy.SELF_UPDATE))
        with self_update._lock:
            self.assertTrue(occupancy.held(occupancy.SELF_UPDATE))

    def test_module_apply_answers_for_any_device(self):
        self.assertFalse(occupancy.held(occupancy.MODULE_APPLY))
        with mock.patch.dict(modules_api.ssh_jobs, kg={"state": "running"}):
            self.assertTrue(occupancy.held(occupancy.MODULE_APPLY))
        with mock.patch.dict(modules_api.job, state="running"):
            self.assertTrue(occupancy.held(occupancy.MODULE_APPLY))


class CallerOrderTest(unittest.TestCase):
    """모두 점유한 뒤 하나씩 비우면, 호출자마다 정해진 순서로 자기 문구를 낸다."""

    def answers(self, *held):
        return mock.patch.dict(occupancy._answers, {w: (lambda v=(RUNNING if w in held else None): v) for w in ALL})

    def walk(self, call, order):
        for i, (work, want) in enumerate(order):
            with self.answers(*[w for w, _ in order[i:]]):
                self.assertEqual(call(), want, work)

    def test_module_apply_checks(self):
        self.walk(modules_api._busy, [
            (occupancy.SELF_UPDATE, "jarvis 갱신이 끝난 뒤에 적용하세요"),
        ])
        with self.answers():
            self.assertIsNone(modules_api._busy())

    def test_self_update_checks(self):
        self.walk(lambda: self_update.start("1.1.0"), [
            (occupancy.MODULE_APPLY, (409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"})),
        ])

    def test_self_update_ignores_its_own_lock(self):
        with self.answers(occupancy.SELF_UPDATE), self_update._lock:
            self.assertEqual(self_update.start("1.1.0"), (409, {"error": "이미 갱신하고 있습니다"}))


if __name__ == "__main__":
    unittest.main()
