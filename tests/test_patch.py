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


def test_selector_must_precede_actions():
    with pytest.raises(ValueError, match="Selector must come before actions"):
        _ = Two[REMOVE][Selector("#app")]


def test_action_requires_selector():
    with pytest.raises(ValueError, match="MORPH requires a Selector"):
        _ = Two[MORPH][["div"]]


def test_add_requires_classes_first():
    with pytest.raises(ValueError, match="ADD requires CLASSES before it"):
        _ = Three[Selector("#x")][ADD]["active"]


def test_data_must_be_last():
    with pytest.raises(ValueError, match="Data must be the last item"):
        _ = Three[Selector("#x")][{"bad": "data"}][MORPH]


def test_morph_chain_renders_js():
    js = Three[Selector("#app")][MORPH][["div#app", "hello"]]
    assert js == 'Idiomorph.morph(document.querySelector("#app"), `<div id="app">hello</div>`)' 


def test_selector_escaping_for_quotes_and_backslashes():
    js = Two[Selector('#a"b\\c')][REMOVE]
    assert js == 'document.querySelector("#a\\"b\\\\c")?.remove()'


def test_eval_direct_code_passthrough():
    js = One[Eval("console.log('ok')")]
    assert js == "console.log('ok')"


def test_eval_arrow_expands_with_selector_argument():
    js = Two[Selector("#root")][Eval("=> $.focus()")]
    assert js == '(($) => { $.focus() })(document.querySelector("#root"))'


def test_eval_arrow_without_selector_uses_null():
    js = One[Eval("=> console.log($)")]
    assert js == "(($) => { console.log($) })(null)"


def test_classes_add_remove_toggle_emit_expected_js():
    add_js = Four[Selector("#item")][CLASSES][ADD]["on"]
    rem_js = Four[Selector("#item")][CLASSES][REMOVE]["on"]
    tog_js = Four[Selector("#item")][CLASSES][TOGGLE]["on"]

    assert add_js == "document.querySelector(\"#item\")?.classList.add('on')"
    assert rem_js == "document.querySelector(\"#item\")?.classList.remove('on')"
    assert tog_js == "document.querySelector(\"#item\")?.classList.toggle('on')"


def test_append_prepend_outer_emit_expected_js():
    append_js = Three[Selector("#list")][APPEND][["li", "x"]]
    prepend_js = Three[Selector("#list")][PREPEND][["li", "x"]]
    outer_js = Three[Selector("#list")][OUTER][["ul#list", ["li", "x"]]]

    assert append_js == "document.querySelector(\"#list\").insertAdjacentHTML('beforeend', `<li>x</li>`)"
    assert prepend_js == "document.querySelector(\"#list\").insertAdjacentHTML('afterbegin', `<li>x</li>`)"
    assert outer_js == 'document.querySelector("#list").outerHTML = `<ul id="list"><li>x</li></ul>`'

