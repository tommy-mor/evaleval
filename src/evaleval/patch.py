from dataclasses import dataclass
from enum import Enum, auto
from itertools import count

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
class _Morph(Step): pass

@dataclass(frozen=True)
class _Prepend(Step): pass

@dataclass(frozen=True)
class _Append(Step): pass

@dataclass(frozen=True)
class _Outer(Step): pass

@dataclass(frozen=True)
class _Classes(Step): pass

@dataclass(frozen=True)
class _Add(Step): pass

@dataclass(frozen=True)
class _Toggle(Step): pass

@dataclass(frozen=True)
class _Remove(Step): pass


MORPH   = _Morph()
PREPEND = _Prepend()
APPEND  = _Append()
REMOVE  = _Remove()
OUTER   = _Outer()
CLASSES = _Classes()
ADD     = _Add()
TOGGLE  = _Toggle()


class State(Enum):
    START        = auto()
    SELECTED     = auto()
    EFFECT       = auto()
    CLASS_NAV    = auto()
    CLASS_EFFECT = auto()
    DONE         = auto()


_STEP_NAMES = {
    _Morph: "MORPH", _Prepend: "PREPEND", _Append: "APPEND", _Outer: "OUTER",
    _Classes: "CLASSES", _Add: "ADD", _Toggle: "TOGGLE", _Remove: "REMOVE",
}


def _name(step):
    return _STEP_NAMES.get(type(step), type(step).__name__)


def _transition(state, step):
    match state, step:
        case State.START, Selector():                                        return State.SELECTED
        case State.START, Eval():                                            return State.DONE

        case State.SELECTED, _Morph() | _Prepend() | _Append() | _Outer():   return State.EFFECT
        case State.SELECTED, _Classes():                                     return State.CLASS_NAV
        case State.SELECTED, _Remove():                                      return State.DONE
        case State.SELECTED, Eval():                                         return State.DONE

        case State.EFFECT, Hiccup() | Text():                                return State.DONE

        case State.CLASS_NAV, _Add() | _Remove() | _Toggle():                return State.CLASS_EFFECT
        case State.CLASS_EFFECT, Text():                                     return State.DONE

        case State.DONE, _:
            raise ValueError(f"chain is already complete")

        case State.START, _Remove():
            raise ValueError("REMOVE requires a Selector before it")
        case State.START, _Morph() | _Prepend() | _Append() | _Outer() | _Classes():
            raise ValueError(f"{_name(step)} requires a Selector before it")
        case State.START, _Add() | _Toggle():
            raise ValueError(f"{_name(step)} requires CLASSES before it")
        case State.START, Hiccup() | Text():
            raise ValueError("Data must follow an action")
        case State.SELECTED, _Add() | _Toggle():
            raise ValueError(f"{_name(step)} requires CLASSES before it")

        case _:
            raise ValueError(f"{_name(step)} cannot follow here")


def _coerce(item):
    match item:
        case Step():           return item
        case list() | tuple(): return Hiccup(item)
        case str():            return Text(item)
        case _:                raise TypeError(f"Cannot use {type(item).__name__} in patch chain")


def _normalize(raw_items):
    state = State.START
    steps = []
    for item in raw_items:
        step = _coerce(item)
        state = _transition(state, step)
        steps.append(step)
    return tuple(steps)


# --- codegen ---

_REF_COUNTER = count()


def _fresh():
    return f"_{next(_REF_COUNTER)}"


def _sel_js(query):
    safe = query.replace("\\", "\\\\").replace('"', '\\"')
    return f'document.querySelector("{safe}")'


def _tmpl(text):
    return text.replace("`", "\\`").replace("${", "\\${")


def _payload_html(step):
    match step:
        case Hiccup(data): return _tmpl(render(data))
        case Text(value):  return _tmpl(value)


def _lower_eval(code, ref):
    if code.startswith("=>"):
        return code[2:].strip().replace("$", ref)
    return code


def _compile(steps):
    match steps:
        case (Eval(code),):
            return _lower_eval(code, "null")

        case (Selector(q), Eval(code)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{_lower_eval(code, r)}"

        case (Selector(q), _Remove()):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}?.remove()"

        case (Selector(q), _Morph(), p) if isinstance(p, (Hiccup, Text)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\nIdiomorph.morph({r}, `{_payload_html(p)}`)"

        case (Selector(q), _Prepend(), p) if isinstance(p, (Hiccup, Text)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}.insertAdjacentHTML('afterbegin', `{_payload_html(p)}`)"

        case (Selector(q), _Append(), p) if isinstance(p, (Hiccup, Text)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}.insertAdjacentHTML('beforeend', `{_payload_html(p)}`)"

        case (Selector(q), _Outer(), p) if isinstance(p, (Hiccup, Text)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}.outerHTML = `{_payload_html(p)}`"

        case (Selector(q), _Classes(), _Add(), Text(v)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}?.classList.add(`{_tmpl(v)}`)"

        case (Selector(q), _Classes(), _Remove(), Text(v)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}?.classList.remove(`{_tmpl(v)}`)"

        case (Selector(q), _Classes(), _Toggle(), Text(v)):
            r = _fresh()
            return f"const {r} = {_sel_js(q)};\n{r}?.classList.toggle(`{_tmpl(v)}`)"

        case _:
            raise ValueError(f"Unsupported patch chain: {steps!r}")


# --- chain builder ---

class DepthChain:
    def __init__(self, depth, items=None):
        self.depth = depth
        self.items = tuple(items or ())

    def __getitem__(self, item):
        items = self.items + (item,)
        steps = _normalize(items)
        if len(items) >= self.depth:
            return _compile(steps)
        return DepthChain(self.depth, items)

    def __str__(self):
        return _compile(_normalize(self.items))


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
