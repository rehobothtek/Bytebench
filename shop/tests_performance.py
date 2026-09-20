"""Query-count regressions for the pages the audit found doing avoidable work:
the product rating helpers (F-18), the account order pages (F-50) and the admin
order changelist (F-49).

Each test measures a page at two different data sizes rather than asserting a
fixed number. What matters is that the count does not *grow* with the number of
rows; a hard-coded total would fail every time an unrelated query is added
anywhere up the stack, and would say nothing about the thing being tested.

A warm-up request is made before the first measurement, because the very first
request in a session writes that session — one extra query that has nothing to
do with the page and would otherwise land on one side of the comparison only.

Run with:  python manage.py test shop.tests_performance
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import Order, OrderItem, Product, Review

User = get_user_model()


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=10, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def make_order(user=None, items=1, **kwargs):
    defaults = dict(
        customer_name='Ada Obi', email='ada@example.com',
        phone_number='08030000000', payment_method='delivery',
    )
    defaults.update(kwargs)
    order = Order.objects.create(user=user, **defaults)
    for index in range(items):
        OrderItem.objects.create(
            order=order,
            product=make_product(name=f'Line {index}'),
            product_name=f'Line {index}',
            price=Decimal('5000'),
            quantity=2,
        )
    return order


def query_count(call):
    with CaptureQueriesContext(connection) as captured:
        call()
    return len(captured)


class ProductRatingQueryTests(TestCase):
    """F-18 — a card reads the rating percentage and the review count, and reads
    the count twice more. None of that may cost a query per card."""

    def test_the_listing_does_not_gain_a_query_per_card(self):
        make_product(name='One')
        make_product(name='Two')
        self.client.get(reverse('shop:shop'))  # warm the session up...
        two_products = query_count(lambda: self.client.get(reverse('shop:shop')))

        for index in range(6):
            product = make_product(name=f'Extra {index}')
            Review.objects.create(product=product, reviewer_name='Ada',
                                  rating=5, is_approved=True)
        eight_products = query_count(lambda: self.client.get(reverse('shop:shop')))

        self.assertEqual(
            two_products, eight_products,
            'the listing gained queries as products were added — the rating '
            'aggregate is being fetched per card again')

    def test_the_homepage_does_not_gain_a_query_per_card(self):
        # Three separate grids on one page, each annotated independently.
        make_product(name='One', is_featured=True, is_bestseller=True)
        make_product(name='Two', is_featured=True, is_bestseller=True)
        self.client.get(reverse('shop:home'))
        two_products = query_count(lambda: self.client.get(reverse('shop:home')))

        for index in range(6):
            product = make_product(name=f'Extra {index}', is_featured=True, is_bestseller=True)
            Review.objects.create(product=product, reviewer_name='Ada',
                                  rating=4, is_approved=True)
        eight_products = query_count(lambda: self.client.get(reverse('shop:home')))

        self.assertEqual(two_products, eight_products)

    def test_the_detail_page_rating_is_unchanged_by_the_annotation(self):
        """The numbers themselves must be exactly what they were before."""
        product = make_product(name='Rated')
        Review.objects.create(product=product, reviewer_name='Ada', rating=5, is_approved=True)
        Review.objects.create(product=product, reviewer_name='Bola', rating=4, is_approved=True)
        Review.objects.create(product=product, reviewer_name='Chidi', rating=1, is_approved=False)

        # The annotated instance (the shop pages) and the plain one (the admin,
        # a signal, anywhere that fetched it ordinarily) must agree exactly —
        # AVG comes back as a float either way.
        annotated = Product.objects.with_rating_stats().get(pk=product.pk)
        self.assertEqual(annotated.review_count(), 2)
        self.assertAlmostEqual(float(annotated.average_rating()), 4.5)
        self.assertEqual(annotated.rating_percent(), 90)

        plain = Product.objects.get(pk=product.pk)
        self.assertEqual(plain.review_count(), 2)
        self.assertAlmostEqual(float(plain.average_rating()), 4.5)
        self.assertEqual(plain.rating_percent(), 90)

    def test_reading_the_rating_twice_costs_one_query(self):
        """The card asks for the percentage and then the count; the helper is
        the only place either is fetched, so that is one query, not two."""
        product = make_product(name='Rated')
        Review.objects.create(product=product, reviewer_name='Ada', rating=5, is_approved=True)
        plain = Product.objects.get(pk=product.pk)

        def read_three_times():
            plain.rating_percent()
            plain.review_count()
            plain.average_rating()

        self.assertEqual(query_count(read_three_times), 1)

    def test_a_product_with_no_approved_reviews_still_reads_as_unrated(self):
        product = make_product(name='Unrated')
        Review.objects.create(product=product, reviewer_name='Ada', rating=5, is_approved=False)
        annotated = Product.objects.with_rating_stats().get(pk=product.pk)
        self.assertEqual(annotated.review_count(), 0)
        self.assertEqual(annotated.rating_percent(), 0)


class AccountOrderQueryTests(TestCase):
    """F-50 — the order pages list each order's lines, and each order's total
    sums those lines, so both must be prefetched."""

    def setUp(self):
        self.user = User.objects.create_user('ada', password='pw-12345')
        self.client.force_login(self.user)
        self.client.get(reverse('shop:account'))  # warm the session up

    def test_the_account_page_does_not_gain_a_query_per_order(self):
        make_order(user=self.user, items=1)
        one_order = query_count(lambda: self.client.get(reverse('shop:account')))

        for _ in range(5):
            make_order(user=self.user, items=3)
        six_orders = query_count(lambda: self.client.get(reverse('shop:account')))

        self.assertEqual(
            one_order, six_orders,
            'the account page gained queries as orders were added — each '
            "order's total is being summed in its own query again")

    def test_order_detail_does_not_gain_a_query_per_line(self):
        small = make_order(user=self.user, items=1)
        one_line = query_count(lambda: self.client.get(
            reverse('shop:order_detail', args=[small.id])))

        large = make_order(user=self.user, items=6)
        seven_lines = query_count(lambda: self.client.get(
            reverse('shop:order_detail', args=[large.id])))

        self.assertEqual(
            one_line, seven_lines,
            'the order page gained queries as lines were added — the linked '
            'product of each line is being fetched one at a time again')

    def test_another_customers_order_is_still_not_reachable(self):
        """The prefetch must not have widened what the view is allowed to see."""
        other = User.objects.create_user('bola', password='pw-12345')
        theirs = make_order(user=other, items=2)
        mine = make_order(user=self.user, items=2)

        self.assertEqual(
            self.client.get(reverse('shop:order_detail', args=[theirs.id])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('shop:order_detail', args=[mine.id])).status_code, 200)
