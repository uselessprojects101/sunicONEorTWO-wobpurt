import ast
import io
import pathlib
import re
import sys
import tokenize

ROOT = pathlib.Path(__file__).resolve().parent.parent

FILES = [
    "build.sh",
    "tools/setup_web.py",
    "web/shell.html",
    "web/net.js",
    "web/NetworkingWeb.cpp",
    "web/Emscripten.cmake",
    "server/relay.js",
    "server/selftest.js",
]

JS_KEYWORDS = {
    "return", "typeof", "case", "in", "of", "void", "delete", "throw", "new",
    "instanceof", "else", "do", "yield", "await",
}
JS_REGEX_AFTER = set("(,=:[!&|?{};+-*%<>~^")


def js_scan(src, i, spans, stop_on_close):
    n = len(src)
    depth = 0
    regex_ok = True
    while i < n:
        c = src[i]
        if c in " \t\r\n":
            i += 1
            continue
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            spans.append((i, j))
            i = j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((i, j))
            i = j
            continue
        if c in "\"'":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i += 1
            regex_ok = False
            continue
        if c == "`":
            i += 1
            while i < n and src[i] != "`":
                if src[i] == "\\":
                    i += 2
                elif src.startswith("${", i):
                    i = js_scan(src, i + 2, spans, True)
                else:
                    i += 1
            i += 1
            regex_ok = False
            continue
        if c == "/":
            if regex_ok:
                i += 1
                in_class = False
                while i < n:
                    ch = src[i]
                    if ch == "\\":
                        i += 2
                        continue
                    if ch == "[":
                        in_class = True
                    elif ch == "]":
                        in_class = False
                    elif ch == "/" and not in_class:
                        break
                    i += 1
                i += 1
                while i < n and (src[i].isalnum() or src[i] == "_"):
                    i += 1
                regex_ok = False
            else:
                i += 1
                regex_ok = True
            continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            regex_ok = src[i:j] in JS_KEYWORDS
            i = j
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if stop_on_close and depth == 0:
                return i + 1
            depth -= 1
        regex_ok = c in JS_REGEX_AFTER or c == "}"
        i += 1
    return i


def js_spans(src):
    spans = []
    js_scan(src, 0, spans, False)
    return spans


def css_spans(src):
    spans = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            i += 1
            while i < n and src[i] != c:
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def html_spans(src):
    spans = []
    pattern = re.compile(r"<!--|<script\b[^>]*>|<style\b[^>]*>", re.I)
    i = 0
    while True:
        m = pattern.search(src, i)
        if not m:
            break
        tag = m.group(0).lower()
        if tag == "<!--":
            j = src.find("-->", m.end())
            j = len(src) if j < 0 else j + 3
            spans.append((m.start(), j))
            i = j
            continue
        kind = "script" if tag.startswith("<script") else "style"
        j = src.lower().find("</" + kind, m.end())
        j = len(src) if j < 0 else j
        body = src[m.end():j]
        finder = js_spans if kind == "script" else css_spans
        if kind == "script" and "src=" in tag:
            finder = lambda s: []
        for a, b in finder(body):
            spans.append((m.end() + a, m.end() + b))
        i = j
    return spans


def cpp_spans(src):
    spans = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = i
            while True:
                j = src.find("\n", j)
                if j < 0:
                    j = n
                    break
                if src[j - 1] == "\\":
                    j += 1
                    continue
                break
            spans.append((i, j))
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            spans.append((i, j))
            i = j
        elif c == "R" and src.startswith('R"', i) and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] == "_")):
            k = src.find("(", i)
            delim = src[i + 2:k]
            end = src.find(")" + delim + '"', k)
            i = n if end < 0 else end + len(delim) + 2
        elif c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif c == "'":
            if i > 0 and src[i - 1].isalnum():
                i += 1
                continue
            i += 1
            while i < n and src[i] != "'":
                i += 2 if src[i] == "\\" else 1
            i += 1
        else:
            i += 1
    return spans


