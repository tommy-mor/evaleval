import pytest

from evaleval.signing import (
    Signer,
    SnippetExecutionError,
    apply_snippet_substitutions,
    scrub,
)


def test_scrub_uses_python_repr():
    assert scrub('a"b') == '\'a"b\''


def test_substitutions_replace_longest_keys_first():
    snippet = "go($id, $idx)"
    out = apply_snippet_substitutions(snippet, {"id": "A", "idx": "B"})
    assert out == "go('A', 'B')"


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


def test_verify_snippet_consumes_nonce_and_substitutes_form_data():
    signer = Signer(secret=b"secret")
    snippet = "add($text)"
    nonce = signer.generate_nonce()
    sig = signer.sign(snippet, nonce)

    out = signer.verify_snippet(
        {"__snippet__": snippet, "__sig__": sig, "__nonce__": nonce, "text": "todo"}
    )
    assert out == "add('todo')"

    with pytest.raises(SnippetExecutionError, match="Invalid nonce"):
        signer.verify_snippet(
            {"__snippet__": snippet, "__sig__": sig, "__nonce__": nonce, "text": "todo"}
        )

