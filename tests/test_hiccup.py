from evaleval.hiccup import RawContent, parse_tag, render, render_attrs


def test_parse_tag_extracts_tag_id_classes():
    tag, ident, classes = parse_tag("button.primary.large#save")
    assert tag == "button"
    assert ident == "save"
    assert classes == ["primary", "large"]


def test_parse_tag_defaults_to_div_when_empty_tag():
    tag, ident, classes = parse_tag(".a.b#x")
    assert tag == "div"
    assert ident == "x"
    assert classes == ["a", "b"]


def test_render_attrs_merges_and_escapes():
    attrs = render_attrs(
        {"class": "external", "data-k": 'a"b', "id": "ignored"},
        id_val="real-id",
        classes=["one", "two"],
    )
    assert attrs == ' id="real-id" class="one two external" data-k="a&quot;b"'


def test_render_handles_rawcontent_without_escape():
    assert render(RawContent("<b>safe</b>")) == "<b>safe</b>"


def test_render_escapes_text_nodes():
    assert render(["p", "<unsafe>"]) == "<p>&lt;unsafe&gt;</p>"


def test_render_void_element_self_closes():
    assert render(["input", {"type": "text", "value": "x"}]) == '<input type="text" value="x" />'


def test_render_flattens_nested_children_lists():
    data = ["ul", [["li", "a"], ["li", "b"]]]
    assert render(data) == "<ul><li>a</li><li>b</li></ul>"


def test_render_returns_empty_for_invalid_values():
    assert render(123) == ""
    assert render([]) == ""
    assert render([42, "x"]) == ""
