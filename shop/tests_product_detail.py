"""Regressions for the product detail page: what a customer sees, and what
the Add to cart button is allowed to do.

This is the page most of the shop's traffic lands on, and most of what follows
was written while reworking its layout — so each test pins down a property that
a future cosmetic change could quietly break:

  * The facts a buying decision needs are on the page as *text*: condition,
    stock wording and the real available quantity. None of it is signalled by
    colour alone, so it survives being read aloud or printed.
  * Out of stock hides every route to the cart — the quantity input, the
    sticky bar — rather than leaving a button that fails on submit.
  * The quantity input is capped at the stock that actually exists, and the
    cap is re-applied server-side (tested below), not just in the markup.
  * A long name or spec degrades to a long line instead of breaking the page.
  * Only approved reviews are public; the rating summary counts exactly the
    ones that are shown, and stars carry their rating as text for screen
    readers rather than as star glyphs alone.
  * The spec line is split into scannable parts, and an empty spec renders no
    empty list.

The cart tests here are deliberately about the *product page's* entry point.
`tests_accounts.py` covers the separate question of how a guest cart becomes an
account cart; this file only asserts that a plain Add to cart still posts the
quantity it was given and still refuses to oversell.

Run with:  python manage.py test shop.tests_product_detail
Uses an isolated in-memory test database; the dev db.sqlite3 is untouched.
"""
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from .models import Product, Review


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=5, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


