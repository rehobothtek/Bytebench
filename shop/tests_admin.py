"""Admin regressions for the admin implementation pass.

Covers the two named findings — F-49 (the order changelist deriving each row's
total in its own query) and F-54 (a payment-status change made from the
changelist leaving no record of who changed it or what it was) — plus the
standing safety properties the pass had to preserve: guest orders still
reachable, order lines still not editable, only approved reviews public, and no
credential anywhere in an admin response.

Run with:  python manage.py test shop.tests_admin
"""
from decimal import Decimal

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import (
    CartItem, ContactMessage, CustomerProfile, FAQ, Order, OrderItem, Product,
    RepairBooking, RepairType, Review, Testimonial,
)

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


class AdminPageTests(TestCase):
    """One of every shop object, then every admin page that shows it."""

    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.customer = User.objects.create_user('ada', password='pw-12345')

        self.product = make_product()
        self.order = make_order(user=self.customer, items=2)
        self.order_line = self.order.items.first()
        self.review = Review.objects.create(product=self.product, reviewer_name='Ada Obi', rating=5)
        self.testimonial = Testimonial.objects.create(customer_name='Ada Obi', quote='Good.')
        self.faq = FAQ.objects.create(question='Do you deliver?', answer='Yes.')
        self.repair_type = RepairType.objects.create(
            name='Screen replacement', description='Glass only',
            price_range='₦25,000', time_estimate='2 hrs')
        self.booking = RepairBooking.objects.create(
            customer_name='Ada Obi', phone_number='08030000000',
            device='iPhone 11', repair_type=self.repair_type)
        self.profile = CustomerProfile.objects.create(user=self.customer)
        self.cart_item = CartItem.objects.create(
            user=self.customer, product=self.product, quantity=2)
        self.message = ContactMessage.objects.create(
            name='Ada Obi', message='Do you have chargers in stock?')

        self.request = RequestFactory().get('/')
        self.request.user = self.staff
        self.client.force_login(self.staff)

    def admin_pages(self):
        """The index, every changelist, and the change page of every object
        above — minus any model whose admin deliberately refuses edits, which
        is covered by its own test."""
        pages = [reverse('admin:index')]
        for model in admin.site._registry:
            meta = model._meta
            pages.append(reverse(f'admin:{meta.app_label}_{meta.model_name}_changelist'))
        for obj in (self.product, self.order, self.review, self.testimonial, self.faq,
                    self.repair_type, self.booking, self.profile, self.cart_item,
                    self.message):
            meta = obj._meta
            if not admin.site._registry[meta.model].has_change_permission(self.request):
                continue
            pages.append(reverse(f'admin:{meta.app_label}_{meta.model_name}_change',
                                 args=[obj.pk]))
        return pages

    def test_every_admin_page_loads(self):
        for url in self.admin_pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_every_admin_add_page_loads(self):
        for model, model_admin in admin.site._registry.items():
            if not model_admin.has_add_permission(self.request):
                continue
            meta = model._meta
            url = reverse(f'admin:{meta.app_label}_{meta.model_name}_add')
            with self.subTest(model=meta.label):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_no_admin_page_falls_back_to_a_debug_page(self):
        """A 500 rendered by Django's technical page would carry the settings
        module, the traceback and the local variables. None of that belongs in
        front of an administrator, so none of it may appear."""
        for url in self.admin_pages():
            with self.subTest(url=url):
                body = self.client.get(url).content.decode()
                self.assertNotIn('Traceback', body)
                self.assertNotIn('DJANGO_SETTINGS_MODULE', body)
                self.assertNotIn(settings.SECRET_KEY, body)

    def test_no_admin_page_exposes_the_paystack_secret_key(self):
        # The public key is meant to be public; the secret key never is.
        secret = settings.PAYSTACK_SECRET_KEY
        self.assertTrue(secret, 'no Paystack secret key is configured to test with')
        for url in self.admin_pages():
            with self.subTest(url=url):
                self.assertNotIn(secret, self.client.get(url).content.decode())

    def test_no_admin_page_exposes_an_account_password_hash(self):
        password_hash = self.customer.password
        self.assertTrue(password_hash, 'the test user has no usable password')
        for url in self.admin_pages():
            with self.subTest(url=url):
                self.assertNotIn(password_hash, self.client.get(url).content.decode())


class OrderChangelistQueryTests(TestCase):
    """F-49 — the Total column is derived from the order's lines, so without a
    prefetch the changelist ran one extra query per row."""

    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.customer = User.objects.create_user('ada', password='pw-12345')
        self.client.force_login(self.staff)
        self.url = reverse('admin:shop_order_changelist')
        self.client.get(self.url)  # warm the session and the admin's own caches

    def test_the_changelist_does_not_gain_a_query_per_order(self):
        make_order(user=self.customer, items=1)
        one_order = query_count(lambda: self.client.get(self.url))

        for _ in range(5):
            make_order(user=self.customer, items=3)
        six_orders = query_count(lambda: self.client.get(self.url))

        self.assertEqual(
            one_order, six_orders,
            'the order changelist gained queries as orders were added — each '
            "row's total is being summed in its own query again")


class OrderAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.model_admin = admin.site._registry[Order]
        self.request = RequestFactory().get('/')
        self.request.user = self.staff

    def test_the_total_column_still_comes_from_the_order_lines(self):
        """The optimisation must not have changed the arithmetic."""
        order = make_order(items=2)  # 2 lines x 2 x ₦5,000
        self.assertEqual(order.total(), Decimal('20000'))
        self.assertEqual(self.model_admin.total_display(order), '₦20000')

    def test_an_order_with_no_lines_totals_zero(self):
        self.assertEqual(Order.objects.create(customer_name='Ada').total(), 0)

    def test_status_and_payment_status_are_still_editable_from_the_list(self):
        """F-54 kept the convenience and added the record; it did not remove it."""
        for field in ('status', 'payment_status'):
            self.assertIn(field, self.model_admin.list_editable)
            self.assertIn(field, self.model_admin.list_display)

    def test_a_status_change_is_logged_with_who_and_what_it_was(self):
        order = make_order()
        edited = Order.objects.get(pk=order.pk)
        edited.status = 'confirmed'
        with self.assertLogs('shop.admin', level='INFO') as captured:
            self.model_admin.save_model(self.request, edited, form=None, change=True)
        line = '\n'.join(captured.output)
        self.assertIn('new -> confirmed', line)
        self.assertIn(self.staff.get_username(), line)

    def test_the_log_line_carries_no_customer_detail_and_no_token(self):
        """The audit trail names the order and the two states — nothing else.

        A reference is set here rather than left blank, so that "the reference
        is not logged" is checked against something that genuinely could be.
        """
        order = make_order(payment_reference='PSK-REF-778812')
        edited = Order.objects.get(pk=order.pk)
        edited.payment_status = 'paid'
        with self.assertLogs('shop.admin', level='INFO') as captured:
            self.model_admin.save_model(self.request, edited, form=None, change=True)
        line = '\n'.join(captured.output)
        self.assertIn('unpaid -> paid', line)
        for detail in (order.customer_name, order.phone_number, order.email,
                       str(order.access_token), order.payment_reference):
            self.assertTrue(detail, 'nothing to check — the fixture is blank')
            self.assertNotIn(detail, line)

    def test_saving_without_changing_a_state_logs_nothing(self):
        order = make_order()
        unchanged = Order.objects.get(pk=order.pk)
        with self.assertNoLogs('shop.admin', level='INFO'):
            self.model_admin.save_model(self.request, unchanged, form=None, change=True)

    def test_a_new_order_is_not_logged_as_a_change(self):
        fresh = Order.objects.create(customer_name='Ada')
        with self.assertNoLogs('shop.admin', level='INFO'):
            self.model_admin.save_model(self.request, fresh, form=None, change=False)

    def test_guest_orders_are_still_listed_and_searchable(self):
        """Guest checkout must not become invisible to staff."""
        self.client.force_login(self.staff)
        guest = make_order(user=None, customer_name='Guest Buyer')
        html = self.client.get(reverse('admin:shop_order_changelist')).content.decode()
        self.assertIn('Guest Buyer', html)

        searched = self.client.get(
            reverse('admin:shop_order_changelist'), {'q': 'Guest Buyer'}).content.decode()
        self.assertIn('Guest Buyer', searched)

    def test_an_order_can_be_found_by_its_number(self):
        """search_fields carries '=id' precisely so staff can paste a number in.

        The decoy is given no digits in any of its searchable fields, so the
        only way it could surface in the results is the id match this is
        checking for.
        """
        self.client.force_login(self.staff)
        make_order(customer_name='Someone Else', phone_number='', email='')
        wanted = make_order(customer_name='Number Search Target')
        html = self.client.get(
            reverse('admin:shop_order_changelist'), {'q': str(wanted.id)}).content.decode()
        self.assertIn(f'admin/shop/order/{wanted.id}/change/', html)
        self.assertNotIn('Someone Else', html)

    def test_an_order_can_be_found_by_the_phone_number_it_was_placed_with(self):
        self.client.force_login(self.staff)
        make_order(phone_number='08031234567', customer_name='Phone Search Target')
        html = self.client.get(
            reverse('admin:shop_order_changelist'), {'q': '08031234567'}).content.decode()
        self.assertIn('Phone Search Target', html)


