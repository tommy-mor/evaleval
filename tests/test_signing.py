import pytest

from evaleval.signing import (
    BoundSnippet,
    Signer,
    SnippetExecutionError,
    apply_snippet_substitutions,
    bind_snippet,
    scrub,
)


def test_scrub_uses_python_repr():
    assert scrub('a"b') == '\'a"b\''


def test_bind_replaces_longest_keys_first():
    snippet = "go($id, $idx)"
    bound = bind_snippet(snippet, {"id": "A", "idx": "B"})
    assert isinstance(bound, BoundSnippet)
    assert bound.source == "go(__ee_arg_0__, __ee_arg_1__)"
    assert bound.locals_ == {"__ee_arg_0__": "A", "__ee_arg_1__": "B"}
    assert bound.eval({"go": lambda a, b: (a, b)}) == ("A", "B")


def test_apply_snippet_substitutions_returns_bound_snippet():
    bound = apply_snippet_substitutions("go($id, $idx)", {"id": "A", "idx": "B"})
    assert bound.eval({"go": lambda a, b: (a, b)}) == ("A", "B")


def test_nested_dollar_in_value_does_not_resubstitute():
    """Regression: splicing repr() allowed `$x` inside values to break out."""
    calls: list[str] = []

    class FakeOs:
        def system(self, cmd: str) -> int:
            calls.append(cmd)
            return 0

    def fake_import(name, *args, **kwargs):
        if name == "os":
            return FakeOs()
        raise ImportError(name)

    payload = "'), __import__('os').system('echo RCE'), ('"
    bound = bind_snippet("add($text)", {"text": "xx $x yy", "x": payload})

    assert bound.source == "add(__ee_arg_0__)"
    assert "$" not in bound.source
    assert bound.locals_["__ee_arg_0__"] == "xx $x yy"

    result = bound.eval(
        {"__builtins__": {"__import__": fake_import}},
        {"add": lambda *a: a},
    )
    assert result == ("xx $x yy",)
    assert calls == []


def test_hyphenated_form_keys_bind():
    bound = bind_snippet("add($new-todo-body)", {"new-todo-body": "milk"})
    assert bound.eval({"add": lambda t: t}) == "milk"


def test_sign_and_verify_roundtrip():
    signer = Signer(secret=b"secret", nonce_ttl=60)
    nonce = signer.generate_nonce()
    sig = signer.sign("add($text)", nonce)
    assert signer.verify("add($text)", nonce, sig) is True
    assert signer.verify("add($text) ", nonce, sig) is False


def test_verify_snippet_rejects_missing_fields():
    signer = Signer(secret=b"secret")
    with pytest.raises(SnippetExecutionError, match="Missing fields") as exc:
        signer.verify_snippet({})
    assert exc.value.status_code == 400


def test_verify_snippet_rejects_bad_signature():
    signer = Signer(secret=b"secret")
    nonce = signer.generate_nonce()
    with pytest.raises(SnippetExecutionError, match="Invalid signature") as exc:
        signer.verify_snippet(
            {
                "__snippet__": "add($text)",
                "__sig__": "bad",
                "__nonce__": nonce,
                "text": "x",
            }
        )
    assert exc.value.status_code == 403


def test_verify_snippet_consumes_nonce_and_binds_form_data():
    signer = Signer(secret=b"secret")
    snippet = "add($text)"
    nonce = signer.generate_nonce()
    sig = signer.sign(snippet, nonce)

    bound = signer.verify_snippet(
        {"__snippet__": snippet, "__sig__": sig, "__nonce__": nonce, "text": "todo"}
    )
    assert bound.eval({"add": lambda t: f"got:{t}"}) == "got:todo"

    with pytest.raises(SnippetExecutionError, match="Invalid nonce"):
        signer.verify_snippet(
            {"__snippet__": snippet, "__sig__": sig, "__nonce__": nonce, "text": "todo"}
        )


def test_eval_snippet_helper():
    signer = Signer(secret=b"secret")
    snippet = "go('whale', $message)"
    nonce = signer.generate_nonce()
    sig = signer.sign(snippet, nonce)
    out = signer.eval_snippet(
        {"__snippet__": snippet, "__sig__": sig, "__nonce__": nonce, "message": "hi"},
        {"go": lambda animal, msg: (animal, msg)},
    )
    assert out == ("whale", "hi")
