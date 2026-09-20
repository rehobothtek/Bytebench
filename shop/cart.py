from django.db.models import Sum

from .models import Product, CartItem

SESSION_KEY = 'cart'


class Cart:
    """The cart for someone who isn't logged in — stored in their session.

    This is the original cart, unchanged: a guest can fill a cart without
    having an account, and it follows their browser cookie until they either
    check out, or log in (at which point merge_session_cart_into_account()
    hands its contents over to their account cart).
    """

    def __init__(self, request):
        self.session = request.session
        cart = self.session.get(SESSION_KEY)
        if cart is None:
            cart = self.session[SESSION_KEY] = {}
        self.cart = cart

    def add(self, product_id, quantity=1):
        product_id = str(product_id)
        self.cart[product_id] = self.cart.get(product_id, 0) + quantity
        self.save()

    def set_quantity(self, product_id, quantity):
        product_id = str(product_id)
        if quantity <= 0:
            self.cart.pop(product_id, None)
        else:
            self.cart[product_id] = quantity
        self.save()

    def remove(self, product_id):
        self.cart.pop(str(product_id), None)
        self.save()

    def clear(self):
        self.session[SESSION_KEY] = {}
        self.save()

    def save(self):
        self.session[SESSION_KEY] = self.cart
        self.session.modified = True

    def quantity_of(self, product_id):
        return self.cart.get(str(product_id), 0)

    def items(self):
        product_ids = self.cart.keys()
        products = Product.objects.filter(id__in=product_ids)
        products_by_id = {str(p.id): p for p in products}
        result = []
        for product_id, quantity in self.cart.items():
            product = products_by_id.get(product_id)
            if not product:
                continue
            result.append({'product': product, 'quantity': quantity, 'subtotal': product.price * quantity})
        return result

    def total(self):
        return sum(item['subtotal'] for item in self.items())

    def count(self):
        return sum(self.cart.values())


class DBCart:
    """The cart for a logged-in customer — stored in the database.

    Deliberately mirrors the session Cart's interface (add, set_quantity,
    remove, clear, items, total, count, quantity_of) so that views, templates
    and the context processor never have to know which kind of cart they're
    dealing with. get_cart() is what picks between them.
    """

    def __init__(self, user):
        self.user = user

    def add(self, product_id, quantity=1):
        item = self._queryset().filter(product_id=product_id).first()
        if item:
            item.quantity += quantity
            item.save(update_fields=['quantity', 'updated_at'])
        else:
            CartItem.objects.create(user=self.user, product_id=product_id, quantity=quantity)

    def set_quantity(self, product_id, quantity):
        if quantity <= 0:
            self.remove(product_id)
            return
        CartItem.objects.update_or_create(
            user=self.user, product_id=product_id, defaults={'quantity': quantity}
        )

    def remove(self, product_id):
        self._queryset().filter(product_id=product_id).delete()

    def clear(self):
        self._queryset().delete()

    def quantity_of(self, product_id):
        item = self._queryset().filter(product_id=product_id).values_list('quantity', flat=True).first()
        return item or 0

    def items(self):
        result = []
        for item in self._queryset().select_related('product'):
            product = item.product
            result.append({'product': product, 'quantity': item.quantity, 'subtotal': product.price * item.quantity})
        return result

    def total(self):
        return sum(item['subtotal'] for item in self.items())

    def count(self):
        return self._queryset().aggregate(n=Sum('quantity'))['n'] or 0

    def _queryset(self):
        return CartItem.objects.filter(user=self.user)


def get_cart(request):
    """The right cart for this request: database-backed once logged in, session-backed for guests."""
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return DBCart(user)
    return Cart(request)


def count_cart_items(request):
    """Just the badge number — without creating a session for a visitor who doesn't have one yet."""
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated:
        return DBCart(user).count()
    return sum((request.session.get(SESSION_KEY) or {}).values())


def merge_session_cart_into_account(request, user):
    """Hands a guest's session cart over to their account cart, on login or registration.

    Quantities for a product already in the account cart are added together,
    then trimmed to what can actually be bought right now — the same stock
    ceiling and 20-per-line maximum the Add-to-cart button enforces. Products
    that have since been deleted or sold out are left behind, matching how the
    session cart already ignores lines it can no longer resolve.

    The session cart is emptied either way, so logging in twice can't double
    anything up. Returns a small summary the caller can show the customer.
    """
    session_cart = request.session.get(SESSION_KEY) or {}
    if not session_cart:
        return {'moved': 0, 'skipped': 0}

    moved = 0
    skipped = 0
    for raw_product_id, session_quantity in session_cart.items():
        try:
            product_id = int(raw_product_id)
            session_quantity = int(session_quantity)
        except (TypeError, ValueError):
            continue

        product = Product.objects.filter(id=product_id, is_active=True).first()
        if not product or not product.is_in_stock() or session_quantity <= 0:
            skipped += 1
            continue

        item = CartItem.objects.filter(user=user, product=product).first()
        already = item.quantity if item else 0
        room = min(product.max_purchasable(already), CartItem.MAX_PER_ITEM - already)
        if room <= 0:
            skipped += 1
            continue

        quantity = min(session_quantity, room)
        if item:
            item.quantity = already + quantity
            item.save(update_fields=['quantity', 'updated_at'])
        else:
            CartItem.objects.create(user=user, product=product, quantity=quantity)
        moved += 1

    request.session[SESSION_KEY] = {}
    request.session.modified = True
    return {'moved': moved, 'skipped': skipped}
