"""Wishlist and compare: guest sessions, accounts, and the handover on login.

The bug these exist for: a logged-in customer's wishlist and compare list
disappeared when they logged out and back in. Both lists were stored only in
the session, and django.contrib.auth.logout() flushes the whole session — so
the data was destroyed on the way out and nothing had been written to the
database for the next login to restore.

The tests are grouped by the guarantee they pin down:

  * GuestListsStillWorkTests — anonymous visitors keep using their session,
    and merely rendering a page no longer creates one.
  * PersistenceAcrossLogoutTests — the reported bug itself.
  * WishlistMergeTests / CompareMergeTests — the handover rules, including the
    one asymmetry between the lists: the wishlist is a plain union, while
    compare lets the account win and only fills whatever slots remain under
    the cap of 4.
  * AccountBackedSurfaceTests — the pages, badges and toggle endpoints report
    the account's data for a logged-in customer.
  * RequestCacheTests — the account lists are built once per request, not once
    per caller (site_info runs on every page, and _card_context asks again).

Run with:  python manage.py test shop.tests_wishlist_compare
Uses an isolated in-memory test database; the dev db.sqlite3 is untouched.
"""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import CompareItem, Product, WishlistItem
from .session_lists import AccountCompareList, AccountWishlist

User = get_user_model()

PASSWORD = 'Sup3rSecret!23'


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=5, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def make_user(username='ada'):
    return User.objects.create_user(username, password=PASSWORD)


class BaseListTests(TestCase):
    def log_in(self, user):
        self.assertTrue(self.client.login(username=user.username, password=PASSWORD))

    def toggle(self, name, product):
        """Toggle through the endpoint the site's own buttons call.

        The header matters: both views answer with JSON only to an AJAX
        request, and redirect for a plain form post.
        """
        return self.client.post(
            reverse(f'shop:{name}_toggle', args=[product.id]),
            headers={'x-requested-with': 'XMLHttpRequest'},
        )

    def account_ids(self, user, account_class):
        """The account list's product ids, in the order the list holds them."""
        return list(account_class(user).ids.keys())


# --- Guests ------------------------------------------------------------------

class GuestListsStillWorkTests(BaseListTests):
    """Anonymous visitors keep the session-backed behaviour they always had."""

    def test_guest_wishlist_still_works(self):
        phone = make_product(name='Guest Phone')
        self.toggle('wishlist', phone)

        self.assertEqual(self.client.session['wishlist'], {str(phone.id): True})
        self.assertIn(phone, self.client.get(reverse('shop:wishlist_detail')).context['products'])
        # A guest's list is nowhere near the database.
        self.assertEqual(WishlistItem.objects.count(), 0)

    def test_guest_compare_still_works(self):
        phone = make_product(name='Guest Phone')
        self.toggle('compare', phone)

        self.assertEqual(self.client.session['compare'], {str(phone.id): True})
        self.assertIn(phone, self.client.get(reverse('shop:compare_detail')).context['products'])
        self.assertEqual(CompareItem.objects.count(), 0)

    def test_guest_compare_is_still_capped_at_four(self):
        products = [make_product(name=f'Guest {index}') for index in range(5)]
        for product in products:
            self.toggle('compare', product)

        ids = list(self.client.session['compare'].keys())
        self.assertEqual(len(ids), 4)
        # The oldest entry is the one that makes way, exactly as before.
        self.assertNotIn(str(products[0].id), ids)
        self.assertEqual(ids, [str(p.id) for p in products[1:]])

    def test_loading_a_page_does_not_hand_a_guest_a_session(self):
        """Reading the lists must not write them.

        The old implementation stored an empty dict back into the session from
        its constructor, so every anonymous visitor was given a session row and
        a cookie just for loading a page that asked for the header badges.
        """
        response = self.client.get(reverse('shop:home'))
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)


# --- The reported bug --------------------------------------------------------

class PersistenceAcrossLogoutTests(BaseListTests):
    """A logged-in customer's lists survive logging out and back in."""

    def setUp(self):
        self.user = make_user()
        self.phone = make_product(name='Saved Phone')

    def test_logged_in_wishlist_survives_logout_and_login(self):
        self.client.force_login(self.user)
        self.toggle('wishlist', self.phone)
        self.assertTrue(WishlistItem.objects.filter(user=self.user, product=self.phone).exists())

        self.client.post(reverse('logout'))
        # Still there with nobody logged in — this is the half that used to fail.
        self.assertTrue(WishlistItem.objects.filter(user=self.user, product=self.phone).exists())

        self.log_in(self.user)
        self.assertIn(self.phone, self.client.get(reverse('shop:wishlist_detail')).context['products'])

    def test_logged_in_compare_survives_logout_and_login(self):
        self.client.force_login(self.user)
        self.toggle('compare', self.phone)
        self.assertTrue(CompareItem.objects.filter(user=self.user, product=self.phone).exists())

        self.client.post(reverse('logout'))
        self.assertTrue(CompareItem.objects.filter(user=self.user, product=self.phone).exists())

        self.log_in(self.user)
        self.assertIn(self.phone, self.client.get(reverse('shop:compare_detail')).context['products'])


