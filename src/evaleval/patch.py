from dataclasses import dataclass
from enum import Enum, auto
from itertools import count
import json
import re

from evaleval.hiccup import render


_SLOT_PATTERN = re.compile(r"(?<![\w$])\$(?![\w$])")
_REF_COUNTER = count()


class State(Enum):
    START = auto()
    SELECTED = auto()
    EFFECT = auto()
    CLASS_NAV = auto()
    CLASS_EFFECT = auto()
    DONE = auto()


class Action(Enum):
    MORPH = "morph"
    PREPEND = "prepend"
    APPEND = "append"
    OUTER = "outer"
    CLASSES = "classes"
    ADD = "add"
    TOGGLE = "toggle"
    REMOVE = "remove"


@dataclass(frozen=True)
class Selector:
    query: str


@dataclass(frozen=True)
class Eval:
    code: str
    bind_selector: bool = False

    @classmethod
    def on_selected(cls, code: str) -> "Eval":
        return cls(code=code, bind_selector=True)


@dataclass(frozen=True)
class Hiccup:
    data: list | tuple


@dataclass(frozen=True)
class Text:
    value: str


MORPH = Action.MORPH
PREPEND = Action.PREPEND
APPEND = Action.APPEND
REMOVE = Action.REMOVE
OUTER = Action.OUTER
CLASSES = Action.CLASSES
ADD = Action.ADD
TOGGLE = Action.TOGGLE


Step = Selector | Eval | Hiccup | Text | Action


def _step_name(step: Step) -> str:
    match step:
        case Action() as action:
            return action.name
        case _:
            return type(step).__name__


def _coerce(item) -> Step:
    match item:
        case Selector() | Eval() | Hiccup() | Text() | Action():
            return item
        case list() | tuple():
            return Hiccup(item)
        case str():
            return Text(item)
        case _:
            raise TypeError(f"Cannot use {type(item).__name__} in patch chain")


def _transition(state: State, step: Step) -> State:
    match state, step:
        case State.START, Selector():
            return State.SELECTED
        case State.START, Eval():
            return State.DONE

        case State.SELECTED, Action.MORPH | Action.PREPEND | Action.APPEND | Action.OUTER:
            return State.EFFECT
        case State.SELECTED, Action.CLASSES:
            return State.CLASS_NAV
        case State.SELECTED, Action.REMOVE | Eval():
            return State.DONE

        case State.EFFECT, Hiccup() | Text():
            return State.DONE

        case State.CLASS_NAV, Action.ADD | Action.REMOVE | Action.TOGGLE:
            return State.CLASS_EFFECT
        case State.CLASS_EFFECT, Text():
            return State.DONE

        case State.DONE, _:
            raise ValueError("chain is already complete")

        case State.START, Action.REMOVE:
            raise ValueError("REMOVE requires a Selector before it")
        case State.START, Action.MORPH | Action.PREPEND | Action.APPEND | Action.OUTER | Action.CLASSES:
            raise ValueError(f"{_step_name(step)} requires a Selector before it")
        case State.START, Action.ADD | Action.TOGGLE:
            raise ValueError(f"{_step_name(step)} requires CLASSES before it")
        case State.START, Hiccup() | Text():
            raise ValueError("Data must follow an action")
        case State.SELECTED, Action.ADD | Action.TOGGLE:
            raise ValueError(f"{_step_name(step)} requires CLASSES before it")

        case _:
            raise ValueError(f"{_step_name(step)} cannot follow here")


def _normalize(raw_items) -> tuple[Step, ...]:
    state = State.START
    steps: list[Step] = []
    for item in raw_items:
        step = _coerce(item)
        state = _transition(state, step)
        steps.append(step)
    return tuple(steps)


def _fresh_ref() -> str:
    return f"_{next(_REF_COUNTER)}"


def _js_string(value: str) -> str:
    return json.dumps(value)


def _selector_expr(query: str) -> str:
    return f"document.querySelector({_js_string(query)})"


def _payload_html(step: Step) -> str:
    match step:
        case Hiccup(data):
            return render(data)
        case Text(value):
            return value
        case _:
            raise TypeError(f"Expected HTML payload, got {_step_name(step)}")


def _bound_eval(code: str, ref: str) -> str:
    return _SLOT_PATTERN.sub(ref, code)


def _compile(steps: tuple[Step, ...]) -> str:
    match steps:
        case (Eval(code, bind_selector=False),):
            return code

        case (Eval(code, bind_selector=True),):
            return _bound_eval(code, "null")

        case (Selector(query), Eval(code, bind_selector)):
            ref = _fresh_ref()
            body = _bound_eval(code, ref) if bind_selector else code
            return f"const {ref} = {_selector_expr(query)};\n{body}"

        case (Selector(query), Action.REMOVE):
            ref = _fresh_ref()
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.remove()"

        case (Selector(query), Action.MORPH, Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _js_string(_payload_html(payload))
            return f"const {ref} = {_selector_expr(query)};\n{ref} && Idiomorph.morph({ref}, {html})"

        case (Selector(query), Action.PREPEND, Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _js_string(_payload_html(payload))
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.insertAdjacentHTML(\"afterbegin\", {html})"

        case (Selector(query), Action.APPEND, Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _js_string(_payload_html(payload))
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.insertAdjacentHTML(\"beforeend\", {html})"

        case (Selector(query), Action.OUTER, Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _js_string(_payload_html(payload))
            return f"const {ref} = {_selector_expr(query)};\nif ({ref}) {ref}.outerHTML = {html}"

        case (Selector(query), Action.CLASSES, Action.ADD, Text(value)):
            ref = _fresh_ref()
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.classList.add({_js_string(value)})"

        case (Selector(query), Action.CLASSES, Action.REMOVE, Text(value)):
            ref = _fresh_ref()
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.classList.remove({_js_string(value)})"

        case (Selector(query), Action.CLASSES, Action.TOGGLE, Text(value)):
            ref = _fresh_ref()
            return f"const {ref} = {_selector_expr(query)};\n{ref}?.classList.toggle({_js_string(value)})"

        case _:
            raise ValueError(f"Unsupported patch chain: {steps!r}")


class DepthChain:
    def __init__(self, depth: int, items=None):
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
