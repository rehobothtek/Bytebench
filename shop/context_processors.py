from django.conf import settings
from django.templatetags.static import static

from .cart import count_cart_items
from .session_lists import get_wishlist, get_compare

SITE_LOGO = 'images/logo-mark-256.png'


def _absolute(request, url):
    """Resolve a static/media path against the request's scheme and host."""
    if url.startswith(('http://', 'https://', '//')):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return request.build_absolute_uri(url)


def _canonical_url(request):
    """Absolute URL for the current page, without its query string.

    The query string is dropped deliberately. /shop/?category=laptop and
    /shop/?q=charger are filtered views of the same listing, and echoing those
    parameters into a canonical would either point crawlers at a search-result
    URL or hand every visitor a different "canonical" page. Pointing them all
    at the unfiltered listing never invents a URL that doesn't exist and never
    claims two genuinely different pages are one. A page that wants its own
    canonical can override the {% block canonical %} it renders into.
    """
    path = request.path
    if settings.SITE_URL:
        return f'{settings.SITE_URL}{path}'
    return request.build_absolute_uri(path)


def _site_jsonld(request):
    """schema.org description of the business, built only from configuration.

    Every field is a value the owner has already set in settings (or its
    environment override) — nothing is inferred. There is deliberately no
    aggregateRating or priceRange: the project has no real site-wide rating
    and no meaningful price range to report, and publishing invented ones is
    exactly what structured data must not do.
    """
    phone = (settings.WHATSAPP_NUMBER or '').strip()
    if phone and not phone.startswith('+'):
        phone = f'+{phone}'

    return {
        '@context': 'https://schema.org',
        '@type': 'LocalBusiness',
        'name': settings.SITE_NAME,
        'slogan': settings.SITE_TAGLINE,
        'url': settings.SITE_URL or request.build_absolute_uri('/'),
        'image': _absolute(request, static(SITE_LOGO)),
        'email': settings.SITE_EMAIL,
        'telephone': phone,
        'address': settings.SHOP_ADDRESS,
        # Mirrors the opening hours shown in the site footer.
        'openingHours': 'Mo-Sa 09:00-19:00',
    }


def site_info(request):
    return {
        'cart_count': count_cart_items(request),
        'wishlist_count': get_wishlist(request).count(),
        'compare_count': get_compare(request).count(),
        'WHATSAPP_NUMBER': settings.WHATSAPP_NUMBER,
        'SITE_NAME': settings.SITE_NAME,
        'SITE_TAGLINE': settings.SITE_TAGLINE,
        'SITE_EMAIL': settings.SITE_EMAIL,
        'SITE_URL': settings.SITE_URL,
        'TELEGRAM_USERNAME': settings.TELEGRAM_USERNAME,
        'SHOP_ADDRESS': settings.SHOP_ADDRESS,
        'canonical_url': _canonical_url(request),
        'default_og_image': _absolute(request, static(SITE_LOGO)),
        'site_jsonld': _site_jsonld(request),
    }
