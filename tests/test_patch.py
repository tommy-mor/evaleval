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
    with pytest.raises(ValueError, match="REMOVE requires a Selector"):
        _ = One[REMOVE]


def test_action_requires_selector():
    with pytest.raises(ValueError, match="MORPH requires a Selector"):
        _ = Two[MORPH][["div"]]


def test_add_requires_classes_first():
    with pytest.raises(ValueError, match="ADD requires CLASSES before it"):
        _ = Three[Selector("#x")][ADD]["active"]


def test_data_must_be_last():
    with pytest.raises(ValueError, match="Data must be the last item"):
        _ = Four[Selector("#x")][MORPH][["div"]][APPEND]


def test_morph_chain_renders_js():
    js = Three[Selector("#app")][MORPH][["div#app", "hello"]]
    assert js == 'const _0 = document.querySelector("#app");\nIdiomorph.morph(_0, `<div id="app">hello</div>`)'


def test_selector_escaping_for_quotes_and_backslashes():
    js = Two[Selector('#a"b\\c')][REMOVE]
    assert js == 'const _0 = document.querySelector("#a\\"b\\\\c");\n_0?.remove()'


def test_eval_direct_code_passthrough():
    js = One[Eval("console.log('ok')")]
    assert js == "console.log('ok')"


def test_eval_arrow_substitutes_selector_var():
    js = Two[Selector("#root")][Eval("=> $.focus()")]
    assert js == 'const _0 = document.querySelector("#root");\n_0.focus()'


def test_eval_arrow_without_selector_substitutes_null():
    js = One[Eval("=> console.log($)")]
    assert js == "console.log(null)"


def test_classes_add_remove_toggle_emit_expected_js():
    add_js = Four[Selector("#item")][CLASSES][ADD]["on"]
    rem_js = Four[Selector("#item")][CLASSES][REMOVE]["on"]
    tog_js = Four[Selector("#item")][CLASSES][TOGGLE]["on"]

    assert add_js == "const _0 = document.querySelector(\"#item\");\n_0?.classList.add('on')"
    assert rem_js == "const _0 = document.querySelector(\"#item\");\n_0?.classList.remove('on')"
    assert tog_js == "const _0 = document.querySelector(\"#item\");\n_0?.classList.toggle('on')"


def test_append_prepend_outer_emit_expected_js():
    append_js  = Three[Selector("#list")][APPEND][["li", "x"]]
    prepend_js = Three[Selector("#list")][PREPEND][["li", "x"]]
    outer_js   = Three[Selector("#list")][OUTER][["ul#list", ["li", "x"]]]

    assert append_js  == "const _0 = document.querySelector(\"#list\");\n_0.insertAdjacentHTML('beforeend', `<li>x</li>`)"
    assert prepend_js == "const _0 = document.querySelector(\"#list\");\n_0.insertAdjacentHTML('afterbegin', `<li>x</li>`)"
    assert outer_js   == 'const _0 = document.querySelector("#list");\n_0.outerHTML = `<ul id="list"><li>x</li></ul>`'


def test_eval_must_be_last():
    with pytest.raises(ValueError, match="Eval must be the last item"):
        _ = Three[Selector("#root")][Eval("=> $.focus()")][APPEND]


def test_class_name_escaping_uses_single_quoted_js():
    js = Four[Selector("#item")][CLASSES][ADD]["it's\\ok"]
    assert js == "const _0 = document.querySelector(\"#item\");\n_0?.classList.add('it\\'s\\\\ok')"
