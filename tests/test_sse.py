from evaleval.sse import exec_event, shell_html


def test_exec_event_wraps_single_string():
    out = exec_event("console.log('x')")
    assert out == "event: exec\ndata: console.log('x')\n\n"


def test_exec_event_joins_multiple_statements():
    out = exec_event(["a()", "b()"])
    assert out == "event: exec\ndata: a();b()\n\n"


def test_shell_html_defaults_include_fixed_sse_path():
    html = shell_html()
    assert "https://unpkg.com/idiomorph@0.3.0/dist/idiomorph.esm.js" in html
    assert "new EventSource('/sse')" in html
    assert "__IDIOMORPH_URL__" not in html
    assert "__SSE_PATH__" not in html


def test_shell_html_applies_custom_sse_and_idiomorph():
    html = shell_html(sse_path="/custom/sse", idiomorph_url="https://cdn.example/idiomorph.js")
    assert "import { Idiomorph } from 'https://cdn.example/idiomorph.js';" in html
    assert "new EventSource('/custom/sse')" in html
