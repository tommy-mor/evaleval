# evaleval

Browser DOM is modified _ONLY_ through *javascript code snippets* sent over the wire to the browers **eval** function.

Backend actions execute _ONLY_ through *python code snippets* sent over the wire to python's **eval** function.

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

```python
from evaleval import SnippetExecutionError

@app.post("/")
async def do(request):
    form = await request.form()
    try:
        # Form values are bound as locals — never spliced into source.
        return signer.verify_snippet(form).eval(globals())

    except SnippetExecutionError as e:
        return PlainTextResponse(e.message, status_code=e.status_code)
```

## Example: [evaleval-todo](https://github.com/tommy-mor/evaleval-todo)

`evaleval` also includes a quick implementation of clojure's [hiccup](https://github.com/weavejester/hiccup), a data-driven embedded DSL for rendering DOM nodes in an ergonomic way.

Observe this example:
```python
from evaleval import Signer
from evaleval.depth import Three, Two
from evaleval.patch import Selector, MORPH, APPEND, REMOVE

signer = Signer()

def add_form():
    return ["form", {"action": "/", "method": "post"},
        *signer.snippet_hidden("add($new-todo-body)"),
        ["input", {"type": "text", "name": "new-todo-body", "placeholder": "what needs doing?"}],
        ["button", {"type": "submit"}, "add"],
    ]
```
All forms have a handler. In a traditional stack, it would be pointed to by a url which points to a routing table which points to a handler function. In `evaleval`, the handler is _embedded into the form itself_.

The signed template `add($new-todo-body)` is verified, then `$new-todo-body` is bound as a Python local (not spliced into source via `repr`). That avoids classic nested-`$` breakouts: form data never becomes code. The expression is evaluated and — as you'll see later — the result is _returned directly to the client_.

So the handler function from the form is called directly with form arguments. And it returns javscript code. Now how do you write js snippets ergonomically in python? You could write them directly:
```python
def add(text):
    t = {"id": uuid.uuid4().hex[:8], "text": text, "done": False}
    TODOS.append(t)
    escaped = t["text"].replace("`", "\\`")
    return PlainTextResponse(f"""
Idiomorph.morph(document.querySelector('#add-form'), `<form id="add-form">...</form>`);
document.querySelector('#todo-list').insertAdjacentHTML('beforeend', `<li id="todo-{t["id"]}">{escaped}</li>`);
Idiomorph.morph(document.querySelector('p.count'), `<p class="count">...</p>`);
console.log('todo added', {text!r});
""")
```

Ew.

