"""Convert provider HTML to bounded model input; retain raw content in storage."""
from html.parser import HTMLParser


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1
        elif tag in ('p', 'br', 'div', 'li'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ('p', 'div', 'li'):
            self.parts.append('\n')

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def article_text(item, limit=8000):
    parser = _Text()
    parser.feed(item.get('content') or '')
    text = ' '.join(''.join(parser.parts).split())
    return text[:limit], len(text) > limit


def model_item(item):
    content, truncated = article_text(item)
    return {'symbols': item['symbols'], 'headline': item['headline'],
            'summary': (item.get('summary') or '')[:1200],
            'source': item.get('source') or '', 'content': content,
            'content_available': bool(content), 'content_truncated': truncated}
