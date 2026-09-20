"""Regression tests for the launch-quality fixes from the audit.

Each test here pins down one behaviour that was previously broken, so it
can't silently regress again:

  * Checkout and contact validation errors are actually visible on the page
    (they used to be swallowed — a bad email just reloaded a blank-looking
    form, with no hint of what was wrong).
  * The payment page renders the Paystack SDK and its handler exactly once,
    and hands the customer's email to Paystack as a correctly escaped
    JavaScript string (HTML-escaping is not enough inside <script>).
  * Payment verification only accepts a transaction that is genuinely ours,
    for the exact right amount, and only then reserves stock.
  * The Track page refuses a phone number too short to be a real ownership
    proof instead of accidentally matching a large share of orders.

Run with:  python manage.py test shop.tests_launch_fixes
Deliberately offline: Paystack's HTTP call is mocked throughout, so these
tests need no network access and no live Paystack credentials. Uses an
isolated in-memory test database; the dev db.sqlite3 is untouched.
"""
from decimal import Decimal
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from .models import ContactMessage, Order, OrderItem, Product
from .paystack import PaystackVerificationError, verify_transaction


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=10, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


CHECKOUT_DETAILS = {
    'customer_name': 'Ada Obi',
    'email': 'ada@example.com',
    'phone_number': '08031234567',
    'delivery_address': 'No3 Charismatic Crescent, Ugbokolo',
    'payment_method': 'delivery',
}

ORDER_PHONE = '08031234567'  # last 7 digits: 1234567


# --- Paystack doubles -----------------------------------------------------------
# verify_transaction() only ever touches `raise_for_status()` and `json()` on
# the response, so a plain Mock stands in for requests' Response object.

def fake_paystack_response(payload, status_code=200):
    response = mock.Mock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def paystack_payload(reference, amount_kobo, email='ada@example.com', status='success', currency='NGN'):
    """A transaction payload shaped exactly like Paystack's verify response."""
    return {
        'status': True,
        'message': 'Verification successful',
        'data': {
            'reference': reference,
            'status': status,
            'amount': amount_kobo,
            'currency': currency,
            'customer': {'email': email},
        },
    }


class CheckoutValidationErrorTests(TestCase):
    """A rejected checkout must say why, and must not create an order."""

    def setUp(self):
        self.product = make_product()
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})

    def test_an_invalid_email_shows_a_visible_error_and_creates_no_order(self):
        details = dict(CHECKOUT_DETAILS, email='not-an-email')
        response = self.client.post(reverse('shop:checkout'), details)

        # Re-rendered with the bound form, not redirected away.
        self.assertEqual(response.status_code, 200)

        # The error is on the form the template was given...
        self.assertIn('email', response.context['form'].errors)
        # ...and it actually reached the page the customer is looking at.
        self.assertContains(response, 'Enter a valid email')

        # Nothing was ordered, and no stock was touched.
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)


class ContactFormErrorTests(TestCase):
    """A rejected contact message must say why, and must not be swallowed."""

    def test_an_invalid_contact_message_shows_a_visible_error_and_saves_nothing(self):
        response = self.client.post(reverse('shop:contact'), {
            'name': 'Ada Obi',
            'email': 'not-an-email',
            'phone_number': '',
            'message': 'Do you have this in blue?',
        })

        self.assertEqual(response.status_code, 200)
        self.assertIn('email', response.context['form'].errors)
        self.assertContains(response, 'Enter a valid email')

        self.assertEqual(ContactMessage.objects.count(), 0)


@override_settings(PAYSTACK_PUBLIC_KEY='pk_test_offline_only')
class PaymentPageRenderingTests(TestCase):
    """The payment page's inline JavaScript has to be valid and single."""

    def setUp(self):
        self.order = Order.objects.create(
            customer_name='Ada Obi', email='ada@example.com',
            phone_number=ORDER_PHONE, delivery_address='Ugbokolo',
            payment_method='online', payment_reference='RBT-abc123def456',
        )
        OrderItem.objects.create(order=self.order, product=make_product(),
                                 product_name='Test Phone', price=Decimal('100000'), quantity=1)

    def _payment_page(self, **kwargs):
        if kwargs:
            for field, value in kwargs.items():
                setattr(self.order, field, value)
            self.order.save()
        url = reverse('shop:payment', args=[self.order.id, self.order.access_token])
        return self.client.get(url).content.decode()

    def test_the_paystack_sdk_and_handler_are_emitted_exactly_once(self):
        # The extra_js block used to be nested inside the content block, so
        # Django rendered it twice — the second `new PaystackPop()` ran against
        # an already-declared `const` and threw. One copy is correct; two is a
        # JavaScript error waiting to happen.
        html = self._payment_page()
        self.assertEqual(html.count('js.paystack.co/v2/inline.js'), 1)
        self.assertEqual(html.count('new PaystackPop()'), 1)

    def test_an_email_with_an_apostrophe_is_escaped_for_java_script(self):
        # Inside <script>, HTML entities are NOT decoded, so HTML autoescaping
        # would hand Paystack the literal text "o&#x27;brien@..." — which then
        # fails our own email check in paystack.py AFTER the card is charged.
        # escapejs produces a string that is still the customer's real email.
        html = self._payment_page(email="o'brien@example.com")

        # The rendered JS must carry a real JavaScript escape (the backslash-u
        # form is escapejs's spelling of an apostrophe), and must NOT carry the
        # HTML entity form, which would corrupt the address.
        self.assertIn('o\\u0027brien@example.com', html)
        self.assertNotIn('o&#x27;brien', html)


