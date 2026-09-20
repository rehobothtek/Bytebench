"""Tests for the page metadata work: canonical URLs, Open Graph/Twitter tags,
per-page descriptions, and JSON-LD structured data.

No network: every URL asserted here is built from the test client's own host,
and the structured-data blocks are parsed back out of the rendered page.

Run with:  python manage.py test shop.tests_seo_metadata
"""
import json
import re
from decimal import Decimal

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Product, Review

LD_JSON_BLOCK = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=10, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def jsonld_blocks(html):
    """Every application/ld+json block on the page, parsed back into dicts."""
    return [json.loads(match) for match in LD_JSON_BLOCK.findall(html)]


class MetaDescriptionTests(TestCase):
    """F-58 — pages describe themselves instead of sharing one blurb."""

    def test_the_homepage_keeps_the_site_wide_description(self):
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn('Phones, laptops, accessories and expert repairs', html)

    def test_the_shop_page_has_its_own_description(self):
        html = self.client.get(reverse('shop:shop')).content.decode()
        self.assertIn('Condition graded honestly', html)

    def test_contact_repairs_about_and_track_all_differ(self):
        pages = ['shop:contact', 'shop:repairs', 'shop:about', 'shop:track']
        descriptions = []
        for name in pages:
            html = self.client.get(reverse(name)).content.decode()
            match = re.search(r'<meta name="description" content="([^"]*)"', html)
            self.assertIsNotNone(match, f'{name} has no meta description')
            descriptions.append(match.group(1))

        self.assertEqual(len(set(descriptions)), len(pages),
                         'two of these pages share a meta description')

    def test_the_product_description_uses_the_products_own_fields(self):
        p = make_product(name='iPhone 13', spec='128GB · Blue')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        match = re.search(r'<meta name="description" content="([^"]*)"', html)
        self.assertIn('iPhone 13', match.group(1))
        self.assertIn('128GB · Blue', match.group(1))
        self.assertIn('₦100000', match.group(1))

    def test_every_description_is_a_single_attribute(self):
        # A quote or angle bracket in owner-entered text must not be able to
        # break out of the attribute.
        p = make_product(name='He said "cheap" <b>', spec='a "b" <c>')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        match = re.search(r'<meta name="description" content="([^"]*)"', html)
        self.assertIsNotNone(match)
        self.assertNotIn('<b>', match.group(1))


class CanonicalUrlTests(TestCase):
    """F-59 — canonical URLs, carefully, on query-driven pages."""

    def test_a_page_canonicalises_to_its_own_absolute_url(self):
        html = self.client.get(reverse('shop:about')).content.decode()
        self.assertIn('<link rel="canonical" href="http://testserver/about/">', html)

    def test_the_query_string_is_never_echoed_into_the_canonical(self):
        # ?category and ?q are filtered views of one listing. A canonical that
        # repeated them would point crawlers at a search-result URL, and would
        # give two visitors different "canonical" pages.
        for query in ('?category=laptop', '?q=charger', '?category=phone&q=a', '?page=2'):
            with self.subTest(query=query):
                html = self.client.get(reverse('shop:shop') + query).content.decode()
                self.assertIn('<link rel="canonical" href="http://testserver/shop/">', html)
                self.assertNotIn('canonical" href="http://testserver/shop/?', html)

    def test_a_configured_site_url_wins_over_the_request_host(self):
        with override_settings(SITE_URL='https://rehobothtek.com'):
            html = self.client.get(reverse('shop:about')).content.decode()
        self.assertIn('href="https://rehobothtek.com/about/"', html)

    def test_the_og_url_matches_the_canonical(self):
        html = self.client.get(reverse('shop:contact')).content.decode()
        self.assertIn('<meta property="og:url" content="http://testserver/contact/">', html)


class OpenGraphTests(TestCase):
    """F-60 — overridable OG/Twitter metadata."""

    def test_a_plain_page_gets_the_site_wide_defaults(self):
        html = self.client.get(reverse('shop:about')).content.decode()
        self.assertIn('<meta property="og:type" content="website">', html)
        self.assertIn(f'<meta property="og:site_name" content="{settings.SITE_NAME}">', html)
        self.assertIn('<meta name="twitter:card" content="summary_large_image">', html)
        self.assertIn('<meta property="og:title" content="About — RehoBothTek">', html)

    def test_a_product_page_declares_itself_as_a_product(self):
        p = make_product(name='iPhone 13')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertIn('<meta property="og:type" content="product">', html)
        self.assertIn('<meta property="og:title" content="iPhone 13 — RehoBothTek">', html)

    def test_a_product_with_a_photo_uses_the_photo(self):
        p = make_product(name='With Photo', image='products/hp-elitebook-840.jpg')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertIn(
            '<meta property="og:image" content="http://testserver/media/products/hp-elitebook-840.jpg"',
            html)

    def test_a_product_without_a_photo_falls_back_to_the_shop_logo(self):
        p = make_product(name='No Photo', image=None)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertIn(
            '<meta property="og:image" content="http://testserver/static/images/logo-mark-256.png"',
            html)

    def test_every_og_image_is_an_absolute_url(self):
        # A relative og:image is silently ignored by every scraper.
        p = make_product(name='No Photo', image=None)
        for url in (reverse('shop:home'), reverse('shop:product_detail', args=[p.id])):
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                image = re.search(r'<meta property="og:image" content="([^"]*)"', html).group(1)
                self.assertTrue(image.startswith('http://'), image)


