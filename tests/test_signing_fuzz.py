"""Property-based / fuzz tests for snippet binding security.

The old ``repr``-splice path was exploitable when a form value contained
another ``$key`` present in the form: the second substitution ran inside an
already-quoted literal and broke out into real code. These tests fuzz that
surface and assert values are only ever bound as data.
"""

from __future__ import annotations

import ast

import pytest
from hypothesis import given, settings, strategies as st

from evaleval.signing import BoundSnippet, Signer, bind_snippet


# Form keys that appear as placeholders in signed templates under test.
_KEYS = st.sampled_from(["text", "a", "b", "x", "id", "idx", "new-todo-body", "message"])

# Values meant to stress quoting, nesting, and code-injection attempts.
_ATTACK_FRAGMENTS = st.sampled_from(
    [
        "",
        "$",
        "$x",
        "$text",
        "$a$b",
        "xx $x yy",
        "'), __import__('os').system('id'), ('",
        '__import__("os").system("id")',
        "`; import os; os.system('id'); #",
        "\\",
        "'",
        '"',
        "'''",
        '"""',
        "\n",
        "\x00",
        "${text}",
        "$new-todo-body",
        "add($text)",
        "__ee_arg_0__",
    ]
)

_VALUES = st.one_of(
    st.text(max_size=64),
    _ATTACK_FRAGMENTS,
    st.binary(max_size=32).map(lambda b: b.decode("utf-8", "surrogateescape")),
)


class _RceCanary(Exception):
    """Raised if evaluation reaches an unexpected dangerous builtin."""


def _hostile_builtins() -> dict:
    def boom(*args, **kwargs):
        raise _RceCanary(args, kwargs)

    return {
        "__import__": boom,
        "eval": boom,
        "exec": boom,
        "compile": boom,
        "open": boom,
        "input": boom,
        "breakpoint": boom,
        "getattr": boom,
        "globals": boom,
        "locals": boom,
        "vars": boom,
        "setattr": boom,
        "delattr": boom,
    }


@given(
    text=st.one_of(st.text(max_size=64), _ATTACK_FRAGMENTS),
    extra_key=_KEYS,
    extra_val=_VALUES,
)
@settings(max_examples=300)
def test_fuzz_bound_values_never_enter_source(text, extra_key, extra_val):
    form = {"text": text}
    if extra_key != "text":
        form[extra_key] = extra_val
    bound = bind_snippet("add($text)", form)

    assert bound.source == "add(__ee_arg_0__)"
    assert list(bound.locals_) == ["__ee_arg_0__"]
    assert bound.locals_["__ee_arg_0__"] == text
    # Extra fields that are not placeholders must not create bindings or
    # alter the rewritten source — even when their values contain `$…`.
    if extra_key != "text":
        assert len(bound.locals_) == 1
        tree = ast.parse(bound.source, mode="eval")
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert names == {"add", "__ee_arg_0__"}


@given(data=st.data())
@settings(max_examples=200)
def test_fuzz_eval_only_invokes_handler_with_exact_args(data):
    keys = data.draw(st.lists(_KEYS, min_size=1, max_size=4, unique=True))
    values = {k: data.draw(_VALUES) for k in keys}
    # Build a template that uses every key once, longest-first safe.
    template = "handler(" + ", ".join(f"${k}" for k in keys) + ")"
    bound = bind_snippet(template, values)

    seen: list[tuple] = []

    def handler(*args):
        seen.append(args)
        return args

    result = bound.eval({"__builtins__": _hostile_builtins(), "handler": handler})
    assert seen == [tuple(values[k] for k in keys)]
    assert result == tuple(values[k] for k in keys)


@given(
    text=st.one_of(st.text(max_size=80), _ATTACK_FRAGMENTS),
    x=st.one_of(st.text(max_size=80), _ATTACK_FRAGMENTS),
)
@settings(max_examples=300)
def test_fuzz_nested_dollar_cannot_rce(text, x):
    """Nested ``$x`` inside ``text`` must not execute attacker-controlled code."""
    bound = bind_snippet("add($text)", {"text": text, "x": x})

    def add(value):
        return ("ok", value)

    try:
        result = bound.eval({"__builtins__": _hostile_builtins(), "add": add})
    except _RceCanary as exc:  # pragma: no cover - failure mode under test
        pytest.fail(f"RCE canary tripped: {exc}")

    assert result == ("ok", text)


@given(message=st.one_of(st.text(max_size=64), _ATTACK_FRAGMENTS))
@settings(max_examples=100)
def test_fuzz_signer_roundtrip_keeps_literal_args(message):
    signer = Signer(secret=b"fuzz-secret")
    snippet = "go('whale', $message)"
    nonce = signer.generate_nonce()
    sig = signer.sign(snippet, nonce)
    bound = signer.verify_snippet(
        {
            "__snippet__": snippet,
            "__sig__": sig,
            "__nonce__": nonce,
            "message": message,
            # Extra fields that look like placeholders must not rewrite source.
            "whale": "NOPE",
            "x": "'), pwned, ('",
        }
    )
    assert isinstance(bound, BoundSnippet)
    tree = ast.parse(bound.source, mode="eval")
    assert isinstance(tree.body, ast.Call)
    out = bound.eval({"__builtins__": _hostile_builtins(), "go": lambda a, b: (a, b)})
    assert out == ("whale", message)


@given(
    keys=st.lists(_KEYS, min_size=1, max_size=5, unique=True),
    body=st.text(
        alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
        max_size=40,
    ),
)
@settings(max_examples=150)
def test_fuzz_source_is_always_parseable_call(keys, body):
    # Embed placeholders amid punctuation that used to confuse string splicing.
    template = "fn(" + ", ".join(f"${k}" for k in keys) + ")"
    form = {k: f"{body}${keys[(i + 1) % len(keys)]}" for i, k in enumerate(keys)}
    bound = bind_snippet(template, form)
    tree = ast.parse(bound.source, mode="eval")
    assert isinstance(tree.body, ast.Call)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            assert node.id == "fn" or node.id.startswith("__ee_arg_")
