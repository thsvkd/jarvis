"""ai-setup 에서 옮긴 pytest 형식 테스트를 표준 라이브러리 unittest 로 돌린다.

옮긴 테스트의 본문과 단언을 한 글자도 바꾸지 않으려고 둔다(원본과 diff 로 대조할 수 있게).
테스트 모듈은 `from pytest_compat import pytest` 로 바꾸고, 끝에 `load_tests = collect(globals())` 를 둔다.
지원하는 것: 픽스처 tmp_path·monkeypatch·capsys 와 모듈의 @pytest.fixture(autouse 포함), mark.parametrize·mark.skipif(모듈 pytestmark 포함),
raises(match=)·fail·skip. 그 밖의 pytest 기능은 일부러 넣지 않았다(쓰면 이름 오류로 바로 드러난다).
"""
import contextlib
import inspect
import io
import itertools
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_UNSET = object()


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=_UNSET, raising=True):
        if value is _UNSET:  # setattr("pkg.mod.attr", value) 형식
            value, (module, name) = name, target.rsplit(".", 1)
            target = __import__(module, fromlist=[name])
        old = getattr(target, name, _UNSET)
        if old is _UNSET and raising:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        if inspect.isclass(target):  # 클래스에서 꺼내면 staticmethod 가 벗겨지므로 원래 값을 그대로 둔다
            old = target.__dict__.get(name, _UNSET)
        self._undo.append(lambda: delattr(target, name) if old is _UNSET else setattr(target, name, old))
        setattr(target, name, value)

    def delattr(self, target, name, raising=True):
        old = getattr(target, name, _UNSET)
        if old is _UNSET:
            if raising:
                raise AttributeError(name)
            return
        self._undo.append(lambda: setattr(target, name, old))
        delattr(target, name)

    def setitem(self, mapping, key, value):
        old = mapping.get(key, _UNSET)
        self._undo.append(lambda: mapping.pop(key, None) if old is _UNSET else mapping.__setitem__(key, old))
        mapping[key] = value

    def delitem(self, mapping, key, raising=True):
        if key not in mapping:
            if raising:
                raise KeyError(key)
            return
        old = mapping[key]
        self._undo.append(lambda: mapping.__setitem__(key, old))
        del mapping[key]

    def setenv(self, name, value, prepend=None):
        value = str(value)
        if prepend and name in os.environ:
            value = value + prepend + os.environ[name]
        self.setitem(os.environ, name, value)

    def delenv(self, name, raising=True):
        self.delitem(os.environ, name, raising)

    def syspath_prepend(self, path):
        saved = list(sys.path)
        self._undo.append(lambda: sys.path.__setitem__(slice(None), saved))
        sys.path.insert(0, str(path))

    def chdir(self, path):
        saved = os.getcwd()
        self._undo.append(lambda: os.chdir(saved))
        os.chdir(path)

    def undo(self):
        while self._undo:
            self._undo.pop()()


class _Captured:
    def __init__(self, out, err):
        self.out, self.err = out, err

    def __iter__(self):
        return iter((self.out, self.err))


class CaptureFixture:
    def __init__(self, stack):
        self._out, self._err = io.StringIO(), io.StringIO()
        stack.enter_context(contextlib.redirect_stdout(self._out))
        stack.enter_context(contextlib.redirect_stderr(self._err))

    def readouterr(self):
        result = _Captured(self._out.getvalue(), self._err.getvalue())
        for buf in (self._out, self._err):
            buf.seek(0)
            buf.truncate()
        return result


class Failed(BaseException):
    """pytest.fail. pytest 처럼 BaseException 이라 제품 코드의 `except Exception` 이 삼키지 못한다."""


class Skipped(BaseException):
    """pytest.skip. 같은 이유로 BaseException 이고, 케이스 밖에서 unittest.SkipTest 로 바꾼다."""


class _ExcInfo:
    value = None
    type = None


class _Mark:
    @staticmethod
    def parametrize(names, values, ids=None, indirect=False):
        if indirect:
            raise TypeError("pytest_compat: parametrize(indirect=) 는 지원하지 않는다")
        names = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
        rows = [tuple(v) if len(names) > 1 else (v,) for v in values]

        def mark(func):
            func.__dict__.setdefault("_params", []).insert(0, (names, rows))
            return func
        return mark

    @staticmethod
    def skipif(condition, reason=""):
        def mark(func):
            func.__dict__.setdefault("_skips", []).append((condition, reason))
            return func
        mark._skips = [(condition, reason)]  # 모듈의 pytestmark 로 쓸 때
        return mark


class pytest:
    mark = _Mark

    @staticmethod
    @contextlib.contextmanager
    def raises(expected, match=None):
        info = _ExcInfo()
        try:
            yield info
        except expected as e:
            info.value, info.type = e, type(e)
            if match is not None and not re.search(match, str(e)):
                raise AssertionError(f"pattern {match!r} not found in {str(e)!r}") from e
        else:
            raise AssertionError(f"DID NOT RAISE {expected}")

    @staticmethod
    def fail(msg=""):
        raise Failed(msg)

    @staticmethod
    def skip(msg=""):
        raise Skipped(msg)

    @staticmethod
    def fixture(func=None, autouse=False):
        def mark(f):
            f._fixture, f._autouse = True, autouse
            return f
        return mark(func) if func else mark


