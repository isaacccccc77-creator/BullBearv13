"""
Guards against call-before-definition bugs in the app script.

Run with:  python test_ordering.py

Why this exists: Streamlit re-executes the whole script top to bottom on
every interaction, so a helper defined *below* the code that calls it raises
NameError the moment a user touches that control — while passing import
checks, linters and type checkers, because the name does exist at module
scope by the time the file finishes loading.

This has already shipped twice in this codebase: the Settings tab called
send_telegram_message ~1,700 lines before it was defined, and the market
tape called yf_call_with_retry before its definition. Both were invisible
until the button was actually pressed.

The check walks module-level statements in source order and reports any call
to a function that is defined later in the file.
"""

import ast
import os
import sys

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_v30.py")
tree = ast.parse(open(APP, encoding="utf-8").read())

# Line number where each top-level function becomes available.
defined_at = {
    node.name: node.lineno
    for node in tree.body
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
}

problems = set()
for statement in tree.body:
    # def/class bodies don't run during a pass; everything else does,
    # including the bodies of `with` and `if` blocks.
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        continue
    for node in ast.walk(statement):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id
            if name in defined_at and node.lineno < defined_at[name]:
                problems.add((node.lineno, name, defined_at[name]))

for lineno, name, definition_line in sorted(problems):
    print(f"FAIL  line {lineno}: calls {name}(), which is not defined until line {definition_line}")

if problems:
    print(f"\n{len(problems)} ordering problem(s) — these raise NameError at runtime.")
    sys.exit(1)

print(f"No ordering problems ({len(defined_at)} top-level functions checked).")
