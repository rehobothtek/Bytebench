"""Verification tests for the customer account system (registration, login,
persistent cart, saved delivery details, order history).

Run with:  python manage.py test shop.tests_accounts
Uses an isolated in-memory test database; the dev db.sqlite3 is untouched.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import CartItem, CustomerProfile, Order, OrderItem, Product


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=10, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


REGISTRATION = {
    'username': 'ada',
    'first_name': 'Ada Obi',
    'email': 'ada@example.com',
    'phone_number': '08031234567',
    'password1': 'Sup3rSecret!23',
    'password2': 'Sup3rSecret!23',
}


class RegistrationTests(TestCase):
    """Test A — registration, password, login."""

    def test_register_creates_an_account_that_can_log_in(self):
        response = self.client.post(reverse('shop:register'), REGISTRATION)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('shop:account'))

        user = User.objects.get(username='ada')
        self.assertEqual(user.email, 'ada@example.com')
        self.assertEqual(user.first_name, 'Ada Obi')
        # Django hashed it — never stored in the clear.
        self.assertNotEqual(user.password, 'Sup3rSecret!23')
        self.assertTrue(user.check_password('Sup3rSecret!23'))

        # The new account is logged straight in.
        self.assertEqual(int(self.client.session['_auth_user_id']), user.id)

    def test_registration_details_become_the_saved_delivery_details(self):
        self.client.post(reverse('shop:register'), REGISTRATION)
        profile = CustomerProfile.objects.get(user__username='ada')
        self.assertEqual(profile.default_name, 'Ada Obi')
        self.assertEqual(profile.default_phone, '08031234567')
        self.assertEqual(profile.default_email, 'ada@example.com')

    def test_logging_out_and_back_in_works(self):
        self.client.post(reverse('shop:register'), REGISTRATION)
        self.assertEqual(self.client.get(reverse('shop:account')).status_code, 200)

        # Logout is POST-only — a GET link must not be able to log anyone out.
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertEqual(self.client.post(reverse('logout')).status_code, 302)

        self.assertTrue(self.client.login(username='ada', password='Sup3rSecret!23'))
        self.assertEqual(self.client.get(reverse('shop:account')).status_code, 200)

    def test_weak_password_is_rejected_by_djangos_validators(self):
        response = self.client.post(reverse('shop:register'), {
            **REGISTRATION, 'password1': 'password', 'password2': 'password',
        })
        self.assertEqual(response.status_code, 200)  # re-rendered with errors
        self.assertFalse(User.objects.filter(username='ada').exists())

    def test_login_page_and_account_views_are_protected(self):
        self.assertEqual(self.client.get(reverse('login')).status_code, 200)

        for url in (reverse('shop:account'), reverse('shop:order_history'),
                    reverse('shop:order_detail', args=[1])):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertIn(reverse('login'), response['Location'])


class CartTransferTests(TestCase):
    """Tests B, C, H, I — the guest cart moving into an account."""

    def setUp(self):
        self.phone = make_product(name='Guest Cart Phone', stock_quantity=10)
        self.cable = make_product(name='Guest Cart Cable', stock_quantity=10, price=Decimal('5000'))

    def _fill_guest_cart(self, **quantities):
        for name, quantity in quantities.items():
            product = getattr(self, name)
            self.client.post(reverse('shop:cart_add', args=[product.id]), {'quantity': str(quantity)})

    def _put_guest_cart_in_the_session(self, cart):
        """Poke a cart straight into the guest session.

        `self.client.session` builds a fresh SessionStore on *every* access, so
        `self.client.session['cart'] = ...` writes into an object that is then
        thrown away (and the `save()` on the next access re-loads the empty row
        from the database). Hold on to one session and save that — the posted
        quantities here are deliberately not ones cart_add would accept.
        """
        session = self.client.session
        session['cart'] = cart
        session.save()

    def test_b_guest_cart_is_moved_into_the_new_account(self):
        """Test B — register with a full cart; it arrives intact, once."""
        self._fill_guest_cart(phone=2, cable=3)
        self.client.post(reverse('shop:register'), REGISTRATION)
        user = User.objects.get(username='ada')

        items = {item.product_id: item.quantity for item in CartItem.objects.filter(user=user)}
        self.assertEqual(items, {self.phone.id: 2, self.cable.id: 3})

        # The session cart is emptied, so the merge can't be applied twice.
        self.assertEqual(self.client.session['cart'], {})
        self.assertEqual(CartItem.objects.filter(user=user).count(), 2)

    def test_h_quantities_combine_when_a_product_is_in_both_carts(self):
        """Tests H and I — a returning customer's two carts add up, capped at 20."""
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        # Stock has to be plentiful here, otherwise the stock ceiling (not the
        # 20-per-line maximum) is what the merge trips over.
        bulk = make_product(name='Bulk Phone', stock_quantity=50)
        CartItem.objects.create(user=user, product=bulk, quantity=15)
        self._put_guest_cart_in_the_session({str(bulk.id): 15})

        self.assertTrue(self.client.login(username='ada', password='Sup3rSecret!23'))

        item = CartItem.objects.get(user=user, product=bulk)
        self.assertEqual(item.quantity, 20)  # not 30 — the per-line maximum holds
        self.assertEqual(self.client.session['cart'], {})

    def test_i_logging_in_again_does_not_duplicate_the_cart(self):
        """Test C and I — the cart survives logout, and a second login changes nothing."""
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.phone.id]), {'quantity': '4'})
        self.assertEqual(CartItem.objects.get(user=user, product=self.phone).quantity, 4)

        self.client.post(reverse('logout'))
        # Test C — the cart is still there after logging out.
        self.assertEqual(CartItem.objects.get(user=user, product=self.phone).quantity, 4)

        self.assertTrue(self.client.login(username='ada', password='Sup3rSecret!23'))
        self.assertEqual(CartItem.objects.filter(user=user).count(), 1)
        self.assertEqual(CartItem.objects.get(user=user, product=self.phone).quantity, 4)

    def test_merge_respects_stock_and_skips_unavailable_products(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        scarce = make_product(name='Only Three Left', stock_quantity=3)
        sold_out = make_product(name='Sold Out', stock_quantity=0)
        # A stale session cart: more than stock, plus a sold-out product and a deleted one.
        self._put_guest_cart_in_the_session({str(scarce.id): 8, str(sold_out.id): 1, '999999': 2})

        self.assertTrue(self.client.login(username='ada', password='Sup3rSecret!23'))

        item = CartItem.objects.get(user=user, product=scarce)
        self.assertEqual(item.quantity, 3)  # trimmed to what's actually available
        self.assertFalse(CartItem.objects.filter(user=user, product=sold_out).exists())
        self.assertEqual(CartItem.objects.filter(user=user).count(), 1)
        self.assertEqual(self.client.session['cart'], {})

    def test_guests_keep_a_session_cart_and_accounts_use_the_database(self):
        self.client.post(reverse('shop:cart_add', args=[self.phone.id]), {'quantity': '2'})
        self.assertEqual(self.client.session['cart'][str(self.phone.id)], 2)
        self.assertEqual(CartItem.objects.count(), 0)  # nothing hits the DB for guests

        # A fresh session, then logging in: the cart now lives in the database.
        self.client.logout()
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.phone.id]), {'quantity': '2'})
        self.assertEqual(CartItem.objects.get(user=user, product=self.phone).quantity, 2)
        # Nothing was written back into the guest session cart in the process.
        self.assertFalse(self.client.session.get('cart'))

    def test_cart_count_badge_matches_the_account_cart(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.phone.id]), {'quantity': '5'})
        self.client.post(reverse('shop:cart_add', args=[self.cable.id]), {'quantity': '4'})
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn('id="cartCount">9<', html)

    def test_cart_can_be_updated_and_emptied_through_the_existing_urls(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.phone.id]), {'quantity': '2'})

        self.client.post(reverse('shop:cart_update', args=[self.phone.id]), {'action': 'increase'})
        self.assertEqual(CartItem.objects.get(user=user, product=self.phone).quantity, 3)

        self.client.post(reverse('shop:cart_update', args=[self.phone.id]), {'action': 'remove'})
        self.assertEqual(CartItem.objects.filter(user=user).count(), 0)