# --- Merging on login --------------------------------------------------------

class WishlistMergeTests(BaseListTests):
    """The wishlist merge is a plain union."""

    def setUp(self):
        self.user = make_user()
        self.phone = make_product(name='Guest Phone')
        self.cable = make_product(name='Guest Cable')

    def test_guest_wishlist_is_merged_into_the_account(self):
        self.toggle('wishlist', self.phone)
        self.toggle('wishlist', self.cable)

        self.log_in(self.user)

        saved = set(WishlistItem.objects.filter(user=self.user).values_list('product_id', flat=True))
        self.assertEqual(saved, {self.phone.id, self.cable.id})
        # Merged in the order the guest saved them, which is the order the
        # wishlist page shows them in.
        self.assertEqual(
            self.account_ids(self.user, AccountWishlist),
            [str(self.phone.id), str(self.cable.id)],
        )

    def test_a_product_already_saved_is_not_duplicated(self):
        WishlistItem.objects.create(user=self.user, product=self.phone)
        self.toggle('wishlist', self.phone)

        self.log_in(self.user)

        self.assertEqual(WishlistItem.objects.filter(user=self.user, product=self.phone).count(), 1)

    def test_the_guest_list_is_emptied_by_the_merge(self):
        self.toggle('wishlist', self.phone)
        self.log_in(self.user)

        # Emptied, so logging in again cannot apply the same list twice.
        self.assertEqual(self.client.session.get('wishlist'), {})

    def test_logging_in_twice_does_not_duplicate_anything(self):
        self.toggle('wishlist', self.phone)
        self.log_in(self.user)
        self.client.post(reverse('logout'))
        self.log_in(self.user)

        self.assertEqual(WishlistItem.objects.filter(user=self.user).count(), 1)

    def test_inactive_products_are_not_merged(self):
        hidden = make_product(name='Unlisted', is_active=False)
        self.toggle('wishlist', hidden)
        self.toggle('wishlist', self.phone)

        self.log_in(self.user)

        saved = set(WishlistItem.objects.filter(user=self.user).values_list('product_id', flat=True))
        self.assertEqual(saved, {self.phone.id})

    def test_out_of_stock_products_are_merged(self):
        """Stock is irrelevant to a wishlist — a sold-out item is exactly the
        kind of thing you want to keep an eye on."""
        sold_out = make_product(name='Sold Out', stock_quantity=0)
        self.toggle('wishlist', sold_out)

        self.log_in(self.user)

        self.assertTrue(WishlistItem.objects.filter(user=self.user, product=sold_out).exists())


class CompareMergeTests(BaseListTests):
    """The compare merge lets the account win, then fills what's left."""

    def setUp(self):
        self.user = make_user()
        self.a = make_product(name='A')
        self.b = make_product(name='B')
        self.c = make_product(name='C')
        self.d = make_product(name='D')
        self.e = make_product(name='E')

    def test_guest_compare_is_merged_into_the_account(self):
        self.toggle('compare', self.a)
        self.toggle('compare', self.b)

        self.log_in(self.user)

        saved = set(CompareItem.objects.filter(user=self.user).values_list('product_id', flat=True))
        self.assertEqual(saved, {self.a.id, self.b.id})

    def test_the_account_wins_and_the_guest_fills_the_remaining_slots(self):
        """Account A, B + guest B, C, D, E must come out as A, B, C, D.

        B was already on the account, so the guest's copy of it changes
        nothing; C and D take the two remaining slots; E has nowhere to go.
        Nothing already on the account is displaced.
        """
        CompareItem.objects.create(user=self.user, product=self.a)
        CompareItem.objects.create(user=self.user, product=self.b)
        for product in (self.b, self.c, self.d, self.e):
            self.toggle('compare', product)

        self.log_in(self.user)

        self.assertEqual(
            self.account_ids(self.user, AccountCompareList),
            [str(self.a.id), str(self.b.id), str(self.c.id), str(self.d.id)],
        )

    def test_a_full_account_list_is_left_completely_alone(self):
        for product in (self.a, self.b, self.c, self.d):
            CompareItem.objects.create(user=self.user, product=product)
        self.toggle('compare', self.e)

        self.log_in(self.user)

        self.assertEqual(
            self.account_ids(self.user, AccountCompareList),
            [str(p.id) for p in (self.a, self.b, self.c, self.d)],
        )

    def test_the_merge_never_exceeds_the_cap_of_four(self):
        for product in (self.a, self.b, self.c, self.d, self.e):
            self.toggle('compare', product)
        # A guest can only hold four, so the fifth is already gone before the
        # merge — assert on the total rather than on which four survived.
        self.log_in(self.user)
        self.assertEqual(CompareItem.objects.filter(user=self.user).count(), 4)

    def test_the_guest_compare_list_is_emptied_by_the_merge(self):
        self.toggle('compare', self.a)
        self.log_in(self.user)

        self.assertEqual(self.client.session.get('compare'), {})

    def test_inactive_and_out_of_stock_products_merge_by_the_same_rules(self):
        hidden = make_product(name='Unlisted', is_active=False)
        sold_out = make_product(name='Sold Out', stock_quantity=0)
        self.toggle('compare', hidden)
        self.toggle('compare', sold_out)

        self.log_in(self.user)

        saved = set(CompareItem.objects.filter(user=self.user).values_list('product_id', flat=True))
        self.assertEqual(saved, {sold_out.id})