class ProductDetailRenderTests(TestCase):
    def test_in_stock_product_shows_primary_information(self):
        p = make_product(name='iPhone 13, 128GB', condition='uk', stock_quantity=8)
        r = self.client.get(reverse('shop:product_detail', args=[p.id]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('iPhone 13, 128GB', html)
        self.assertIn('₦100000', html)            # floatformat:0 — the site never groups digits
        self.assertIn('UK-Used', html)                 # condition, as text not colour alone
        self.assertIn('In stock', html)                # stock, as text
        self.assertIn('8 available', html)             # real quantity from stock_quantity
        self.assertIn('Condition', html)
        self.assertIn('name="quantity"', html)
        self.assertIn('max="8"', html)                 # quantity can't exceed real stock
        self.assertIn('Add to cart', html)
        self.assertIn('Warranty', html)
        self.assertIn("What's in the box", html)
        self.assertIn('Delivery', html)
        self.assertIn('Returns', html)

    def test_out_of_stock_product_hides_add_to_cart(self):
        p = make_product(name='Sold Out Laptop', stock_quantity=0)
        r = self.client.get(reverse('shop:product_detail', args=[p.id]))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn('Out of stock', html)
        self.assertNotIn('name="quantity"', html)
        self.assertNotIn('id="stickyAddBar"', html)     # the element is stock-gated; the bare id stays in the always-on script
        self.assertIn('Ask us when it', html)

    def test_low_and_warn_stock_wording(self):
        last = make_product(name='Last One', stock_quantity=1)
        low = make_product(name='Few Left', stock_quantity=4)
        self.assertIn('Only 1 left', self.client.get(
            reverse('shop:product_detail', args=[last.id])).content.decode())
        self.assertIn('4 available', self.client.get(
            reverse('shop:product_detail', args=[low.id])).content.decode())

    def test_long_name_and_spec_do_not_break_the_page(self):
        p = make_product(name='A' * 200, spec='B' * 255)
        r = self.client.get(reverse('shop:product_detail', args=[p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn('A' * 200, r.content.decode())

    def test_empty_spec_renders_without_spec_list(self):
        p = make_product(spec='')
        r = self.client.get(reverse('shop:product_detail', args=[p.id]))
        self.assertEqual(r.status_code, 200)
        self.assertNotIn('spec-list', r.content.decode())

    def test_spec_is_split_into_scannable_parts(self):
        p = make_product(spec='Blue · Face ID · Battery 89%')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        for part in ('Blue', 'Face ID', 'Battery 89%'):
            self.assertIn(f'<li class="spec-item">{part}</li>', html)

    def test_sticky_bar_script_actually_renders(self):
        p = make_product()
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertIn('IntersectionObserver', html)
        self.assertIn('has-sticky-bar', html)

    def test_image_alt_text_and_emoji_fallback(self):
        p = make_product(name='With Photo', image='products/hp-elitebook-840.jpg')
        html = self.client.get(reverse('shop:product_detail', args=[p.id])).content.decode()
        self.assertIn('alt="With Photo"', html)
        p2 = make_product(name='No Photo')
        html2 = self.client.get(reverse('shop:product_detail', args=[p2.id])).content.decode()
        self.assertIn('aria-hidden="true"', html2)


class ProductDetailReviewTests(TestCase):
    def setUp(self):
        self.p = make_product(name='Reviewed Phone')

    def test_only_approved_reviews_show_and_broken_down(self):
        Review.objects.create(product=self.p, reviewer_name='Ada', rating=5,
                              body='Great', is_approved=True)
        Review.objects.create(product=self.p, reviewer_name='Bola', rating=3,
                              body='Okay', is_approved=True)
        Review.objects.create(product=self.p, reviewer_name='Hidden', rating=1,
                              body='Spam', is_approved=False)
        html = self.client.get(reverse('shop:product_detail', args=[self.p.id])).content.decode()
        self.assertIn('Ada', html)
        self.assertIn('Bola', html)
        self.assertNotIn('Hidden', html)
        self.assertIn('Rated 5 out of 5', html)        # screen-reader friendly stars
        self.assertIn('Based on 2 reviews', html)

    def test_no_reviews_state(self):
        html = self.client.get(reverse('shop:product_detail', args=[self.p.id])).content.decode()
        self.assertIn('No reviews yet for this product', html)
        self.assertIn('Write a review', html)

    def test_review_form_still_submits(self):
        r = self.client.post(reverse('shop:product_detail', args=[self.p.id]), {
            'reviewer_name': 'Chidi', 'rating': '4', 'title': 'Solid', 'body': 'Works well',
        })
        self.assertEqual(r.status_code, 302)
        review = Review.objects.get(reviewer_name='Chidi')
        self.assertEqual(review.product, self.p)
        self.assertFalse(review.is_approved)           # still gated behind approval


class AddToCartStillWorksTests(TestCase):
    def setUp(self):
        self.p = make_product(stock_quantity=5)

    def test_ajax_add_to_cart_uses_posted_quantity(self):
        r = self.client.post(reverse('shop:cart_add', args=[self.p.id]), {'quantity': '3'},
                             headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['cart_count'], 3)
        self.assertEqual(self.client.session['cart'][str(self.p.id)], 3)

    def test_non_ajax_add_to_cart_redirects(self):
        r = self.client.post(reverse('shop:cart_add', args=[self.p.id]), {'quantity': '2'},
                             HTTP_REFERER=reverse('shop:product_detail', args=[self.p.id]))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session['cart'][str(self.p.id)], 2)

    def test_quantity_is_capped_at_available_stock(self):
        r = self.client.post(reverse('shop:cart_add', args=[self.p.id]), {'quantity': '20'},
                             headers={'x-requested-with': 'XMLHttpRequest'})
        data = r.json()
        self.assertTrue(data['ok'])
        self.assertIn('warning', data)
        self.assertEqual(self.client.session['cart'][str(self.p.id)], 5)

    def test_out_of_stock_cannot_be_added(self):
        p = make_product(stock_quantity=0)
        r = self.client.post(reverse('shop:cart_add', args=[p.id]),
                             headers={'x-requested-with': 'XMLHttpRequest'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.json()['ok'])


class PaymentSurfaceUntouchedTests(TestCase):
    """A URL-surface guard, not a behavioural test.

    It asserts the payment and checkout routes still reverse after the product
    page's rework — the cheap check that nothing was renamed out from under
    them. It says nothing about what those pages render; `tests_launch_fixes.py`
    covers that.
    """

    def test_payment_urls_still_resolve(self):
        p = make_product()
        self.assertEqual(reverse('shop:payment', args=[1, '00000000-0000-0000-0000-000000000000']),
                         '/order/1/00000000-0000-0000-0000-000000000000/pay/')
        self.assertTrue(reverse('shop:verify_payment', args=[1, '00000000-0000-0000-0000-000000000000']))
        self.assertTrue(reverse('shop:checkout'))
        self.assertTrue(reverse('shop:cart_add', args=[p.id]))
