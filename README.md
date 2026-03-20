# strophe

The entire client.

```js
import { Idiomorph } from 'idiomorph';
window.Idiomorph = Idiomorph;
const es = new EventSource('/sse');
es.addEventListener('exec', e => eval(e.data));
document.addEventListener('submit', async e => {
  e.preventDefault();
  const r = await fetch(e.target.action, { method: 'POST', body: new URLSearchParams(new FormData(e.target)) });
  const t = await r.text();
  if (t) eval(t);
});
```

The entire server.

```python
@app.post("/do")
async def do(request):
    form = await request.form()
    snippet, sig, nonce = form['__snippet__'], form['__sig__'], form['__nonce__']
    if not verify(snippet, nonce, sig):
        return 403
    if not consume_nonce(nonce):
        return 403
    for key, value in form.items():
        snippet = snippet.replace(f"${key}", repr(value))
    return eval(snippet)
```

Pages are data.

```python
def login_form():
    return ["div.login",
        ["form", {"action": "/do", "method": "post"},
            *snippet_hidden("login($password)"),
            ["input", {"type": "password", "name": "password"}],
            ["button", "enter"],
        ],
    ]
```

Forms carry their own handlers. Signed, nonced, one-use.

```python
snippet_hidden("delete_item($id)")
# => [
#   ["input", {"type": "hidden", "name": "__snippet__", "value": "delete_item($id)"}],
#   ["input", {"type": "hidden", "name": "__sig__",     "value": "a8Kj..."}],
#   ["input", {"type": "hidden", "name": "__nonce__",   "value": "e7f2..."}],
# ]
```

The server pushes JS through SSE. The chains say how many parts they have, then become strings and disappear.

```python
from poem import One, Three, Four, Selector, MORPH, PREPEND, CLASSES, ADD

Three[Selector("#events")][PREPEND][rendered]
# => "document.querySelector(\"#events\").insertAdjacentHTML('afterbegin', `<div>...</div>`)"

Three[Selector("form.go-form")][MORPH][new_form]
# => "Idiomorph.morph(document.querySelector(\"form.go-form\"), `<form>...</form>`)"

Four[Selector(".btn")][CLASSES][ADD]["sending"]
# => "document.querySelector(\".btn\")?.classList.add('sending')"
```

Three endpoints. No framework.

```python
GET  *      # serve the shell
GET  */sse  # push what you see
POST /do    # verify, eval
```

## Example

[strophe-todo](https://github.com/tommy-mor/strophe-todo) — a todo list in ~170 lines.
