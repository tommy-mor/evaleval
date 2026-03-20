"""SSE helpers for the dual-eval loop."""


def exec_event(js: str | list[str] | tuple[str, ...]) -> str:
    """Wrap JavaScript code as an SSE 'exec' event."""
    if isinstance(js, (list, tuple)):
        js = ";".join(js)
    lines = ["event: exec"]
    for line in js.split('\n'):
        lines.append(f"data: {line}")
    lines += ["", ""]
    return "\n".join(lines)


SHELL_HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body>
<script type="module">
import { Idiomorph } from '__IDIOMORPH_URL__';
window.Idiomorph = Idiomorph;
const es = new EventSource(__SSE_PATH__);
es.addEventListener('exec', e => {
  eval(e.data);
});
document.addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  try {
    const r = await fetch(f.action, { method: 'POST', body: new FormData(f) });
    const t = await r.text();
    if (t) eval(t);
  } catch (err) {
    console.error(err);
  }
  if (f.dataset.reset !== 'false') f.reset();
});
</script>
</body>
</html>"""


def shell_html(sse_path: str | None = None, idiomorph_url: str | None = None) -> str:
    """Generate the shell HTML with optional custom SSE path and Idiomorph URL."""
    idi = idiomorph_url or "https://unpkg.com/idiomorph@0.3.0/dist/idiomorph.esm.js"
    sse = f"'{sse_path}'" if sse_path else "'/sse'"
    return (
        SHELL_HTML_TEMPLATE
        .replace("__IDIOMORPH_URL__", idi)
        .replace("__SSE_PATH__", sse)
    )
