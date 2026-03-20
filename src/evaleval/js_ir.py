import json
from dataclasses import dataclass


class Expr:
    pass


class Stmt:
    pass


@dataclass(frozen=True, slots=True)
class Id(Expr):
    name: str


@dataclass(frozen=True, slots=True)
class Str(Expr):
    value: str


@dataclass(frozen=True, slots=True)
class RawExpr(Expr):
    code: str


@dataclass(frozen=True, slots=True)
class Member(Expr):
    obj: Expr
    prop: str
    optional: bool = False


@dataclass(frozen=True, slots=True)
class Call(Expr):
    callee: Expr
    args: tuple[Expr, ...] = ()


@dataclass(frozen=True, slots=True)
class Assign(Expr):
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class And(Expr):
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Const(Stmt):
    name: str
    value: Expr


@dataclass(frozen=True, slots=True)
class ExprStmt(Stmt):
    expr: Expr


@dataclass(frozen=True, slots=True)
class If(Stmt):
    condition: Expr
    then: Stmt


@dataclass(frozen=True, slots=True)
class RawStmt(Stmt):
    code: str


@dataclass(frozen=True, slots=True)
class Program:
    statements: tuple[Stmt, ...]


def render_expr(expr: Expr) -> str:
    match expr:
        case Id(name):
            return name
        case Str(value):
            return json.dumps(value)
        case RawExpr(code):
            return code
        case Member(obj, prop, optional):
            op = "?." if optional else "."
            return f"{render_expr(obj)}{op}{prop}"
        case Call(callee, args):
            args_js = ", ".join(render_expr(arg) for arg in args)
            return f"{render_expr(callee)}({args_js})"
        case Assign(left, right):
            return f"{render_expr(left)} = {render_expr(right)}"
        case And(left, right):
            return f"{render_expr(left)} && {render_expr(right)}"
        case _:
            raise TypeError(f"Unsupported expression: {expr!r}")


def render_stmt(stmt: Stmt) -> str:
    match stmt:
        case Const(name, value):
            return f"const {name} = {render_expr(value)};"
        case ExprStmt(expr):
            return render_expr(expr)
        case If(condition, then):
            return f"if ({render_expr(condition)}) {render_stmt(then)}"
        case RawStmt(code):
            return code
        case _:
            raise TypeError(f"Unsupported statement: {stmt!r}")


def render_program(program: Program) -> str:
    return "\n".join(render_stmt(stmt) for stmt in program.statements)
