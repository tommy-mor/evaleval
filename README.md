# strophe

The entire client.

```js
import { Idiomorph } from 'idiomorph';
window.Idiomorph = Idiomorph;
const es = new EventSource('/sse');
es.addEventListener('exec', e => eval(e.data));
document.addEventListener('submit', async e => {
  e.preventDefault();
  const r = await fetch(e.target.action, { method: 'POST', body: new FormData(e.target) });
  const t = await r.text();
  if (t) eval(t);
});
```

Three endpoints. No framework.

```
GET  /       — serve the shell
GET  /sse    — push what you see
POST /     — verify, eval
```

## Example: [strophe-todo](https://github.com/tommy-mor/strophe-todo)

A todo list in ~170 lines.

Forms are data. They carry their own signed handlers.

```python
from strophe import Signer, Three, Two, Selector, MORPH, APPEND, REMOVE

signer = Signer()

def add_form():
    return ["form", {"action": "/", "method": "post"},
        *signer.snippet_hidden("add($text)"),
        ["input", {"type": "text", "name": "text", "placeholder": "what needs doing?"}],
        ["button", {"type": "submit"}, "add"],
    ]
```

Sandbox functions return JS patch chains. The chains say how many parts they have, then become strings and disappear.

```python
def add(text):
    t = {"id": uuid.uuid4().hex[:8], "text": text, "done": False}
    TODOS.append(t)
    return PlainTextResponse(";".join([
        Three[Selector("#add-form")][MORPH][add_form()],
        Three[Selector("#todo-list")][APPEND][todo_item(t)],
        Three[Selector("p.count")][MORPH][remaining_count()],
    ]))

def delete(todo_id):
    TODOS.remove(_find(todo_id))
    return PlainTextResponse(";".join([
        Two[Selector(f"#todo-{todo_id}")][REMOVE],
        Three[Selector("p.count")][MORPH][remaining_count()],
    ]))
```

The `POST /` route verifies the signature and evals the snippet. `verify_snippet` raises `SnippetExecutionError` with a status code.

```python
from strophe import SnippetExecutionError

@app.post("/")
async def do(request):
    form = await request.form()
    try:
        snippet = signer.verify_snippet(form)
        return eval(snippet)
    except SnippetExecutionError as e:
        return PlainTextResponse(e.message, status_code=e.status_code)
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)
```

`eval` only accepts a single expression. Multi-line snippets and `return ...` statements will fail with `SyntaxError`.

If you want side effects plus a return value, use an expression pattern like:

```python
(print("form callback for add todo", $text), add($text))[1]
```

SSE pushes the initial page as JS the browser evals.

```python
from strophe import exec_event, shell_html, One, Eval

@app.get("/")
async def index():
    return HTMLResponse(shell_html())

@app.get("/sse")
async def sse(request):
    async def generate():
        yield exec_event([
            One[Eval("document.title = 'todos'")],
            Three[Selector("body")][MORPH][["body", page()]],
        ])
    return StreamingResponse(generate(), media_type="text/event-stream")
```

`pip install strophe`