def cmake_spans(src):
    spans = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif c == "\\":
            i += 2
        elif c == "#":
            if src.startswith("#[[", i):
                j = src.find("]]", i + 3)
                j = n if j < 0 else j + 2
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def sh_spans(src):
    spans = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "'":
            j = src.find("'", i + 1)
            i = n if j < 0 else j + 1
        elif c == '"':
            i += 1
            while i < n and src[i] != '"':
                i += 2 if src[i] == "\\" else 1
            i += 1
        elif c == "\\":
            i += 2
        elif c == "$" and src.startswith("$#", i):
            i += 2
        elif c == "{" and i > 0 and src[i - 1] == "$" and src.startswith("#", i + 1):
            i += 2
        elif c == "#" and (i == 0 or src[i - 1] in " \t\n;(&|"):
            j = src.find("\n", i)
            j = n if j < 0 else j
            if not (i == 0 and src.startswith("#!")):
                spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def py_spans(src):
    spans = []
    starts = [0]
    for line in src.split("\n"):
        starts.append(starts[-1] + len(line) + 1)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            if tok.start[0] == 1 and tok.string.startswith("#!"):
                continue
            a = starts[tok.start[0] - 1] + tok.start[1]
            b = starts[tok.end[0] - 1] + tok.end[1]
            spans.append((a, b))
    lines = src.split("\n")

    def offset(lineno, col_bytes):
        text = lines[lineno - 1].encode("utf-8")[:col_bytes].decode("utf-8")
        return starts[lineno - 1] + len(text)

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if len(body) > 1 and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                first = body[0]
                spans.append((offset(first.lineno, first.col_offset), offset(first.end_lineno, first.end_col_offset)))
    return sorted(spans)


def apply_spans(src, spans):
    spans = sorted(spans)
    out = []
    pos = 0
    removed = 0
    for a, b in spans:
        if a < pos:
            continue
        line_start = src.rfind("\n", 0, a) + 1
        line_end = src.find("\n", b)
        line_end = len(src) if line_end < 0 else line_end
        before = src[line_start:a]
        after = src[b:line_end]
        if before.strip() == "" and after.strip() == "":
            out.append(src[pos:line_start])
            pos = min(line_end + 1, len(src))
        elif after.strip() == "":
            out.append(src[pos:a].rstrip(" \t"))
            pos = b
        else:
            out.append(src[pos:a])
            left = src[a - 1] if a > 0 else " "
            right = src[b] if b < len(src) else " "
            if (left.isalnum() or left == "_") and (right.isalnum() or right == "_"):
                out.append(" ")
            pos = b
        removed += 1
    out.append(src[pos:])
    text = "".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if text.startswith("\n"):
        text = text.lstrip("\n")
    if not text.endswith("\n"):
        text += "\n"
    return text, removed


def finder_for(path):
    name = path.name
    suffix = path.suffix.lower()
    if suffix == ".html":
        return html_spans
    if suffix in (".js", ".mjs"):
        return js_spans
    if suffix in (".cpp", ".hpp", ".c", ".h"):
        return cpp_spans
    if suffix == ".cmake" or name == "CMakeLists.txt":
        return cmake_spans
    if suffix == ".sh":
        return sh_spans
    if suffix == ".py":
        return py_spans
    return None


def main():
    targets = [ROOT / f for f in FILES]
    for extra in sorted((ROOT / "server").glob("*.js")):
        if extra not in targets:
            targets.append(extra)
    total = 0
    for path in targets:
        if not path.exists():
            continue
        finder = finder_for(path)
        src = path.read_text(encoding="utf-8")
        if "\r\n" in src:
            src = src.replace("\r\n", "\n")
        spans = finder(src)
        if not spans:
            print(f"  no comments: {path.relative_to(ROOT)}")
            continue
        text, removed = apply_spans(src, spans)
        path.write_text(text, encoding="utf-8")
        total += removed
        print(f"  removed {removed:3d}: {path.relative_to(ROOT)}")
    print(f"Done. {total} comments removed.")


if __name__ == "__main__":
    sys.exit(main())
