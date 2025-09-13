import io
import re
import ast
import tokenize
import streamlit as st
from pathlib import Path

TARGET = Path(__file__).with_name("streamlit_app.py")

st.set_page_config(page_title="Syntax Debug", page_icon="🧪")

st.title("🧪 Syntax & Indentation Debugger")
st.caption(f"Scanning: `{TARGET}`")

code = TARGET.read_text(encoding="utf-8", errors="replace")
lines = code.splitlines()

def excerpt(ln, radius=6):
    lo = max(1, ln - radius)
    hi = min(len(lines), ln + radius)
    block = "\n".join(
        f"{i:>5}  {lines[i-1]}" for i in range(lo, hi + 1)
    )
    return block

# ---------- 1) Try to compile (AST) ----------
st.header("1) AST compile check")
try:
    ast.parse(code, filename=str(TARGET), mode="exec")
    st.success("✅ AST parse OK (no SyntaxError).")
except SyntaxError as e:
    st.error(f"❌ SyntaxError: {e.msg} @ line {e.lineno}, col {e.offset}")
    st.code(excerpt(e.lineno), language="python")

# ---------- 2) Tab/space mix & indent check ----------
st.header("2) Tab/space & indent scan")
has_tabs = any("\t" in ln for ln in lines)
if has_tabs:
    idxs = [i + 1 for i, ln in enumerate(lines) if "\t" in ln]
    st.warning(f"Found TABs on lines: {idxs[:20]}{' ...' if len(idxs)>20 else ''}")
else:
    st.success("No hard TAB characters found.")

# ---------- 3) Find unclosed try blocks (simple) ----------
st.header("3) Unclosed try blocks (heuristic)")
stack = []  # list of (indent, line_no, text)
unclosed = []

for i, raw in enumerate(lines, 1):
    stripped = raw.lstrip()
    indent = len(raw) - len(stripped)

    # skip empty/comment-only
    if not stripped or stripped.startswith("#"):
        continue

    # crude: only match top keywords on this line
    if re.match(r"^try:\s*$", stripped):
        stack.append((indent, i, raw))
        continue

    if re.match(r"^(except\b.*:|finally:\s*)$", stripped):
        # close last try at this indent
        for j in range(len(stack) - 1, -1, -1):
            if stack[j][0] == indent:
                stack.pop(j)
                break

unclosed = stack[:]
if unclosed:
    st.error("❌ Unclosed try blocks (same-indent 'except/finally' missing):")
    for indent, ln, text in unclosed:
        st.code(f"line {ln}: {text.rstrip()}")
        st.code(excerpt(ln), language="python")
else:
    st.success("No obvious unclosed try blocks found.")

# ---------- 4) Bracket / quote balance (token-based) ----------
st.header("4) Brackets & strings balance")
openers = "([{"
closers = ")]}"
pairs = {")": "(", "]": "[", "}": "{"}
stack_b = []
ok_tokens = True
bad_info = None

try:
    toks = tokenize.generate_tokens(io.StringIO(code).readline)
    for tok in toks:
        ttype, tstring, (sl, sc), (el, ec), _ = tok
        # token module ignores braces inside strings/comments
        if ttype == tokenize.OP:
            if tstring in openers:
                stack_b.append((tstring, sl, sc))
            elif tstring in closers:
                if not stack_b or stack_b[-1][0] != pairs[tstring]:
                    ok_tokens = False
                    bad_info = (tstring, sl, sc)
                    break
                stack_b.pop()
except tokenize.TokenError as te:
    ok_tokens = False
    st.error(f"Tokenize error: {te}")

if ok_tokens and not stack_b:
    st.success("Brackets look balanced.")
else:
    if not ok_tokens and bad_info:
        t, ln, col = bad_info
        st.error(f"Unmatched closer `{t}` at line {ln}, col {col}")
        st.code(excerpt(ln), language="python")
    elif stack_b:
        last, ln, col = stack_b[-1]
        st.error(f"Unclosed opener `{last}` started at line {ln}, col {col}")
        st.code(excerpt(ln), language="python")

# ---------- 5) Quick grep for suspicious multi-line strings ----------
st.header("5) Multi-line string sanity")
triple_counts = (
    code.count("'''") + code.count('"""')
)
if triple_counts % 2 != 0:
    st.warning(
        "Odd number of triple quote markers found (possible unclosed string)."
    )
else:
    st.success("Triple-quoted strings count is even.")

st.divider()
st.caption(
    "Tip: fix the first error shown above, save, rerun this page; "
    "then switch main file back to your app."
)
