import re

import pytest

from evaleval.patch import (
    ADD,
    APPEND,
    CLASSES,
    Eval,
    Four,
    MORPH,
    OUTER,
    PREPEND,
    REMOVE,
    Selector,
    TOGGLE,
    Two,
    Three,
    One,
)


def assert_bound_ref(js, selector_expr, body_template):
    first, second = js.split("\n")
    match = re.fullmatch(r"const (_\d+) = (.+)", first)
    assert match is not None
    ref = match.group(1)
    assert match.group(2).removesuffix(";") == selector_expr
    assert second == body_template.format(ref=ref)


def test_selector_must_precede_actions():
    with pytest.raises(ValueError, match="REMOVE requires a Selector"):
        _ = One[REMOVE]


def test_action_requires_selector():
    with pytest.raises(ValueError, match="MORPH requires a Selector"):
        _ = Two[MORPH][["div"]]


def test_add_requires_classes_first():
    with pytest.raises(ValueError, match="ADD requires CLASSES before it"):
        _ = Three[Selector("#x")][ADD]["active"]


def test_data_must_be_last():
    with pytest.raises(ValueError, match="chain is already complete"):
        _ = Four[Selector("#x")][MORPH][["div"]][APPEND]


def test_morph_chain_renders_js():
    js = Three[Selector("#app")][MORPH][["div#app", "hello"]]
    assert_bound_ref(
        js,
        'document.querySelector("#app")',
        'Idiomorph.morph({ref}, `<div id="app">hello</div>`)',
    )


def test_selector_escaping_for_quotes_and_backslashes():
    js = Two[Selector('#a"b\\c')][REMOVE]
    assert_bound_ref(
        js,
        'document.querySelector("#a\\"b\\\\c")',
        "{ref}?.remove()",
    )


def test_eval_direct_code_passthrough():
    js = One[Eval("console.log('ok')")]
    assert js == "console.log('ok')"


def test_eval_arrow_substitutes_selector_var():
    js = Two[Selector("#root")][Eval("=> $.focus()")]
    assert_bound_ref(
        js,
        'document.querySelector("#root")',
        "{ref}.focus()",
    )


def test_eval_arrow_without_selector_substitutes_null():
    js = One[Eval("=> console.log($)")]
    assert js == "console.log(null)"


def test_classes_add_remove_toggle_emit_expected_js():
    add_js = Four[Selector("#item")][CLASSES][ADD]["on"]
    rem_js = Four[Selector("#item")][CLASSES][REMOVE]["on"]
    tog_js = Four[Selector("#item")][CLASSES][TOGGLE]["on"]

    assert_bound_ref(
        add_js,
        'document.querySelector("#item")',
        "{ref}?.classList.add(`on`)",
    )
    assert_bound_ref(
        rem_js,
        'document.querySelector("#item")',
        "{ref}?.classList.remove(`on`)",
    )
    assert_bound_ref(
        tog_js,
        'document.querySelector("#item")',
        "{ref}?.classList.toggle(`on`)",
    )


def test_append_prepend_outer_emit_expected_js():
    append_js  = Three[Selector("#list")][APPEND][["li", "x"]]
    prepend_js = Three[Selector("#list")][PREPEND][["li", "x"]]
    outer_js   = Three[Selector("#list")][OUTER][["ul#list", ["li", "x"]]]

    assert_bound_ref(
        append_js,
        'document.querySelector("#list")',
        "{ref}.insertAdjacentHTML('beforeend', `<li>x</li>`)",
    )
    assert_bound_ref(
        prepend_js,
        'document.querySelector("#list")',
        "{ref}.insertAdjacentHTML('afterbegin', `<li>x</li>`)",
    )
    assert_bound_ref(
        outer_js,
        'document.querySelector("#list")',
        "{ref}.outerHTML = `<ul id=\"list\"><li>x</li></ul>`",
    )


def test_eval_must_be_last():
    with pytest.raises(ValueError, match="chain is already complete"):
        _ = Three[Selector("#root")][Eval("=> $.focus()")][APPEND]


def test_class_name_escaping_uses_template_literal_js():
    js = Four[Selector("#item")][CLASSES][ADD]["it's\\ok"]
    assert_bound_ref(
        js,
        'document.querySelector("#item")',
        "{ref}?.classList.add(`it's\\ok`)",
    )


def test_selector_refs_are_globally_unique():
    first = Two[Selector("#a")][REMOVE]
    second = Two[Selector("#b")][REMOVE]

    first_match = re.match(r"const (_\d+) =", first)
    second_match = re.match(r"const (_\d+) =", second)

    assert first_match is not None
    assert second_match is not None
    assert int(second_match.group(1)[1:]) == int(first_match.group(1)[1:]) + 1
