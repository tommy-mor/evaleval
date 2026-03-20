from enum import Enum, auto
from itertools import count
from dataclasses import dataclass

from evaleval.hiccup import render
from evaleval.js_ir import (
    And,
    Assign,
    Call,
    Const,
    ExprStmt,
    Id,
    If,
    Member,
    Program,
    RawStmt,
    Str,
    render_program,
)


_REF_COUNTER = count()


class State(Enum):
    START = auto()
    SELECTED = auto()
    EFFECT = auto()
    CLASS_NAV = auto()
    CLASS_EFFECT = auto()
    DONE = auto()


class Step:
    NAME: str | None = None

    @classmethod
    def error_name(cls) -> str:
        return cls.NAME or cls.__name__


@dataclass(frozen=True, slots=True)
class Selector(Step):
    query: str


@dataclass(frozen=True, slots=True)
class Eval(Step):
    code: str


@dataclass(frozen=True, slots=True)
class EvalOn(Step):
    template: str


@dataclass(frozen=True, slots=True)
class Hiccup(Step):
    data: list | tuple


@dataclass(frozen=True, slots=True)
class Text(Step):
    value: str


@dataclass(frozen=True, slots=True)
class Morph(Step):
    NAME = "MORPH"


@dataclass(frozen=True, slots=True)
class Prepend(Step):
    NAME = "PREPEND"


@dataclass(frozen=True, slots=True)
class Append(Step):
    NAME = "APPEND"


@dataclass(frozen=True, slots=True)
class Outer(Step):
    NAME = "OUTER"


@dataclass(frozen=True, slots=True)
class Classes(Step):
    NAME = "CLASSES"


@dataclass(frozen=True, slots=True)
class Add(Step):
    NAME = "ADD"


@dataclass(frozen=True, slots=True)
class Toggle(Step):
    NAME = "TOGGLE"


@dataclass(frozen=True, slots=True)
class Remove(Step):
    NAME = "REMOVE"


MORPH = Morph()
PREPEND = Prepend()
APPEND = Append()
REMOVE = Remove()
OUTER = Outer()
CLASSES = Classes()
ADD = Add()
TOGGLE = Toggle()


StepType = Selector | Eval | EvalOn | Hiccup | Text | Morph | Prepend | Append | Outer | Classes | Add | Toggle | Remove


def _name(step: StepType) -> str:
    return type(step).error_name()


def _coerce(item) -> StepType:
    match item:
        case Step():
            return item
        case list() | tuple():
            return Hiccup(item)
        case str():
            return Text(item)
        case _:
            raise TypeError(f"Cannot use {type(item).__name__} in patch chain")


def _transition(state: State, step: StepType) -> State:
    match state, step:
        case State.START, Selector():
            return State.SELECTED
        case State.START, Eval():
            return State.DONE

        case State.SELECTED, Morph() | Prepend() | Append() | Outer():
            return State.EFFECT
        case State.SELECTED, Classes():
            return State.CLASS_NAV
        case State.SELECTED, Remove() | Eval() | EvalOn():
            return State.DONE

        case State.EFFECT, Hiccup() | Text():
            return State.DONE

        case State.CLASS_NAV, Add() | Remove() | Toggle():
            return State.CLASS_EFFECT
        case State.CLASS_EFFECT, Text():
            return State.DONE

        case State.DONE, _:
            raise ValueError("chain is already complete")

        case State.START, Remove():
            raise ValueError("REMOVE requires a Selector before it")
        case State.START, Morph() | Prepend() | Append() | Outer() | Classes():
            raise ValueError(f"{_name(step)} requires a Selector before it")
        case State.START, Add() | Toggle():
            raise ValueError(f"{_name(step)} requires CLASSES before it")
        case State.START, EvalOn():
            raise ValueError("EvalOn requires a Selector before it")
        case State.START, Hiccup() | Text():
            raise ValueError("Data must follow an action")
        case State.SELECTED, Add() | Toggle():
            raise ValueError(f"{_name(step)} requires CLASSES before it")

        case _:
            raise ValueError(f"{_name(step)} cannot follow here")


def _normalize(raw_items) -> tuple[StepType, ...]:
    state = State.START
    steps: list[StepType] = []
    for item in raw_items:
        step = _coerce(item)
        state = _transition(state, step)
        steps.append(step)
    return tuple(steps)


def _fresh_ref() -> str:
    return f"_{next(_REF_COUNTER)}"


def _selector_expr(query: str) -> Call:
    return Call(Member(Id("document"), "querySelector"), (Str(query),))


def _payload_html(step: StepType) -> str:
    match step:
        case Hiccup(data):
            return render(data)
        case Text(value):
            return value
        case _:
            raise TypeError(f"Expected HTML payload, got {_name(step)}")


def _format_bound_eval(template: str, ref: str) -> str:
    return template.format(sel=ref)


def _compile(steps: tuple[StepType, ...]) -> Program:
    match steps:
        case (Eval(code),):
            return Program((RawStmt(code),))

        case (Selector(query), Eval(code)):
            ref = _fresh_ref()
            return Program((Const(ref, _selector_expr(query)), RawStmt(code)))

        case (Selector(query), EvalOn(template)):
            ref = _fresh_ref()
            body = _format_bound_eval(template, ref)
            return Program((Const(ref, _selector_expr(query)), RawStmt(body)))

        case (Selector(query), Remove()):
            ref = _fresh_ref()
            stmt = ExprStmt(Call(Member(Id(ref), "remove", optional=True)))
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Morph(), Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _payload_html(payload)
            call = Call(Member(Id("Idiomorph"), "morph"), (Id(ref), Str(html)))
            stmt = ExprStmt(And(Id(ref), call))
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Prepend(), Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _payload_html(payload)
            call = Call(Member(Id(ref), "insertAdjacentHTML", optional=True), (Str("afterbegin"), Str(html)))
            stmt = ExprStmt(call)
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Append(), Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _payload_html(payload)
            call = Call(Member(Id(ref), "insertAdjacentHTML", optional=True), (Str("beforeend"), Str(html)))
            stmt = ExprStmt(call)
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Outer(), Hiccup() | Text() as payload):
            ref = _fresh_ref()
            html = _payload_html(payload)
            assign = ExprStmt(Assign(Member(Id(ref), "outerHTML"), Str(html)))
            stmt = If(Id(ref), assign)
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Classes(), Add(), Text(value)):
            ref = _fresh_ref()
            callee = Member(Member(Id(ref), "classList", optional=True), "add")
            stmt = ExprStmt(Call(callee, (Str(value),)))
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Classes(), Remove(), Text(value)):
            ref = _fresh_ref()
            callee = Member(Member(Id(ref), "classList", optional=True), "remove")
            stmt = ExprStmt(Call(callee, (Str(value),)))
            return Program((Const(ref, _selector_expr(query)), stmt))

        case (Selector(query), Classes(), Toggle(), Text(value)):
            ref = _fresh_ref()
            callee = Member(Member(Id(ref), "classList", optional=True), "toggle")
            stmt = ExprStmt(Call(callee, (Str(value),)))
            return Program((Const(ref, _selector_expr(query)), stmt))

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
            return render_program(_compile(steps))
        return DepthChain(self.depth, items)

    def __str__(self):
        return render_program(_compile(_normalize(self.items)))


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