class PaystackVerificationTests(TestCase):
    """verify_transaction() with Paystack's HTTP call mocked out."""

    def test_a_matching_successful_transaction_is_accepted(self):
        payload = paystack_payload('RBT-abc123def456', 200000)
        with mock.patch('shop.paystack.requests.get',
                        return_value=fake_paystack_response(payload)) as get:
            data = verify_transaction(
                'RBT-abc123def456', 200000,
                expected_reference='RBT-abc123def456',
                expected_email='ada@example.com',
            )

        self.assertEqual(data['reference'], 'RBT-abc123def456')
        self.assertEqual(data['amount'], 200000)
        get.assert_called_once()

    def test_an_amount_mismatch_is_rejected(self):
        # One kobo short is still short — a part-payment must never be accepted.
        payload = paystack_payload('RBT-abc123def456', 199999)
        with mock.patch('shop.paystack.requests.get',
                        return_value=fake_paystack_response(payload)):
            with self.assertRaises(PaystackVerificationError) as caught:
                verify_transaction(
                    'RBT-abc123def456', 200000,
                    expected_reference='RBT-abc123def456',
                    expected_email='ada@example.com',
                )

        self.assertIn('Amount mismatch', str(caught.exception))

    def test_a_reference_belonging_to_another_order_is_rejected(self):
        # A genuine, successful, correctly-priced transaction — but issued for
        # a different order. Paystack must not even be asked about it.
        payload = paystack_payload('RBT-someoneelsesref', 200000)
        with mock.patch('shop.paystack.requests.get',
                        return_value=fake_paystack_response(payload)) as get:
            with self.assertRaises(PaystackVerificationError):
                verify_transaction(
                    'RBT-someoneelsesref', 200000,
                    expected_reference='RBT-abc123def456',
                    expected_email='ada@example.com',
                )

        get.assert_not_called()


class VerifyPaymentViewTests(TestCase):
    """The verify_payment view end-to-end, with Paystack mocked."""

    def setUp(self):
        self.product = make_product(stock_quantity=10)
        self.order = Order.objects.create(
            customer_name='Ada Obi', email='ada@example.com',
            phone_number=ORDER_PHONE, delivery_address='Ugbokolo',
            payment_method='online', payment_reference='RBT-abc123def456',
        )
        OrderItem.objects.create(order=self.order, product=self.product,
                                 product_name='Test Phone', price=Decimal('100000'), quantity=2)
        self.url = reverse('shop:verify_payment', args=[self.order.id, self.order.access_token])
        self.confirmation_url = reverse('shop:order_confirmation',
                                        args=[self.order.id, self.order.access_token])
        self.expected_kobo = int(self.order.total() * 100)  # 200000

    def test_a_verified_payment_marks_the_order_paid_and_reserves_stock(self):
        payload = paystack_payload(self.order.payment_reference, self.expected_kobo)
        with mock.patch('shop.paystack.requests.get',
                        return_value=fake_paystack_response(payload)):
            response = self.client.get(self.url, {'reference': self.order.payment_reference})

        self.assertRedirects(response, self.confirmation_url, fetch_redirect_response=False)

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'paid')
        self.assertEqual(self.order.payment_reference, 'RBT-abc123def456')

        # Stock is reserved only now, once the money is confirmed.
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 8)

    def test_an_amount_mismatch_leaves_the_order_unpaid_and_reserves_nothing(self):
        payload = paystack_payload(self.order.payment_reference, self.expected_kobo - 100)
        with mock.patch('shop.paystack.requests.get',
                        return_value=fake_paystack_response(payload)):
            response = self.client.get(self.url, {'reference': self.order.payment_reference})

        # Sent back to the payment page to try again, with an explanation.
        self.assertRedirects(
            response,
            reverse('shop:payment', args=[self.order.id, self.order.access_token]),
            fetch_redirect_response=False,
        )

        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'failed')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)

    def test_a_reference_from_another_order_never_reaches_paystack(self):
        with mock.patch('shop.paystack.requests.get') as get:
            response = self.client.get(self.url, {'reference': 'RBT-someoneelsesref'})

        get.assert_not_called()
        self.order.refresh_from_db()
        self.assertEqual(self.order.payment_status, 'failed')
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 10)


class ShortPhoneOwnershipProofTests(TestCase):
    """Track's phone check is the only ownership proof on that path."""

    def setUp(self):
        self.order = Order.objects.create(
            customer_name='Old Guest', email='guest@example.com',
            phone_number=ORDER_PHONE, delivery_address='Ugbokolo',
        )
        OrderItem.objects.create(order=self.order, product=make_product(),
                                 product_name='Old Phone', price=Decimal('80000'), quantity=1)

    def _track(self, phone_number):
        return self.client.post(reverse('shop:track'),
                                {'reference_number': str(self.order.id),
                                 'phone_number': phone_number})

    def test_a_single_digit_cannot_unlock_an_order(self):
        # '7' is the last digit of the phone on this order, and it is what the
        # old fallback compared — so it used to match. A one-digit "proof"
        # would unlock roughly one order in ten for anyone guessing.
        response = self._track('7')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['result'])
        self.assertIn('at least 7 digits', response.context['error'])

    def test_the_full_phone_number_still_unlocks_the_order(self):
        # The guard must not lock out customers who type their number normally.
        response = self._track(ORDER_PHONE)

        self.assertIsNone(response.context['error'])
        self.assertEqual(response.context['result']['object'], self.order)