However, I have instead built an embedded data-driven DSL much like [specter](https://github.com/redplanetlabs/specter), which lets you construct js snippets in fluent python.
The number we are indexing into is the arity of how deep we can index into until it executes the path, rendering it into a js string.
The details of this process are fairly simple and are described [here](https://github.com/tommy-mor/evaleval/blob/main/src/evaleval/patch.py).
The indexable arity objects are also just very cool.

The most common arity path pattern is `Three[dom selector][action][hiccup data]`.


```python
def add(text):
    t = {"id": uuid.uuid4().hex[:8], "text": text, "done": False}
    TODOS.append(t)
    return PlainTextResponse(";".join([
        Three[Selector("#add-form")][MORPH][add_form()],
        Three[Selector("#todo-list")][APPEND][todo_item(t)],
        Three[Selector("p.count")][MORPH][remaining_count()],
        f"console.log('todo added', {text})"
    ]))

def delete(todo_id):
    TODOS.remove(_find(todo_id))
    return PlainTextResponse(";".join([
        Two[Selector(f"#todo-{todo_id}")][REMOVE],
        Three[Selector("p.count")][MORPH][remaining_count()],
    ]))
```

These js snippets go directly into the browser's `eval` function, so you can do whatever you want.

```python
Two[Selector("#progress-bar")][EvalOn(f"=> $.width = '{width}%'")]
```
# Security

Verify snippet consumes the nonce, so for each GET you can only press each button once.
Verify snippet checks the HMAC against the provided snippet, restricting code running on the server to be only code that the server itself produces.
So if a user can't do an action, don't sign a snippet with that action for them.

`$placeholders` are rewritten to synthetic locals (`__ee_arg_N__`); form values are passed through `eval`'s locals mapping. Values are data, never source — a value containing `$x` cannot trigger further substitution or break out of a string literal.

Notice this line in the todo submit form handler:

```python
Three[Selector("#add-form")][MORPH][add_form()],
```

This is neceseary. Because each action is only allowed exactly once per GET. But you don't want to have to reGET the page to send another todo. So a new nonce is required to be generated by add_form(), which returns hiccup, which is rendered to an htmlstring, which is morphed into the dom at `#add-form`.

Each snippet is not only a continuation, but also a capability ticket.

`uv install evaleval`

# Durable state

`evaleval` also includes a Python-native path/reified-operation layer over
[rocksdict](https://github.com/rocksdict/RocksDict) — the same *paths as data*
idea as the Rust `durable` crate, idiomatic in Python.

Paths both **select** and **transform**. Selectors read only the addressed
key/prefix; transformations return plain-data operations applied atomically.

```python
from evaleval import (
    Record, Map, Leaf, Sum, List, Deque, Durability, RocksDb,
)

Store = Record(
    nodes=Map(Leaf()),
    scores=Map(Sum()),
    log=List(Leaf()),
)

db = RocksDb.open("scores.rocks")
root = Store.root()

db.apply([
    root.scores.key("alice").add(10),   # reified Add (RMW under single-writer lock)
    root.scores.key("alice").add(5),
    root.nodes.key("alice").set("Alice"),
], Durability.SYNC_WAL)

assert root.scores.key("alice").get(db) == 15
assert root.nodes.key("alice").get(db) == "Alice"
assert "alice" in root.nodes.keys(db)
```

## Schema markers

| Type | Meaning | Selectors | Transforms |
|------|---------|-----------|------------|
| `Leaf()` | one CBOR value | `get` | `set`, `delete` |
| `Map(V)` | CBOR keys → sub-schema | `key`, `keys`, `entries`, `iter`, paginated variants, `len`, `contains` | `clear`, leaf/sum `set`/`delete`/`add`, `transform_values` |
| `List(V)` | index-addressed sequence | `at`, `get`, `iter`, `len` | `push`, `pop`, `clear` |
| `Deque(V)` | double-ended queue | `front`, `back`, `iter`, `len` | `push_back`/`front`, `pop_front`/`back`, `clear` |
| `Sum()` | numeric accumulator | `get` (0 if absent) | `add`, `set`, `delete` |
| `Record(...)` | named fields (stable declaration-order ids) | field navigation | `clear` |

Nest freely. `Record` field ids come from declaration order — add new fields at
the end; reordering changes the layout.

## Cost model

- **Blind** (no read at construction): `Leaf.set`/`delete`, `Sum.add`/`set`/`delete`,
  collection `clear`. Ops are plain data (`Put` / `Delete` / `DeletePrefix` / `Add`).
- **Read-modify-write**: list/deque push and pop. Operations are interpreted in
  exact sequence against an in-batch view, then committed in one atomic write.
- **Scan**: `Map.keys` / `iter` / `len` / `contains` / `transform_values`.

For large maps, use bounded selectors rather than materializing the map:

```python
page = root.nodes.iter_page(db, limit=100)
while True:
    for key, value in page.items:
        process(key, value)
    if page.cursor is None:
        break
    page = root.nodes.iter_page(db, cursor=page.cursor, limit=100)
```

`keys_page`, `entries_page`, and `iter_page` return `Page(items, cursor)` in
encoded-key order. The cursor is opaque. Backends also expose
`scan_prefix(prefix, start=..., limit=...)`; its raw-key `start` is inclusive.

`rocksdict` has no custom merge operators, so `Add` is interpreted as
read-modify-write under the same single-writer lock and atomic batch. The
reified `Add` API is preserved so a native-merge backend can be swapped later.

## Durability

Every batch commits with an explicit policy:

- `Durability.SYNC_WAL` — WAL + fsync (survives power loss)
- `Durability.WAL_ONLY` — WAL without forced fsync
- `Durability.DISABLE_WAL` — skip WAL (rebuildable projections only)

## Single-writer

Not multi-process safe. One writer process; serialize writes at the application
layer. The rocksdict backend holds a process-local lock so concurrent threads
in the same process do not interleave batches.
