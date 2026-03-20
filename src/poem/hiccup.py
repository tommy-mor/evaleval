from html import escape


VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr'
}


class RawContent:
    """Wrapper for content that should not be HTML-escaped.
    Use with caution - only for trusted content like inline scripts/styles."""
    def __init__(self, content):
        self.content = content


def parse_tag(tag_str):
    """Parse 'div.class1.class2#id' into tag, id, classes."""
    parts = tag_str.split('#')
    tag_and_classes = parts[0]
    id_val = parts[1].split('.')[0] if len(parts) > 1 else None

    class_parts = tag_and_classes.split('.')
    tag = class_parts[0] or 'div'
    classes = class_parts[1:] + (parts[1].split('.')[1:] if len(parts) > 1 else [])

    return tag, id_val, classes


def render_attrs(attrs, id_val, classes):
    """Render HTML attributes."""
    parts = []

    if id_val:
        parts.append(f'id="{escape(id_val)}"')
    elif 'id' in attrs:
        parts.append(f'id="{escape(str(attrs["id"]))}"')

    all_classes = classes[:]
    if 'class' in attrs:
        all_classes.append(attrs['class'])
    if all_classes:
        parts.append(f'class="{escape(" ".join(all_classes))}"')

    for key, val in attrs.items():
        if key not in ('id', 'class'):
            parts.append(f'{escape(key)}="{escape(str(val))}"')

    return ' ' + ' '.join(parts) if parts else ''


def render(data, parent_tag=None):
    """Render hiccup data structure to HTML string."""
    if isinstance(data, RawContent):
        return data.content

    if isinstance(data, str):
        return escape(data)

    if not isinstance(data, (list, tuple)):
        return ''

    if len(data) == 0:
        return ''

    tag_str = data[0]

    if not isinstance(tag_str, str):
        return ''

    tag, id_val, classes = parse_tag(tag_str)

    attrs = {}
    children_start = 1

    if len(data) > 1 and isinstance(data[1], dict):
        attrs = data[1]
        children_start = 2

    children = data[children_start:]

    flattened = []
    for child in children:
        if isinstance(child, list) and len(child) > 0 and isinstance(child[0], list):
            flattened.extend(child)
        else:
            flattened.append(child)

    children_html = ''.join(render(child, tag) for child in flattened)

    attrs_str = render_attrs(attrs, id_val, classes)

    if tag in VOID_ELEMENTS:
        return f'<{tag}{attrs_str} />'
    else:
        return f'<{tag}{attrs_str}>{children_html}</{tag}>'
