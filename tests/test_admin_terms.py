"""The console's terminology dictionary has to keep up with the API.

The control plane's whole surface is Chinese business language: an operator
reads 「停用业务线」, never `override.add.enabled`. That mapping lives in one
file, `static/terms.js`, and nothing enforces it at runtime -- an untranslated
action falls back to its raw name and simply appears in the audit log looking
like a schema dump.

So it is enforced here instead. Add a `cp.record(...)` to `admin/api.py`
without adding a line to `terms.js` and this test says which one is missing,
rather than the audit log saying it three weeks later.

No database and no browser: this reads both files as text, which is why it
runs in the ordinary `make test` sweep rather than behind `test-live`.
"""

import ast
import json
import re
from pathlib import Path

from nexus.registry.effective import CAPABILITY_FIELDS

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "src" / "nexus" / "admin" / "api.py"
STATIC = ROOT / "src" / "nexus" / "admin" / "static"


def recorded_actions() -> set[str]:
    """Every action name `admin/api.py` can write to the audit log.

    Derived from the source rather than typed out here, so a new action is
    covered the moment it is written -- a hand-maintained list in a test is a
    list that silently stops covering things, which is the failure mode
    `conftest._settings_env_names` exists to avoid for settings.
    """
    tree = ast.parse(API.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "record"):
            continue
        if len(node.args) < 2:
            continue
        action = node.args[1]
        if isinstance(action, ast.Constant) and isinstance(action.value, str):
            found.add(action.value)
        elif isinstance(action, ast.JoinedStr):
            # `f"override.add.{field}"` -- the prefix is literal and the hole
            # can only be a capability field, so expand it rather than skip it.
            prefix = "".join(
                p.value for p in action.values
                if isinstance(p, ast.Constant) and isinstance(p.value, str)
            )
            found.update(prefix + f for f in CAPABILITY_FIELDS)
    return found


def terms_table(name: str) -> dict:
    """Pull one object literal out of terms.js without running a JS engine."""
    src = (STATIC / "terms.js").read_text(encoding="utf-8")
    start = src.index(f"\n  {name}: {{")
    depth, i = 0, src.index("{", start)
    body_start = i
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    body = src[body_start : i + 1]
    # Strip comments and trailing commas, quote bare keys, then let the JSON
    # parser do the rest. The alternative is a regex over the whole file,
    # which would quietly match keys inside comments.
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    body = re.sub(r"(\w[\w.]*)\s*:", lambda m: f'"{m.group(1)}":', body)
    body = re.sub(r'""(\w[\w.]*)"":', r'"\1":', body)
    body = re.sub(r",\s*}", "}", body)
    return json.loads(body)


def test_every_audit_action_has_a_chinese_name():
    actions = recorded_actions()
    assert actions, "parsed no actions out of admin/api.py -- the parser broke"
    translated = terms_table("action")
    missing = sorted(actions - set(translated))
    assert not missing, (
        f"这些审计动作没有中文名，会以原始字段名出现在操作日志里：{missing}。"
        f"请在 static/terms.js 的 action 表里补上。"
    )


def test_no_stale_translations():
    """A name in the dictionary that the API can no longer emit is dead text."""
    stale = sorted(set(terms_table("action")) - recorded_actions())
    assert not stale, f"terms.js 里这些动作 api.py 已经不会产生了：{stale}"


def test_every_capability_field_is_translated():
    translated = terms_table("field")
    missing = [f for f in CAPABILITY_FIELDS if f not in translated]
    assert not missing, f"这些策略字段没有中文名：{missing}"


def test_the_page_does_not_leak_schema_words_to_the_operator():
    """The words this rework exists to remove must not be back in the markup.

    Checked against the shell and the script, not against `terms.js` (which
    necessarily contains the English side of every mapping) and not against
    the raw config the operator hands to engineering, which has to keep its
    original keys or it will not parse.
    """
    banned = ["nano-USD", "cross_tenant_read", "substitutable_to", "zero_touch"]
    html = (STATIC / "admin.html").read_text(encoding="utf-8")
    for word in banned:
        assert word not in html, f"admin.html 里还有技术术语 {word!r}"

    js = (STATIC / "admin.js").read_text(encoding="utf-8")
    # In admin.js these may appear only as API field names in request bodies
    # and as keys into T.*, never inside a string shown to a person. Anything
    # between CJK quotation marks is operator-facing text.
    for shown in re.findall(r"[「『]([^」』]*)[」』]", js):
        for word in banned:
            assert word not in shown, f"admin.js 的界面文案里出现了 {word!r}：{shown!r}"
