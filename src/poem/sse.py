"""SSE helpers for the dual-eval loop."""


def exec_event(js: str) -> str:
    """Wrap a JavaScript string as an SSE 'exec' event."""
    lines = ["event: exec"]
    for line in js.split('\n'):
        lines.append(f"data: {line}")
    lines += ["", ""]
    return "\n".join(lines)


SHELL_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body>
<script type="module">
import { Idiomorph } from 'https://unpkg.com/idiomorph@0.3.0/dist/idiomorph.esm.js';
window.Idiomorph = Idiomorph;
const ssePath = (location.pathname || '/').replace(/\\/$/, '') + '/sse';
const es = new EventSource(ssePath);
es.addEventListener('exec', e => {
  eval(e.data);
});
document.addEventListener('submit', async e => {
  e.preventDefault();
  const f = e.target;
  try {
    const r = await fetch(f.action, { method: 'POST', body: new URLSearchParams(new FormData(f)) });
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
    """Generate the shell HTML with optional custom SSE path and Idiomorph URL.

    If no arguments given, returns the default SHELL_HTML.
    """
    if sse_path is None and idiomorph_url is None:
        return SHELL_HTML

    idi = idiomorph_url or "https://unpkg.com/idiomorph@0.3.0/dist/idiomorph.esm.js"
    sse = f"'{sse_path}'" if sse_path else "(location.pathname || '/').replace(/\\/$/, '') + '/sse'"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body>
<script type="module">
import {{ Idiomorph }} from '{idi}';
window.Idiomorph = Idiomorph;
const es = new EventSource({sse});
es.addEventListener('exec', e => {{
  eval(e.data);
}});
document.addEventListener('submit', async e => {{
  e.preventDefault();
  const f = e.target;
  try {{
    const r = await fetch(f.action, {{ method: 'POST', body: new URLSearchParams(new FormData(f)) }});
    const t = await r.text();
    if (t) eval(t);
  }} catch (err) {{
    console.error(err);
  }}
  if (f.dataset.reset !== 'false') f.reset();
}});
</script>
</body>
</html>"""
