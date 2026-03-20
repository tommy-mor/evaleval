"""
poem — signed snippets, hiccup, and a dual-eval loop.

The entire client:

    import { Idiomorph } from 'idiomorph';
    window.Idiomorph = Idiomorph;
    const es = new EventSource('/sse');
    es.addEventListener('exec', e => eval(e.data));
    document.addEventListener('submit', async e => {
      e.preventDefault();
      const r = await fetch(e.target.action, {
        method: 'POST',
        body: new URLSearchParams(new FormData(e.target))
      });
      const t = await r.text();
      if (t) eval(t);
    });

Three endpoints. No framework.

    GET  *      # serve the shell
    GET  */sse  # push what you see
    POST /do    # verify, eval
"""

from poem.hiccup import render, RawContent, parse_tag
from poem.patch import (
    Selector, Eval, Action,
    MORPH, PREPEND, APPEND, REMOVE, OUTER, CLASSES, ADD, TOGGLE,
    DepthChain, One, Two, Three, Four, Five, Six, Seven, Eight, Nine, Ten,
)
from poem.signing import (
    Signer,
    scrub,
    apply_snippet_substitutions,
)
from poem.sse import exec_event, shell_html

__all__ = [
    # hiccup
    "render", "RawContent", "parse_tag",
    # patch
    "Selector", "Eval", "Action",
    "MORPH", "PREPEND", "APPEND", "REMOVE", "OUTER", "CLASSES", "ADD", "TOGGLE",
    "DepthChain", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten",
    # signing
    "Signer", "scrub", "apply_snippet_substitutions",
    # sse
    "exec_event", "shell_html",
]
