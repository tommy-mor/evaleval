import hashlib
import hmac
import uuid
import time
import base64


def scrub(value: str) -> str:
    """Escape a form value so it can't break out of an eval context."""
    return repr(value)


def apply_snippet_substitutions(snippet: str, form_data: dict[str, str]) -> str:
    """Replace $key placeholders; longest keys first so $idx is not broken by $id."""
    for key, value in sorted(form_data.items(), key=lambda x: len(x[0]), reverse=True):
        snippet = snippet.replace(f"${key}", scrub(value))
    return snippet


class Signer:
    """HMAC-SHA256 snippet signing with one-time nonces.

    Usage:
        signer = Signer()

        # At render time — embed in the form
        code = "go('whale', $message)"
        hidden_fields = signer.snippet_hidden(code)

        # At /do time — verify and consume
        if not signer.verify(snippet, nonce, sig):
            return 403
        if not signer.consume_nonce(nonce):
            return 403
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
