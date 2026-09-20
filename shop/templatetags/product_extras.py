"""
Presentation-only helpers for the product detail page.

These read data that already exists on the Product/Review models and format
it for display — they add no database fields, no migrations, and change no
stored values.
"""
import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# The characters that have to be escaped before JSON can sit inside a <script>
# element. A product name is owner-entered text, so "</script>" in it would end
# the block early and spill the rest of the JSON into the page as markup.
# Escaping these three as \uXXXX leaves the JSON parsing identically while
# making it inert as HTML. (The backslashes below are literal — the same
# substitution Django's own json_script filter makes.)
_JSON_SCRIPT_ESCAPES = {
    ord('>'): '\\u003E',
    ord('<'): '\\u003C',
    ord('&'): '\\u0026',
}

# The shop writes a product's `spec` as one short line separated by '·' or
# '·'-style bullets, e.g. "Blue · Face ID · Battery 89%". Splitting on those
# separators lets the detail page show the same text as a scannable list
# without changing how the field is stored or edited.
SPEC_SEPARATORS = ('·', '•')


@register.filter
def spec_parts(value):
    """Split a product's single spec line into a list of parts."""
    text = (value or '').strip()
    if not text:
        return []
    for separator in SPEC_SEPARATORS:
        text = text.replace(separator, '\n')
    return [part.strip() for part in text.splitlines() if part.strip()]


@register.filter
def rating_breakdown(reviews):
    """Per-star counts for a set of reviews, 5 stars down to 1.

    Returns a list of {'stars', 'count', 'percent'} dicts so the template can
    draw a rating summary without any extra queries.
    """
    counts = {star: 0 for star in range(1, 6)}
    total = 0
    for review in reviews:
        try:
            rating = int(review.rating)
        except (TypeError, ValueError):
            continue
        if rating in counts:
            counts[rating] += 1
            total += 1
    return [
        {
            'stars': star,
            'count': counts[star],
            'percent': round(counts[star] * 100 / total) if total else 0,
        }
        for star in range(5, 0, -1)
    ]


@register.filter
def jsonld(value):
    """Serialise a dict for a <script type="application/ld+json"> block.

    Plain json.dumps is not safe here: a product name containing "</script>"
    would close the element early. The three characters that can do that are
    escaped, so the output is still valid JSON and no longer valid markup.
    """
    encoded = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return mark_safe(encoded.translate(_JSON_SCRIPT_ESCAPES))


@register.filter
def absolute_uri(url, request):
    """Resolve a static or media path against the request's host.

    Open Graph and Twitter require absolute image URLs. Both {% static %} and
    ImageField.url can hand back a path rather than a URL, and STATIC_URL is
    configured as 'static/' without a leading slash, so the value is
    normalised before being joined to the request's scheme and host.
    """
    url = (url or '').strip()
    if not url:
        return ''
    if url.startswith(('http://', 'https://', '//')):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return request.build_absolute_uri(url)