class StructuredDataTests(TestCase):
    """F-61 — JSON-LD built only from data the project actually has."""

    def _business_block(self, html):
        return next(b for b in jsonld_blocks(html) if b['@type'] == 'LocalBusiness')

    def _product_block(self, html):
        return next(b for b in jsonld_blocks(html) if b['@type'] == 'Product')

    def test_the_business_block_comes_from_settings(self):
        html = self.client.get(reverse('shop:home')).content.decode()
        business = self._business_block(html)

        self.assertEqual(business['@context'], 'https://schema.org')
        self.assertEqual(business['name'], settings.SITE_NAME)
        self.assertEqual(business['address'], settings.SHOP_ADDRESS)
        self.assertEqual(business['email'], settings.SITE_EMAIL)
        self.assertTrue(business['telephone'].startswith('+'))
        self.assertEqual(business['url'], 'http://testserver/')

    def test_no_invented_ratings_or_price_ranges_are_published(self):
        # There is no real site-wide rating and no meaningful price range in
        # this project, so neither may appear in the markup.
        html = self.client.get(reverse('shop:home')).content.decode()
        business = self._business_block(html)
        self.assertNotIn('aggregateRating', business)
        self.assertNotIn('priceRange', business)
        self.assertNotIn('review', business)

    def test_a_product_page_publishes_the_products_real_data(self):
        p = make_product(name='iPhone 13', spec='128GB · Blue',
                         price=Decimal('250000'), stock_quantity=3)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        product = self._product_block(html)

        self.assertEqual(product['name'], 'iPhone 13')
        self.assertEqual(product['description'], '128GB · Blue')
        self.assertEqual(product['category'], 'Phone')
        self.assertEqual(product['url'], f'http://testserver/product/{p.id}/')
        self.assertEqual(product['offers']['price'], '250000.00')
        self.assertEqual(product['offers']['priceCurrency'], 'NGN')
        self.assertEqual(product['offers']['availability'], 'https://schema.org/InStock')

    def test_an_out_of_stock_product_is_marked_out_of_stock(self):
        p = make_product(name='Sold Out', stock_quantity=0)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertEqual(self._product_block(html)['offers']['availability'],
                         'https://schema.org/OutOfStock')

    def test_a_product_with_a_photo_publishes_its_absolute_url(self):
        p = make_product(image='products/hp.jpg')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertEqual(self._product_block(html)['image'],
                         'http://testserver/media/products/hp.jpg')

    def test_a_product_without_a_photo_omits_the_image_rather_than_faking_one(self):
        p = make_product(image=None)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertNotIn('image', self._product_block(html))

    def test_a_product_with_no_spec_omits_the_description(self):
        p = make_product(spec='')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertNotIn('description', self._product_block(html))

    def test_sample_reviews_are_not_published_as_a_rating(self):
        # The seeded reviews are marked sample content in the README, so their
        # numbers must not be handed to search engines as if they were real.
        p = make_product(name='Reviewed Phone')
        Review.objects.create(product=p, reviewer_name='Ada', rating=5,
                              body='Great', is_approved=True)
        Review.objects.create(product=p, reviewer_name='Bola', rating=4,
                              body='Good', is_approved=True)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        product = self._product_block(html)
        self.assertNotIn('aggregateRating', product)
        self.assertNotIn('review', product)

    def test_markup_in_a_product_name_cannot_escape_the_script_block(self):
        # Product names are owner-entered, so a name containing </script> has
        # to stay inert: the JSON must still parse back to the real text.
        hostile = '</script><script>alert(1)</script>'
        p = make_product(name=hostile)
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()

        self.assertNotIn(hostile, html)
        self.assertNotIn('<script>alert(1)', html)
        self.assertEqual(self._product_block(html)['name'], hostile)

    def test_the_structured_data_is_valid_json_on_every_page(self):
        p = make_product()
        pages = [reverse('shop:home'), reverse('shop:shop'), reverse('shop:about'),
                 reverse('shop:contact'), reverse('shop:repairs'), reverse('shop:track'),
                 reverse('shop:product_detail', args=[p.id])]
        for url in pages:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                blocks = jsonld_blocks(html)  # raises if any block is not JSON
                self.assertTrue(any(b['@type'] == 'LocalBusiness' for b in blocks))


class TemplateHygieneTests(TestCase):
    """Guards against the head being rendered with its own comments in it."""

    def test_no_page_ships_a_django_comment_tag_to_the_browser(self):
        # Django's {# ... #} is single-line only: a multi-line one is passed
        # through verbatim and lands in every page's <head>. The head is
        # invisible in a browser, so nothing else here would catch it.
        #
        # Only the opening delimiter is checked — a bare '{#' cannot occur in
        # this project's CSS or JavaScript, whereas '#}' could plausibly turn up
        # in a stylesheet.
        p = make_product()
        pages = [reverse('shop:home'), reverse('shop:shop'), reverse('shop:about'),
                 reverse('shop:contact'), reverse('shop:repairs'), reverse('shop:track'),
                 reverse('shop:product_detail', args=[p.id])]
        for url in pages:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                self.assertNotIn('{#', html)