class CheckoutTests(TestCase):
    """Tests D and F — authenticated and guest checkout."""

    def setUp(self):
        self.product = make_product(name='Checkout Phone', stock_quantity=5, price=Decimal('120000'))
        self.order_details = {
            'customer_name': 'Ada Obi',
            'email': 'ada@example.com',
            'phone_number': '08031234567',
            'delivery_address': 'No3 Charismatic Crescent, Ugbokolo',
            'payment_method': 'delivery',
            'save_details': 'on',
        }

    def test_d_authenticated_checkout_attaches_the_order_to_the_account(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '2'})

        response = self.client.post(reverse('shop:checkout'), self.order_details)
        self.assertEqual(response.status_code, 302)

        order = Order.objects.get()
        self.assertEqual(order.user, user)
        # The order still keeps its own snapshot of the customer's details.
        self.assertEqual(order.customer_name, 'Ada Obi')
        self.assertEqual(order.phone_number, '08031234567')
        self.assertEqual(order.delivery_address, 'No3 Charismatic Crescent, Ugbokolo')
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.items.first().quantity, 2)
        self.assertEqual(order.payment_status, 'unpaid')
        # The account cart is emptied once the order is placed.
        self.assertEqual(CartItem.objects.filter(user=user).count(), 0)

    def test_d_checkout_saves_the_delivery_details_for_next_time(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})
        self.client.post(reverse('shop:checkout'), self.order_details)

        profile = CustomerProfile.objects.get(user=user)
        self.assertEqual(profile.delivery_address, 'No3 Charismatic Crescent, Ugbokolo')
        self.assertEqual(profile.default_phone, '08031234567')

        # Next checkout comes pre-filled from that profile.
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})
        html = self.client.get(reverse('shop:checkout')).content.decode()
        self.assertIn('No3 Charismatic Crescent, Ugbokolo', html)

    def test_d_unticking_the_box_leaves_saved_details_alone(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        profile = CustomerProfile.for_user(user)
        profile.delivery_address = 'Old Address, Otukpo'
        profile.save()
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})

        # The same form submission, but with the "remember these details" box
        # left unticked — so the profile must be left exactly as it was.
        unticked = {k: v for k, v in self.order_details.items() if k != 'save_details'}
        self.client.post(reverse('shop:checkout'), {**unticked, 'delivery_address': 'Somewhere Else'})
        profile.refresh_from_db()
        self.assertEqual(profile.delivery_address, 'Old Address, Otukpo')
        # ...but the order itself still recorded what was actually submitted.
        self.assertEqual(Order.objects.get().delivery_address, 'Somewhere Else')

    def test_f_guest_checkout_still_works_and_leaves_user_empty(self):
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})
        response = self.client.post(reverse('shop:checkout'), self.order_details)
        self.assertEqual(response.status_code, 302)

        order = Order.objects.get()
        self.assertIsNone(order.user)
        self.assertEqual(order.customer_name, 'Ada Obi')
        self.assertEqual(order.total(), Decimal('120000'))
        self.assertEqual(CustomerProfile.objects.count(), 0)

    def test_checkout_does_not_change_the_paystack_reference_behaviour(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        self.client.post(reverse('shop:cart_add', args=[self.product.id]), {'quantity': '1'})
        self.client.post(reverse('shop:checkout'), {**self.order_details, 'payment_method': 'online'})

        order = Order.objects.get()
        self.assertEqual(order.user, user)
        self.assertTrue(order.payment_reference.startswith('RBT-'))
        self.assertEqual(order.payment_status, 'unpaid')


class OrderHistoryTests(TestCase):
    """Test E — My Orders, order detail, and ownership isolation."""

    def setUp(self):
        self.ada = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.bola = User.objects.create_user('bola', password='Sup3rSecret!23')
        self.ada_order = self._make_order(self.ada, 'Ada Phone', Decimal('50000'))
        self.bola_order = self._make_order(self.bola, 'Bola Laptop', Decimal('300000'))

    def _make_order(self, user, product_name, price):
        order = Order.objects.create(
            user=user, customer_name=user.username, email=f'{user.username}@example.com',
            phone_number='08031234567', delivery_address=f'{user.username} test street 42',
        )
        OrderItem.objects.create(order=order, product_name=product_name, price=price, quantity=1)
        return order

    def test_e_order_history_lists_only_my_orders(self):
        self.client.force_login(self.ada)
        html = self.client.get(reverse('shop:order_history')).content.decode()
        self.assertIn(f'/account/orders/{self.ada_order.id}/', html)
        self.assertNotIn(f'/account/orders/{self.bola_order.id}/', html)
        # The site formats money with floatformat:0, which does not group digits.
        self.assertIn('₦50000', html)

    def test_e_order_detail_shows_products_totals_and_delivery_details(self):
        self.client.force_login(self.ada)
        response = self.client.get(reverse('shop:order_detail', args=[self.ada_order.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('Ada Phone', html)
        self.assertIn('₦50000', html)
        self.assertIn('ada test street 42', html)   # this order's own delivery address
        self.assertIn('Unpaid', html)               # payment status
        self.assertIn('>New</span>', html)          # order status

    def test_e_another_customers_order_is_not_reachable(self):
        """The whole point: changing the id in the URL must not work."""
        self.client.force_login(self.ada)
        response = self.client.get(reverse('shop:order_detail', args=[self.bola_order.id]))
        self.assertEqual(response.status_code, 404)

    def test_e_anonymous_visitors_cannot_browse_orders(self):
        response = self.client.get(reverse('shop:order_detail', args=[self.ada_order.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response['Location'])

    def test_e_a_deleted_account_does_not_take_its_orders_with_it(self):
        order_id = self.ada_order.id
        self.ada.delete()
        self.assertTrue(Order.objects.filter(id=order_id).exists())
        self.assertIsNone(Order.objects.get(id=order_id).user)


class ExistingGuestOrderTests(TestCase):
    """Test G — an order with no user (like the ones already in the database) stays usable."""

    def setUp(self):
        self.product = make_product(stock_quantity=5)
        self.order = Order.objects.create(
            customer_name='Old Guest', email='guest@example.com',
            phone_number='08031234567', delivery_address='Ugbokolo',
        )
        OrderItem.objects.create(order=self.order, product=self.product,
                                 product_name='Old Phone', price=Decimal('80000'), quantity=2)

    def test_g_a_guest_order_is_still_reachable_through_its_link(self):
        response = self.client.get(reverse('shop:order_confirmation',
                                           args=[self.order.id, self.order.access_token]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('Old Phone', response.content.decode())
        self.assertEqual(self.order.total(), Decimal('160000'))

    def test_g_a_guest_order_is_still_trackable_by_number_and_phone(self):
        response = self.client.post(reverse('shop:track'),
                                    {'reference_number': str(self.order.id), 'phone_number': '08031234567'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['result']['object'], self.order)

    def test_g_logged_in_customers_do_not_see_guest_orders(self):
        user = User.objects.create_user('ada', password='Sup3rSecret!23')
        self.client.force_login(user)
        html = self.client.get(reverse('shop:order_history')).content.decode()
        self.assertIn('Browse devices', html)  # the empty state
        self.assertNotIn(f'/account/orders/{self.order.id}/', html)


class AdminSurfaceTests(TestCase):
    """The new models are inspectable, and nothing about passwords leaks."""

    def test_admin_imports_and_registers_the_new_models(self):
        from django.contrib import admin as django_admin

        from .models import CartItem, CustomerProfile

        self.assertIn(CustomerProfile, django_admin.site._registry)
        self.assertIn(CartItem, django_admin.site._registry)

    def test_order_admin_shows_the_associated_user(self):
        from .admin import OrderAdmin

        self.assertIn('user', OrderAdmin.list_display)
        self.assertIn('user__username', OrderAdmin.search_fields)
