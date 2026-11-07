"""작업 점유 답(occupancy): 소유 모듈의 답과, 호출자마다 다른 검사 순서·문구·상태 코드."""
import os
import tempfile
import unittest
from unittest import mock

from jarvis import modules_api, occupancy, orca_issue, orca_update, self_update, server  # noqa: F401 (server: 도는 프로세스의 import)

RUNNING = {"state": "running", "scope": "desktop"}
ALL = (occupancy.SELF_UPDATE, occupancy.ORCA_UPDATE, occupancy.ORCA_UPDATE_MARK, occupancy.ORCA_ISSUE, occupancy.MODULE_APPLY)


class AnswersTest(unittest.TestCase):
    def test_an_unregistered_work_is_not_held(self):
        with mock.patch.dict(occupancy._answers, clear=True):
            self.assertIsNone(occupancy.held(occupancy.ORCA_ISSUE))
            occupancy.register(occupancy.ORCA_ISSUE, lambda: "값")
            self.assertEqual(occupancy.held(occupancy.ORCA_ISSUE), "값")

    def test_the_server_process_has_every_owner_registered(self):
        self.assertEqual(set(occupancy._answers), set(ALL))

    def test_jobs_answer_only_while_running(self):
        for owner, work in ((orca_update, occupancy.ORCA_UPDATE), (orca_issue, occupancy.ORCA_ISSUE)):
            with mock.patch.object(owner, "job", None):
                self.assertFalse(occupancy.held(work))
            with mock.patch.object(owner, "job", {"state": "done"}):
                self.assertFalse(occupancy.held(work))
            with mock.patch.object(owner, "job", RUNNING):
                self.assertEqual(occupancy.held(work), RUNNING)

    def test_the_update_mark_answers_from_the_file(self):
        with tempfile.TemporaryDirectory() as d:
            mark = os.path.join(d, "m.json")
            with mock.patch.object(orca_update, "MARK", mark):
                self.assertFalse(occupancy.held(occupancy.ORCA_UPDATE_MARK))
                open(mark, "w").close()
                self.assertTrue(occupancy.held(occupancy.ORCA_UPDATE_MARK))

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
            (occupancy.ORCA_UPDATE, "Orca 업데이트(복구·세션 검증 포함)가 끝난 뒤에 적용하세요"),
            (occupancy.ORCA_UPDATE_MARK, "Orca 업데이트(복구·세션 검증 포함)가 끝난 뒤에 적용하세요"),
            (occupancy.ORCA_ISSUE, "페어링 코드 발급이 끝난 뒤에 적용하세요"),
        ])
        with self.answers():
            self.assertIsNone(modules_api._busy())

    def test_self_update_checks(self):
        self.walk(lambda: self_update.start("1.1.0"), [
            (occupancy.ORCA_UPDATE, (409, {"error": "Orca 업데이트가 끝난 뒤에 갱신하세요"})),
            (occupancy.ORCA_ISSUE, (409, {"error": "페어링 코드 발급이 끝난 뒤에 갱신하세요"})),
            (occupancy.MODULE_APPLY, (409, {"error": "모듈 적용이 끝난 뒤에 갱신하세요"})),
        ])

    def test_self_update_ignores_its_own_lock_and_the_update_mark(self):
        with self.answers(occupancy.SELF_UPDATE, occupancy.ORCA_UPDATE_MARK), self_update._lock:
            self.assertEqual(self_update.start("1.1.0"), (409, {"error": "이미 갱신하고 있습니다"}))

    def test_orca_issue_checks(self):
        with mock.patch.object(orca_issue, "job", None):
            self.walk(lambda: orca_issue.start("desktop", "100.1.2.3", 6768), [
                (occupancy.ORCA_UPDATE_MARK, (409, {"error": "이전 Orca 업데이트의 복구·세션 검증이 끝난 뒤에 발급하세요"})),
                (occupancy.ORCA_UPDATE, (409, {"error": "Orca 업데이트가 끝난 뒤에 발급하세요", "update": RUNNING})),
                (occupancy.MODULE_APPLY, (409, {"error": "모듈 적용이 끝난 뒤에 발급하세요"})),
                (occupancy.SELF_UPDATE, (409, {"error": "jarvis 갱신이 끝난 뒤에 발급하세요"})),
            ])

    def test_orca_update_checks(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(orca_update, "MARK", os.path.join(d, "m.json")), \
                mock.patch.object(orca_update, "job", None), mock.patch.object(orca_issue, "job", None), \
                mock.patch.object(orca_update, "_supported", return_value=(True, None)):
            self.walk(orca_update.start, [
                (occupancy.MODULE_APPLY, (409, {"error": "모듈 적용이 끝난 뒤에 업데이트하세요"})),
                (occupancy.SELF_UPDATE, (409, {"error": "jarvis 갱신이 끝난 뒤에 업데이트하세요"})),
            ])


if __name__ == "__main__":
    unittest.main()
