"""Wishlist and compare lists, for guests and for logged-in customers.

Two storage backends sit behind one interface, the same split shop/cart.py
uses:

  * ``Wishlist`` / ``CompareList`` keep a guest's list in their session.
  * ``AccountWishlist`` / ``AccountCompareList`` keep a logged-in customer's
    list in the database, so it survives logging out — which the session
    version could never do, because django.contrib.auth.logout() flushes the
    whole session out from under it.

``get_wishlist()`` / ``get_compare()`` are what pick between them. Views,
templates and the context processor should always go through those two and
never construct a class directly.

Both backends expose the same members that views, templates and the context
processor use — ``ids``, ``contains``, ``toggle``, ``remove``, ``clear``,
``count``, ``products`` — so nothing downstream has to know which kind of list
it is dealing with. The account backend adds ``add()`` on top, which the merge
needs and the session backend has no caller for.
"""
from .models import Product, WishlistItem, CompareItem


class _ProductIdSet:
    """A guest's list, stored in their session.

    The list is a dict of ``{product_id_string: True}``. Its insertion order is
    load-bearing: ``CompareList`` evicts its oldest entry when full, and
    ``products()`` hands the rows back in the order they were added.
    """

    session_key = None
    max_items = None

    def __init__(self, request):
        self.session = request.session
        # Read without writing. Rendering a page asks for these lists on every
        # request, and an earlier version stored an empty dict back into the
        # session here — which handed a session cookie to every anonymous
        # visitor just for loading a page. Nothing is written until something
        # actually changes.
        self.ids = self.session.get(self.session_key) or {}

    def save(self):
        self.session[self.session_key] = self.ids
        self.session.modified = True

    def contains(self, product_id):
        return str(product_id) in self.ids

    def toggle(self, product_id):
        product_id = str(product_id)
        if product_id in self.ids:
            del self.ids[product_id]
            added = False
        else:
            if self.max_items and len(self.ids) >= self.max_items:
                oldest = next(iter(self.ids))
                del self.ids[oldest]
            self.ids[product_id] = True
            added = True
        self.save()
        return added

    def remove(self, product_id):
        self.ids.pop(str(product_id), None)
        self.save()

    def clear(self):
        self.session[self.session_key] = {}
        self.ids = self.session[self.session_key]
        self.save()

    def count(self):
        return len(self.ids)

    def products(self):
        # Annotated so the cards on the wishlist/compare pages get their review
        # numbers in this one query rather than one each.
        products = Product.objects.filter(id__in=self.ids.keys()).with_rating_stats()
        products_by_id = {str(p.id): p for p in products}
        return [products_by_id[pid] for pid in self.ids if pid in products_by_id]


class _AccountProductIdSet:
    """A logged-in customer's list, stored in the database.

    Deliberately mirrors _ProductIdSet's interface, so views, templates and the
    context processor never have to know which kind of list they're dealing
    with. get_wishlist() / get_compare() is what picks between them.
    """

    model = None
    max_items = None

    def __init__(self, user):
        self.user = user
        self._ids = None

    @property
    def ids(self):
        """This list's product ids, as strings, in the order they were added.

        Loaded once per instance and reused, so asking for the ids, the count
        and whether one product is on the list costs a single query between
        them.
        """
        if self._ids is None:
            self._ids = {
                str(product_id): True
                for product_id in self._queryset().values_list('product_id', flat=True)
            }
        return self._ids

    def _invalidate(self):
        self._ids = None

    def contains(self, product_id):
        return str(product_id) in self.ids

    def toggle(self, product_id):
        existing = self._queryset().filter(product_id=product_id).first()
        if existing is not None:
            existing.delete()
            self._invalidate()
            return False

        if self.max_items is not None and self._queryset().count() >= self.max_items:
            # Meta.ordering is ['added_at', 'id'], which is what makes first()
            # the oldest entry — the same entry the session version evicted.
            oldest = self._queryset().first()
            if oldest is not None:
                oldest.delete()

        self.model.objects.create(user=self.user, product_id=product_id)
        self._invalidate()
        return True

    def add(self, product_id):
        """Put a product on the list if it isn't already, honouring max_items.

        Returns True if the list changed. Unlike toggle(), this never removes
        an existing entry — see the session version for why the merge needs it
        this way round.
        """
        if self.contains(product_id):
            return False
        if self.max_items is not None and len(self.ids) >= self.max_items:
            return False
        self.model.objects.create(user=self.user, product_id=product_id)
        self._invalidate()
        return True

    def remove(self, product_id):
        self._queryset().filter(product_id=product_id).delete()
        self._invalidate()

    def clear(self):
        self._queryset().delete()
        self._invalidate()

    def count(self):
        return len(self.ids)

    def products(self):
        ids = list(self.ids.keys())
        products = Product.objects.filter(id__in=ids).with_rating_stats()
        products_by_id = {str(p.id): p for p in products}
        return [products_by_id[pid] for pid in ids if pid in products_by_id]

    def _queryset(self):
        return self.model.objects.filter(user=self.user)