# --- Account-backed surface --------------------------------------------------

class AccountBackedSurfaceTests(BaseListTests):
    """Pages, badges and endpoints report the account's data once logged in."""

    def setUp(self):
        self.user = make_user()
        self.phone = make_product(name='Saved Phone')

    def test_the_toggle_endpoint_writes_to_the_account(self):
        self.client.force_login(self.user)

        response = self.toggle('wishlist', self.phone)
        self.assertEqual(response.json(), {'ok': True, 'added': True, 'wishlist_count': 1})
        self.assertTrue(WishlistItem.objects.filter(user=self.user, product=self.phone).exists())

        # Toggling again removes it, still without touching the session.
        response = self.toggle('wishlist', self.phone)
        self.assertEqual(response.json(), {'ok': True, 'added': False, 'wishlist_count': 0})
        self.assertFalse(WishlistItem.objects.filter(user=self.user, product=self.phone).exists())

    def test_compare_toggle_reports_the_account_count(self):
        self.client.force_login(self.user)

        self.assertEqual(self.toggle('compare', self.phone).json()['compare_count'], 1)
        self.assertTrue(CompareItem.objects.filter(user=self.user, product=self.phone).exists())

    def test_the_header_badges_show_the_account_count(self):
        WishlistItem.objects.create(user=self.user, product=self.phone)
        WishlistItem.objects.create(user=self.user, product=make_product(name='Second'))
        self.client.force_login(self.user)

        context = self.client.get(reverse('shop:home')).context
        self.assertEqual(context['wishlist_count'], 2)
        self.assertEqual(context['compare_count'], 0)

    def test_the_product_page_reports_the_account_list(self):
        WishlistItem.objects.create(user=self.user, product=self.phone)
        self.client.force_login(self.user)

        context = self.client.get(reverse('shop:product_detail', args=[self.phone.id])).context
        self.assertTrue(context['in_wishlist'])
        self.assertFalse(context['in_compare'])
        self.assertEqual(context['wishlist_ids'], {self.phone.id})

    def test_the_account_compare_list_is_capped_at_four_and_evicts_the_oldest(self):
        products = [make_product(name=f'Account {index}') for index in range(5)]
        self.client.force_login(self.user)

        for product in products:
            self.toggle('compare', product)

        ids = self.account_ids(self.user, AccountCompareList)
        self.assertEqual(ids, [str(p.id) for p in products[1:]])
        self.assertNotIn(str(products[0].id), ids)


# --- Building the lists once per request -------------------------------------

class RequestCacheTests(BaseListTests):
    """The account lists are built once per request, not once per caller."""

    def test_the_account_wishlist_is_queried_once_per_page(self):
        """site_info runs on every page and _card_context asks again on /shop/.

        Without a request-scoped cache those two callers would each build the
        list and run their own query for it. Three saved products so there is
        something to fetch, and a warm-up request so the session write that
        only happens on a first visit isn't counted.
        """
        user = make_user()
        for index in range(3):
            WishlistItem.objects.create(user=user, product=make_product(name=f'Saved {index}'))
        self.client.force_login(user)
        self.client.get(reverse('shop:shop'))

        with CaptureQueriesContext(connection) as captured:
            self.client.get(reverse('shop:shop'))

        wishlist_queries = [
            query['sql'] for query in captured.captured_queries
            if 'wishlistitem' in query['sql'].lower()
        ]
        self.assertEqual(len(wishlist_queries), 1)
