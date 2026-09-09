"""
Writes an instrumented copy of the app that reports, in its own status bar,
where a rerun actually spent its time.

    python profile_app.py /tmp/app_profiled.py
    streamlit run /tmp/app_profiled.py

Then interact with the app and read the status bar along the bottom.

Why this exists: Streamlit re-executes the entire script on every
interaction, so "the app feels slow" is never localised to the thing you
just clicked. Guessing is worse than useless here — the first guess on
this codebase was that thirteen tab bodies were the cost, and measuring
showed the whole tab rail was 130ms of a 1,080ms rerun while two uncached
backtests were 695ms of it. Ten minutes of measurement replaced a
refactor of several thousand lines with a twelve-line change.

How it works: the app is parsed, and each statement gets a
`time.perf_counter()` before it and an accumulate-into-a-dict after it.
Nothing is re-indented and no statement is moved, so the instrumented
copy behaves exactly like the original.

Two details that are easy to get wrong and are handled here:

* Nested spans overlap, so anything that inserts by list index goes stale
  the moment an inner marker is written inside an outer one's range.
  Markers are accumulated per source line and emitted in one pass instead.
* An `elif` is an `If` nested in the parent's `orelse`, and a marker
  emitted before it would land between a block and its own `elif`, which
  is a syntax error rather than a measurement. Only `body` is descended.
"""

import ast
import sys
from collections import defaultdict

APP = "app_v30.py"
ORIGIN = "_render_start = time.perf_counter()"

# Don't descend into anything shorter than this — the marker overhead
# starts to matter relative to what is being measured.
MIN_LINES_TO_DESCEND = 10
MAX_DEPTH = 3
TOP_N = 18


def build(src: str) -> str:
    tree = ast.parse(src)
    lines = src.splitlines(keepends=True)
    origin = next(i for i, l in enumerate(lines, 1) if l.startswith(ORIGIN))

    def indent_of(lineno: int) -> str:
        line = lines[lineno - 1]
        return line[: len(line) - len(line.lstrip())]

    def label_for(node, prefix: str) -> str:
        if isinstance(node, ast.With) and isinstance(node.items[0].context_expr, ast.Name):
            return prefix + node.items[0].context_expr.id
        if isinstance(node, ast.With):
            return f"{prefix}with@{node.lineno}"
        if isinstance(node, ast.Try):
            return f"{prefix}try@{node.lineno}"
        if isinstance(node, ast.If):
            return f"{prefix}if({ast.unparse(node.test)[:16]})"
        if isinstance(node, (ast.For, ast.While)):
            return f"{prefix}loop@{node.lineno}"
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            return f"{prefix}{ast.unparse(node.value.func)[:22]}@{node.lineno}"
        return f"{prefix}{type(node).__name__}@{node.lineno}"

    def walk(body, prefix, depth):
        spans = []
        for node in body:
            # A def or a class costs a name binding at module scope; the
            # body only runs when something calls it, and that call is
            # already inside whatever statement is being timed.
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                                 ast.Import, ast.ImportFrom)):
                continue
            if depth == MAX_DEPTH and node.lineno <= origin:
                continue  # nothing above the timer's origin can be measured by it
            end = node.end_lineno or node.lineno
            label = label_for(node, prefix)
            spans.append((label, node.lineno, end, depth))
            if depth > 0 and end - node.lineno > MIN_LINES_TO_DESCEND and getattr(node, "body", None):
                spans += walk(node.body, label.split("/")[-1][:14] + "/", depth - 1)
        return spans

    spans = walk(tree.body, "", MAX_DEPTH)

    before, after = defaultdict(list), defaultdict(list)
    for i, (label, start, end, depth) in enumerate(spans):
        pad, var = indent_of(start), f"_prof{i}"
        key = label.replace('"', "'")
        # depth counts DOWN as we nest, so the outermost span holds the
        # largest value. It has to open first and close last, or an inner
        # marker ends up outside the block it is timing.
        before[start].append((-depth, f"{pad}{var} = time.perf_counter()\n"))
        after[end].append((depth, f'{pad}_PROF["{key}"] = _PROF.get("{key}", 0.0) + '
                                  f"(time.perf_counter() - {var}) * 1000\n"))

    out = []
    for n, text in enumerate(lines, 1):
        out += [t for _, t in sorted(before.get(n, []), key=lambda x: x[0])]
        out.append(text)
        out += [t for _, t in sorted(after.get(n, []), key=lambda x: x[0])]

    body = "".join(out).replace(ORIGIN, ORIGIN + "\n_PROF = {}", 1)
    body = body.replace(
        "<span class=\"tv-sb-item\">{(time.perf_counter() - _render_start) * 1000:.0f}ms</span>",
        "<span class=\"tv-sb-item\" id=\"tv-prof\">TOTAL "
        "{(time.perf_counter() - _render_start) * 1000:.0f}ms || "
        "{\" | \".join(f\"{k}={v:.0f}\" for k, v in "
        f"sorted(_PROF.items(), key=lambda kv: -kv[1])[:{TOP_N}])}}</span>",
        1,
    )
    ast.parse(body)  # never hand back something that will not run
    return len(spans), body


def main() -> None:
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    import os
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), APP)
    count, out = build(open(src_path, encoding="utf-8").read())
    open(sys.argv[1], "w", encoding="utf-8").write(out)
    print(f"instrumented {count} statements -> {sys.argv[1]}")
    print("run it with:  streamlit run", sys.argv[1])


if __name__ == "__main__":
    main()