class Wishlist(_ProductIdSet):
    session_key = 'wishlist'
    max_items = None


class CompareList(_ProductIdSet):
    session_key = 'compare'
    max_items = 4


class AccountWishlist(_AccountProductIdSet):
    model = WishlistItem
    max_items = None


class AccountCompareList(_AccountProductIdSet):
    model = CompareItem
    max_items = 4


def get_wishlist(request):
    """The right wishlist for this request: database-backed once logged in."""
    return _for_request(request, 'wishlist', Wishlist, AccountWishlist)


def get_compare(request):
    """The right compare list for this request: database-backed once logged in."""
    return _for_request(request, 'compare', CompareList, AccountCompareList)


def _for_request(request, name, session_class, account_class):
    """Pick the session or account list, building an account list at most once per request.

    site_info (a context processor) runs on every page, and _card_context asks
    for both lists again on the pages that use it. Without this, one page
    render for a logged-in customer would build the same two account lists —
    and run their queries — two or three times over. The built object is parked
    on the request, which is exactly the lifetime wanted. A guest's list is a
    cheap read of an already-loaded session, so it isn't cached.
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return session_class(request)

    cache_attr = f'_shop_account_{name}'
    cached = getattr(request, cache_attr, None)
    if cached is None:
        cached = account_class(user)
        setattr(request, cache_attr, cached)
    return cached


def merge_session_lists_into_account(request, user):
    """Hand a guest's wishlist and compare list over to their account, on login.

    Connected to user_logged_in in signals.py, so it fires for every way
    someone can end up logged in — the login form, registration, the Django
    admin — without each having to remember to call it.

    The two lists merge by different rules, because they are different things:

      * **Wishlist** — a plain union. Everything the guest saved that the
        account doesn't already have is added; the list has no ceiling.
      * **Compare** — the account wins. Entries already on the account's list
        stay exactly where they are and are never displaced by a newer guest
        entry, and the guest's entries only fill whatever slots remain under
        the cap of 4.

    In both cases a product that is no longer listed (is_active=False) is left
    behind, and stock is deliberately ignored: an out-of-stock product is a
    perfectly reasonable thing to wishlist or compare.

    The session lists are emptied either way, so logging in twice can't apply
    the same guest list to the account a second time.

    Returns a small summary the caller could show the customer.
    """
    wishlist = _merge_one(request, user, Wishlist.session_key, AccountWishlist)
    compare = _merge_one(request, user, CompareList.session_key, AccountCompareList)

    for key in (Wishlist.session_key, CompareList.session_key):
        if key in request.session:
            # Assigning marks the session modified for us.
            request.session[key] = {}

    # Any account list already built for this request was read before the merge
    # and would hand back a stale view of it.
    for name in ('wishlist', 'compare'):
        attr = f'_shop_account_{name}'
        if hasattr(request, attr):
            delattr(request, attr)

    return {'wishlist': wishlist, 'compare': compare}


def _merge_one(request, user, session_key, account_class):
    """Move one guest list into the matching account list.

    'moved' counts entries that were added. 'skipped' counts the rest — ones
    the account already had, products that are no longer listed, and (for
    compare) ones that didn't fit under the cap.
    """
    session_ids = list((request.session.get(session_key) or {}).keys())
    if not session_ids:
        return {'moved': 0, 'skipped': 0}

    account = account_class(user)
    moved = 0
    skipped = 0

    for raw_product_id in session_ids:
        try:
            product_id = int(raw_product_id)
        except (TypeError, ValueError):
            skipped += 1
            continue

        # An unlisted product shouldn't arrive on a customer's list by way of a
        # merge. Stock is deliberately not part of this test.
        if not Product.objects.filter(id=product_id, is_active=True).exists():
            skipped += 1
            continue

        if account.add(product_id):
            moved += 1
        else:
            skipped += 1

    return {'moved': moved, 'skipped': skipped}
