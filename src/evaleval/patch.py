from dataclasses import dataclass

from evaleval.hiccup import render


class Step:
    pass


@dataclass(frozen=True)
class Selector(Step):
    query: str


@dataclass(frozen=True)
class Eval(Step):
    code: str


@dataclass(frozen=True)
class Hiccup(Step):
    data: list | tuple


@dataclass(frozen=True)
class Text(Step):
    value: str


@dataclass(frozen=True)
class _Morph(Step):
    pass


@dataclass(frozen=True)
class _Prepend(Step):
    pass


@dataclass(frozen=True)
class _Append(Step):
    pass


@dataclass(frozen=True)
class _Outer(Step):
    pass


@dataclass(frozen=True)
class _Classes(Step):
    pass


@dataclass(frozen=True)
class _Add(Step):
    pass


@dataclass(frozen=True)
class _Toggle(Step):
    pass


@dataclass(frozen=True)
class _Remove(Step):
    pass


@dataclass(frozen=True)
class _NodeRemove(Step):
    pass


@dataclass(frozen=True)
class _ClassRemove(Step):
    pass


MORPH = _Morph()
PREPEND = _Prepend()
APPEND = _Append()
REMOVE = _Remove()
OUTER = _Outer()
CLASSES = _Classes()
ADD = _Add()
TOGGLE = _Toggle()


def _step_name(step):
    match step:
        case Selector():
            return "Selector"
        case Eval():
            return "Eval"
        case Hiccup() | Text():
            return "data"
        case _Morph():
            return "MORPH"
        case _Prepend():
            return "PREPEND"
        case _Append():
            return "APPEND"
        case _Outer():
            return "OUTER"
        case _Classes():
            return "CLASSES"
        case _Add():
            return "ADD"
        case _Toggle():
            return "TOGGLE"
        case _Remove() | _NodeRemove() | _ClassRemove():
            return "REMOVE"
        case _:
            return type(step).__name__


def _coerce_step(item, previous):
    match item:
        case Step():
            if item is REMOVE:
                if isinstance(previous, _Classes):
                    return _ClassRemove()
                return _NodeRemove()
            return item
        case list() | tuple():
            return Hiccup(item)
        case str():
            return Text(item)
        case _:
            raise TypeError(f"Cannot use {type(item).__name__} in patch chain")


def _validate_transition(previous, current):
    match previous, current:
        case None, Selector() | Eval():
            return
        case None, _NodeRemove():
            raise ValueError("REMOVE requires a Selector before it")
        case None, _Morph() | _Prepend() | _Append() | _Outer() | _Classes():
            raise ValueError(f"{_step_name(current)} requires a Selector before it")
        case None, _Add() | _ClassRemove() | _Toggle():
            raise ValueError(f"{_step_name(current)} requires CLASSES before it")
        case None, Hiccup() | Text():
            raise ValueError("Data must follow an action")

        case Hiccup() | Text(), _:
            raise ValueError("Data must be the last item in the chain")
        case Eval() | _NodeRemove(), _:
            raise ValueError(f"{_step_name(previous)} must be the last item in the chain")

        case Selector(), _Morph() | _Prepend() | _Append() | _Outer() | _NodeRemove() | _Classes() | Eval():
            return
        case Selector(), _Add() | _ClassRemove() | _Toggle():
            raise ValueError(f"{_step_name(current)} requires CLASSES before it")
        case _Classes(), _Add() | _ClassRemove() | _Toggle():
            return
        case _Morph() | _Prepend() | _Append() | _Outer(), Hiccup() | Text():
            return
        case _Add() | _ClassRemove() | _Toggle(), Text():
            return

        case _:
            raise ValueError(f"{_step_name(current)} cannot follow {_step_name(previous)}")


def _selector_expr(query):
    safe = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'document.querySelector("{safe}")'


def _js_template_text(text):
    return text.replace("`", "\\`").replace("${", "\\${")


def _js_single_quote(text):
    return (
        text.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _render_payload(payload):
    match payload:
        case Hiccup(data):
            raw = render(data)
        case Text(value):
            raw = value
        case _:
            raise ValueError(f"Unsupported payload: {payload!r}")
    return _js_template_text(raw)


def _lower_eval(code, ref):
    if code.startswith("=>"):
        return code[2:].strip().replace("$", ref)
    return code


def _compile(steps):
    match steps:
        case (Eval(code),):
            return _lower_eval(code, "null")

        case (Selector(query), Eval(code)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                _lower_eval(code, "_0"),
            ])

        case (Selector(query), _NodeRemove()):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                "_0?.remove()",
            ])

        case (Selector(query), _Morph(), payload) if isinstance(payload, (Hiccup, Text)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"Idiomorph.morph(_0, `{_render_payload(payload)}`)",
            ])

        case (Selector(query), _Prepend(), payload) if isinstance(payload, (Hiccup, Text)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0.insertAdjacentHTML('afterbegin', `{_render_payload(payload)}`)",
            ])

        case (Selector(query), _Append(), payload) if isinstance(payload, (Hiccup, Text)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0.insertAdjacentHTML('beforeend', `{_render_payload(payload)}`)",
            ])

        case (Selector(query), _Outer(), payload) if isinstance(payload, (Hiccup, Text)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0.outerHTML = `{_render_payload(payload)}`",
            ])

        case (Selector(query), _Classes(), _Add(), Text(value)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0?.classList.add('{_js_single_quote(value)}')",
            ])

        case (Selector(query), _Classes(), _ClassRemove(), Text(value)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0?.classList.remove('{_js_single_quote(value)}')",
            ])

        case (Selector(query), _Classes(), _Toggle(), Text(value)):
            return ";\n".join([
                f"const _0 = {_selector_expr(query)}",
                f"_0?.classList.toggle('{_js_single_quote(value)}')",
            ])

        case _:
            return "console.warn('unresolved patch chain')"


class DepthChain:
    def __init__(self, depth, items=None):
        self.depth = depth
        self.items = tuple(items or ())

    def __getitem__(self, item):
        previous = self.items[-1] if self.items else None
        current = _coerce_step(item, previous)
        _validate_transition(previous, current)
        items = self.items + (current,)
        if len(items) >= self.depth:
            return _compile(items)
        return DepthChain(self.depth, items)

    def __str__(self):
        return _compile(self.items)


One   = DepthChain(1)
Two   = DepthChain(2)
Three = DepthChain(3)
Four  = DepthChain(4)
Five  = DepthChain(5)
Six   = DepthChain(6)
Seven = DepthChain(7)
Eight = DepthChain(8)
Nine  = DepthChain(9)
Ten   = DepthChain(10)
