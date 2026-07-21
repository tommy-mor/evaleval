import hashlib
import hmac
import uuid
import time
import base64
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


def scrub(value: str) -> str:
    """Return a Python literal for *value*.

    Retained for compatibility. Prefer :func:`bind_snippet`, which never
    splices form data into source text.
    """
    return repr(value)


def _placeholder_matches(snippet: str, form_data: Mapping[str, str]) -> list[tuple[int, int, str]]:
    """Find non-overlapping ``$key`` spans in *snippet* (longest keys first)."""
    matches: list[tuple[int, int, str]] = []
    for key in sorted(form_data.keys(), key=len, reverse=True):
        needle = f"${key}"
        start = 0
        while True:
            idx = snippet.find(needle, start)
            if idx < 0:
                break
            end = idx + len(needle)
            if any(not (end <= m0 or idx >= m1) for m0, m1, _ in matches):
                start = idx + 1
                continue
            matches.append((idx, end, key))
            start = end
    matches.sort(key=lambda m: m[0])
    return matches


@dataclass(frozen=True, slots=True)
class BoundSnippet:
    """A verified snippet with form values bound as Python locals.

    Form data never enters the source string. Placeholders become synthetic
    names; values are passed through the ``eval`` locals mapping.
    """

    source: str
    locals_: dict[str, str]

    def eval(self, globals_dict: dict[str, Any] | None = None, locals_dict: Mapping[str, Any] | None = None) -> Any:
        """Evaluate the bound expression.

        ``globals_dict`` defaults to an empty global namespace; pass
        ``globals()`` (or a deliberate allow-list) so handler names resolve.
        """
        scope = dict(self.locals_)
        if locals_dict:
            scope = {**locals_dict, **scope}
        return eval(self.source, globals_dict if globals_dict is not None else {}, scope)

    def __call__(self, globals_dict: dict[str, Any] | None = None, locals_dict: Mapping[str, Any] | None = None) -> Any:
        return self.eval(globals_dict, locals_dict)


def bind_snippet(snippet: str, form_data: Mapping[str, str]) -> BoundSnippet:
    """Bind ``$key`` placeholders to form values without source splicing.

    Each placeholder in the *original* template is rewritten to a synthetic
    identifier (``__ee_arg_N__``). Form values are supplied as locals when
    evaluating, so nested ``$`` characters inside values cannot invent new
    code or break out of string literals.
    """
    matches = _placeholder_matches(snippet, form_data)
    locals_: dict[str, str] = {}
    parts: list[str] = []
    prev = 0
    for index, (start, end, key) in enumerate(matches):
        sym = f"__ee_arg_{index}__"
        parts.append(snippet[prev:start])
        parts.append(sym)
        locals_[sym] = str(form_data[key])
        prev = end
    parts.append(snippet[prev:])
    return BoundSnippet("".join(parts), locals_)


def apply_snippet_substitutions(snippet: str, form_data: dict[str, str]) -> BoundSnippet:
    """Bind form values into *snippet*.

    Historically this returned a source string with ``repr``-spliced values,
    which allowed RCE via nested ``$`` substitution. It now returns a
    :class:`BoundSnippet`; call ``.eval(globals())`` to execute.
    """
    return bind_snippet(snippet, form_data)


class Signer:
    """HMAC-SHA256 snippet signing with one-time nonces.

    Usage:
        signer = Signer()

        # At render time — embed in the form
        code = "go('whale', $message)"
        hidden_fields = signer.snippet_hidden(code)

        # At /do time — verify, bind, eval
        bound = signer.verify_snippet(form)
        return bound.eval(globals())
    """

    def __init__(self, secret: bytes | None = None, nonce_ttl: int = 3600):
        self.secret = secret or hashlib.sha256(f"snippets-{uuid.uuid4()}".encode()).digest()
        self.nonce_ttl = nonce_ttl
        self._nonces: dict[str, float] = {}
        self._last_nonce_clean: float = 0.0

    def _clean_nonces(self):
        now = time.time()
        if now - self._last_nonce_clean < 60:
            return
        self._last_nonce_clean = now
        for n in [n for n, exp in self._nonces.items() if exp < now]:
            del self._nonces[n]

    def generate_nonce(self) -> str:
        self._clean_nonces()
        nonce = uuid.uuid4().hex
        self._nonces[nonce] = time.time() + self.nonce_ttl
        return nonce

    def consume_nonce(self, nonce: str) -> bool:
        self._clean_nonces()
        if nonce in self._nonces:
            del self._nonces[nonce]
            return True
        return False

    def sign(self, code: str, nonce: str) -> str:
        msg = f"{code}|{nonce}".encode()
        return base64.urlsafe_b64encode(
            hmac.new(self.secret, msg, hashlib.sha256).digest()
        ).decode()

    def verify(self, code: str, nonce: str, sig: str) -> bool:
        return hmac.compare_digest(self.sign(code, nonce), sig)

    def snippet_hidden(self, code: str) -> list:
        """Generate hiccup hidden input fields for a signed snippet."""
        nonce = self.generate_nonce()
        sig = self.sign(code, nonce)
        return [
            ["input", {"type": "hidden", "name": "__snippet__", "value": code}],
            ["input", {"type": "hidden", "name": "__sig__", "value": sig}],
            ["input", {"type": "hidden", "name": "__nonce__", "value": nonce}],
        ]

    def verify_snippet(self, form: Mapping[str, Any]) -> BoundSnippet:
        """Verify signed form payload and return a :class:`BoundSnippet`."""
        snippet = str(form.get("__snippet__", ""))
        sig = str(form.get("__sig__", ""))
        nonce = str(form.get("__nonce__", ""))

        if not all([snippet, sig, nonce]):
            raise SnippetExecutionError("Missing fields", status_code=400)
        if not self.verify(snippet, nonce, sig):
            raise SnippetExecutionError("Invalid signature", status_code=403)
        if not self.consume_nonce(nonce):
            raise SnippetExecutionError("Invalid nonce", status_code=403)

        form_data = {k: str(v) for k, v in form.items() if not k.startswith("__")}
        return bind_snippet(snippet, form_data)

    def eval_snippet(
        self,
        form: Mapping[str, Any],
        globals_dict: dict[str, Any] | None = None,
        locals_dict: Mapping[str, Any] | None = None,
    ) -> Any:
        """Verify, bind, and evaluate a signed snippet in one step."""
        return self.verify_snippet(form).eval(globals_dict, locals_dict)


class SnippetExecutionError(Exception):
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
