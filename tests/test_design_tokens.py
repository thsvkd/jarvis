"""디자인 토큰 규칙(static/app/tokens.css 머리 참고): 컴포넌트 CSS 는 색·글자 크기·굵기·자간·모서리·간격·z-index 를
숫자로 쓰지 않고 토큰만 쓴다. 참조한 var(--x) 는 어딘가에서 정의돼야 한다(오타는 조용히 기본값이 된다)."""
import glob
import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")
APP = os.path.join(STATIC, "app")
TOKENS = os.path.join(APP, "tokens.css")
SETUP = os.path.join(STATIC, "setup")
COMPONENTS = sorted(p for p in glob.glob(os.path.join(APP, "*.css")) if p != TOKENS) + [os.path.join(SETUP, "setup.css")]
COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(")
SPACING = re.compile(r"^(scroll-)?(padding|margin)(-[a-z-]+)?$|^(grid-)?(row-|column-)?gap$")
# 고정 길이(px·rem·pt …). 2px 미만 px 은 머리카락 보정·아이콘 내부 기하라 허용한다. em·%·vw 같은 상대 길이는 검사하지 않는다.
LENGTH = re.compile(r"(?<![\w.])(\d*\.?\d+)(px|rem|pt|pc|cm|mm|in|q)\b", re.I)
DECL = re.compile(r"(?<![\w-])([a-z-]+)\s*:\s*([^;{}]+)")
ALLOWED = {  # 속성 → 토큰 대신 쓸 수 있는 값
    "font-size": re.compile(r"^(var\(--fs-[\w-]+\)|[\d.]+em|[\d.]+%|inherit)$"),
    "font-weight": re.compile(r"^(var\(--fw-[\w-]+\)|inherit|normal)$"),
    "letter-spacing": re.compile(r"^(var\(--ls-[\w-]+\)|0|normal|inherit)$"),
    "border-radius": re.compile(r"^((var\(--r-[\w-]+\)|0|50%|inherit)\s*)+$"),
    "z-index": re.compile(r"^(var\(--z-[\w-]+\)|-1|[0-9]|10|auto|inherit)$"),  # -1~10 은 컴포넌트 안 쌓임 순서
}


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def strip(css):
    """주석과 인쇄용 규칙(종이에는 테마가 없다)을 뺀다."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    i = css.find("@media print")
    while i != -1:
        depth, j = 0, css.index("{", i)
        for j in range(j, len(css)):
            depth += {"{": 1, "}": -1}.get(css[j], 0)
            if depth == 0:
                break
        css = css[:i] + css[j + 1:]
        i = css.find("@media print")
    return css


def decls(css):
    for m in DECL.finditer(strip(css)):
        yield m.group(1), m.group(2).replace("!important", "").strip()


class TokenTest(unittest.TestCase):
    def test_components_use_tokens(self):
        bad = []
        for path in COMPONENTS:
            name = os.path.basename(path)
            for prop, val in decls(read(path)):
                if prop.startswith("--"):
                    continue
                if COLOR.search(val) and not prop.endswith("mask-image"):  # 마스크의 #000 은 색이 아니라 불투명도다
                    bad.append(f"{name}: {prop}: {val}")
                if prop in ALLOWED and not ALLOWED[prop].match(val):
                    bad.append(f"{name}: {prop}: {val}")
                if prop == "font" and re.search(r"\d+(\.\d+)?px|\b[1-9]00\b", val):
                    bad.append(f"{name}: {prop}: {val}")
                if SPACING.match(prop) and any(u.lower() != "px" or float(n) >= 2 for n, u in LENGTH.findall(val)):
                    bad.append(f"{name}: {prop}: {val}")
        self.assertEqual(bad, [], "토큰 대신 숫자·색을 직접 쓴 선언")

    def test_referenced_vars_are_defined(self):
        css = {p: read(p) for p in [TOKENS] + COMPONENTS}
        defined = {n for s in css.values() for n in re.findall(r"(--[\w-]+)\s*:", s)}
        # JS·HTML 이 style 속성으로 넣는 칸별 값(--v, --n, --i …)
        pages = "".join(read(p) for p in glob.glob(os.path.join(APP, "*.js")) + glob.glob(os.path.join(SETUP, "*.js"))
                        + [os.path.join(STATIC, "index.html"), os.path.join(STATIC, "map", "index.html"),
                           os.path.join(SETUP, "index.html")])
        missing = sorted({n for s in css.values() for n in re.findall(r"var\((--[\w-]+)", s)
                          if n not in defined and n not in pages})
        self.assertEqual(missing, [], "정의되지 않은 토큰 참조")

    def test_dark_blocks_match(self):
        """다크 토큰은 명시 선택([data-theme=dark])과 시스템 설정(prefers-color-scheme) 두 벌이다. 한쪽만 고치면 어긋난다."""
        s = read(TOKENS)
        explicit = re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\n\}', s, re.S).group(1)
        system = re.search(r':root:not\(\[data-theme="light"\]\)\s*\{(.*?)\n  \}', s, re.S).group(1)
        norm = lambda b: sorted(" ".join(d.split()) for d in b.split(";") if d.strip())
        self.assertEqual(norm(explicit), norm(system))


if __name__ == "__main__":
    unittest.main()