class OrderLineAdminTests(TestCase):
    """Order lines are the record a total is derived from, so the admin may
    inspect them but must not be able to alter or destroy one."""

    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.model_admin = admin.site._registry[OrderItem]
        self.request = RequestFactory().get('/')
        self.request.user = self.staff
        self.client.force_login(self.staff)

    def test_lines_cannot_be_added_changed_or_deleted(self):
        self.assertFalse(self.model_admin.has_add_permission(self.request))
        self.assertFalse(self.model_admin.has_change_permission(self.request))
        self.assertFalse(self.model_admin.has_delete_permission(self.request))

    def test_the_add_page_is_refused_and_the_change_page_is_view_only(self):
        order = make_order(items=1)
        line = order.items.first()
        self.assertEqual(
            self.client.get(reverse('admin:shop_orderitem_add')).status_code, 403)
        # View permission still stands, so the line can be inspected.
        self.assertEqual(
            self.client.get(
                reverse('admin:shop_orderitem_change', args=[line.pk])).status_code, 200)

    def test_lines_can_be_found_by_the_order_they_belong_to(self):
        order = make_order(items=1, customer_name='Line Search Target')
        html = self.client.get(
            reverse('admin:shop_orderitem_changelist'), {'q': 'Line Search Target'}).content.decode()
        self.assertIn('Line 0', html)


class ReviewAdminTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.product = make_product(name='Reviewed Phone')

    def test_the_approve_action_only_changes_approval(self):
        review = Review.objects.create(
            product=self.product, reviewer_name='Bola', rating=2,
            title='Not great', body='Body text', verified_purchase=True)
        self.client.force_login(self.staff)
        # Driven through the real changelist rather than by calling the action
        # directly, so the admin's own plumbing is part of what is tested.
        self.client.post(
            reverse('admin:shop_review_changelist'),
            {'action': 'approve_reviews', '_selected_action': [str(review.pk)]},
            follow=True)
        review.refresh_from_db()
        self.assertTrue(review.is_approved)
        # Everything else about the review is untouched.
        self.assertTrue(review.verified_purchase)
        self.assertEqual(review.rating, 2)
        self.assertEqual(review.title, 'Not great')
        self.assertEqual(review.body, 'Body text')

    def test_the_approve_action_reports_what_it_did(self):
        review = Review.objects.create(product=self.product, reviewer_name='Bola', rating=2)
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('admin:shop_review_changelist'),
            {'action': 'approve_reviews', '_selected_action': [str(review.pk)]},
            follow=True)
        review.refresh_from_db()
        self.assertTrue(review.is_approved)
        self.assertContains(response, 'approved and now visible')

    def test_only_approved_reviews_are_public(self):
        """Approval is the one thing that decides whether a review is shown."""
        Review.objects.create(product=self.product, reviewer_name='Visible Reviewer',
                              rating=5, is_approved=True)
        Review.objects.create(product=self.product, reviewer_name='Hidden Reviewer',
                              rating=1, is_approved=False)
        html = self.client.get(
            reverse('shop:product_detail', args=[self.product.id])).content.decode()
        self.assertIn('Visible Reviewer', html)
        self.assertNotIn('Hidden Reviewer', html)


class AdminSafetyTests(TestCase):
    """Booking and contact data are still inspectable, and no destructive bulk
    action was added anywhere for convenience."""

    def setUp(self):
        self.staff = User.objects.create_superuser('owner', 'owner@example.com', 'pw-12345')
        self.client.force_login(self.staff)

    def test_a_repair_booking_is_findable_by_ticket_and_by_phone(self):
        repair_type = RepairType.objects.create(
            name='Screen replacement', description='Glass only',
            price_range='₦25,000', time_estimate='2 hrs')
        booking = RepairBooking.objects.create(
            customer_name='Ada Obi', phone_number='08039998877',
            device='iPhone 11', repair_type=repair_type)
        changelist = reverse('admin:shop_repairbooking_changelist')
        for query in (booking.ticket_number, '08039998877'):
            with self.subTest(query=query):
                html = self.client.get(changelist, {'q': query}).content.decode()
                self.assertIn('Ada Obi', html)

    def test_updating_a_repair_status_is_still_allowed(self):
        model_admin = admin.site._registry[RepairBooking]
        self.assertIn('status', model_admin.list_editable)

    def test_a_contact_message_is_findable_by_its_text(self):
        ContactMessage.objects.create(name='Ada Obi', message='Do you stock 65W chargers?')
        html = self.client.get(
            reverse('admin:shop_contactmessage_changelist'),
            {'q': '65W chargers'}).content.decode()
        self.assertIn('Ada Obi', html)

    def test_no_cart_admin_bulk_action_can_empty_a_customer_cart(self):
        """A cart is someone's pending basket; emptying every one of them is not
        something to put one click away."""
        model_admin = admin.site._registry[CartItem]
        self.assertFalse(getattr(model_admin, 'actions', None))