def collect(namespace):
    """모듈의 test_* 함수를 unittest 케이스로 바꾼다(load_tests 프로토콜)."""
    fixtures = {n: f for n, f in namespace.items() if getattr(f, "_fixture", False)}
    autouse = [n for n, f in fixtures.items() if f._autouse]
    mark = namespace.get("pytestmark")
    if mark is not None and not hasattr(mark, "_skips"):  # 리스트 형태 등은 조용히 무시하지 않는다
        raise TypeError("pytest_compat: pytestmark 는 pytest.mark.skipif(...) 하나만 지원한다")
    module_skips = getattr(mark, "_skips", [])
    classes = [n for n, v in namespace.items() if n.startswith("Test") and inspect.isclass(v)
               and not issubclass(v, unittest.TestCase)]
    if classes:  # 옮긴 테스트가 조용히 사라지지 않게
        raise TypeError(f"pytest_compat: Test* 클래스는 지원하지 않는다: {classes}")

    def run(func, params):
        def method(self):
            with contextlib.ExitStack() as stack:
                values = dict(params)
                patch = MonkeyPatch()
                stack.callback(patch.undo)

                def get(name):
                    if name in values:
                        return values[name]
                    if name == "tmp_path":
                        path = tempfile.mkdtemp(prefix="jarvis-test-")
                        stack.callback(shutil.rmtree, path, ignore_errors=True)
                        values[name] = Path(os.path.realpath(path))
                    elif name == "monkeypatch":
                        values[name] = patch
                    elif name == "capsys":
                        values[name] = CaptureFixture(stack)
                    elif name in fixtures:
                        f = fixtures[name]
                        result = f(**{a: get(a) for a in inspect.signature(f).parameters})
                        if inspect.isgenerator(result):
                            gen, result = result, next(result)
                            stack.callback(next, gen, None)
                        values[name] = result
                    else:
                        raise NameError(f"unknown fixture {name!r}")
                    return values[name]

                for name in autouse:
                    get(name)
                try:
                    func(**{a: get(a) for a in inspect.signature(func).parameters})
                except Failed as e:
                    raise AssertionError(str(e)) from None
                except Skipped as e:
                    raise unittest.SkipTest(str(e)) from None
        method.__doc__ = func.__doc__
        for condition, reason in module_skips + getattr(func, "_skips", []):
            if condition:
                method = unittest.skip(reason)(method)
        return method

    methods = {}
    for name, func in list(namespace.items()):
        if not (name.startswith("test_") and inspect.isfunction(func)):
            continue
        grids = getattr(func, "_params", [])
        combos = itertools.product(*[[list(zip(names, row)) for row in rows] for names, rows in grids])
        for i, combo in enumerate(combos):
            params = [kv for part in combo for kv in part]
            methods[name if not grids else f"{name}[{i}]"] = run(func, params)
    # 케이스 ID 가 원래 모듈을 가리키게 한다: python -m unittest tests.<모듈>.Cases.<테스트>
    case = type("Cases", (unittest.TestCase,), {**methods, "__module__": namespace["__name__"]})
    namespace["Cases"] = case

    def load_tests(loader, standard_tests, pattern):
        return loader.loadTestsFromTestCase(case)
    return load_tests


if __name__ == "__main__":  # 자기 점검: python3 tests/pytest_compat.py
    probe = {"__name__": "probe"}
    seen = []

    @pytest.fixture(autouse=True)
    def env(monkeypatch):
        monkeypatch.setenv("PROBE_X", "1")

    @pytest.mark.parametrize("a,b", [(1, 2), (3, 4)])
    @pytest.mark.parametrize("c", ["x"])
    def test_grid(tmp_path, a, b, c, capsys):
        assert tmp_path.is_dir() and os.environ["PROBE_X"] == "1"
        print("hi")
        assert capsys.readouterr().out == "hi\n"
        seen.append((a, b, c))
        with pytest.raises(ValueError, match="bo+m") as info:
            raise ValueError("boom")
        assert isinstance(info.value, ValueError)

    def test_swallowed_fail(tmp_path):
        try:
            pytest.fail("must fail")
        except Exception:  # 제품 코드의 넓은 except 가 삼키려 해도
            pass

    probe.update(env=env, test_grid=test_grid, test_swallowed_fail=test_swallowed_fail)
    suite = collect(probe)(unittest.defaultTestLoader, None, None)
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    assert len(result.failures) == 1 and "test_swallowed_fail" in str(result.failures[0][0]), result.failures
    assert not result.errors and sorted(seen) == [(1, 2, "x"), (3, 4, "x")], (result, seen)
    assert "PROBE_X" not in os.environ
    print("pytest_compat ok")
