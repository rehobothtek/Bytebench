from django.conf import settings
from django.db import models
from django.db.models import F
import uuid


class ProductQuerySet(models.QuerySet):
    def with_rating_stats(self):
        """Attach the approved-review count and average in the same query.

        A product card reads the rating percentage *and* the review count, and
        the detail page reads both again — and the helpers below all derive
        from the same pair of aggregate values. Without this, each listing row
        costs its own COUNT/AVG round trip, which on a grid of products is one
        extra query per card.

        Both annotations use the same filtered join, so they can be computed
        together without the row multiplication you'd get from aggregating two
        *different* multi-valued relations in one query.
        """
        return self.annotate(
            approved_review_count=models.Count(
                'reviews', filter=models.Q(reviews__is_approved=True)),
            approved_review_average=models.Avg(
                'reviews__rating', filter=models.Q(reviews__is_approved=True)),
        )


class Product(models.Model):
    CATEGORY_CHOICES = [
        ('phone', 'Phone'),
        ('laptop', 'Laptop'),
        ('accessory', 'Accessory'),
        ('gaming', 'Gaming'),
    ]
    CONDITION_CHOICES = [
        ('new', 'New'),
        ('uk', 'UK-Used'),
        ('ng', 'Nigerian-Used'),
    ]
    STOCK_CHOICES = [
        ('ok', 'In stock'),
        ('warn', 'Low stock'),
        ('low', 'Last unit'),
        ('out', 'Out of stock'),
    ]

    name = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    condition = models.CharField(max_length=10, choices=CONDITION_CHOICES, default='new')
    spec = models.CharField(max_length=255, blank=True, help_text="Short spec line, e.g. '128GB · Blue · Battery 89%'")
    price = models.DecimalField(max_digits=10, decimal_places=2, help_text="Price in Naira")
    compare_at_price = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True,
        help_text="Optional original price. If higher than price, a discount badge shows automatically.")
    stock_quantity = models.PositiveIntegerField(default=0,
        help_text="The real number of units you have. This is the source of truth — the badge below is set automatically from this number.")
    stock_status = models.CharField(max_length=10, choices=STOCK_CHOICES, default='out', editable=False,
        help_text="Set automatically from the quantity above — not editable directly.")
    image = models.ImageField(upload_to='products/', blank=True, null=True,
        help_text="Product photo. If left empty, the emoji below is shown instead.")
    emoji = models.CharField(max_length=10, default='📱', help_text="Fallback thumbnail when no photo is uploaded")
    is_active = models.BooleanField(default=True, help_text="Untick to hide this product from the shop")
    is_featured = models.BooleanField(default=False, help_text="Show in the homepage Featured row")
    is_bestseller = models.BooleanField(default=False, help_text="Show in the homepage Best Sellers row")
    created_at = models.DateTimeField(auto_now_add=True)

    # The custom queryset adds with_rating_stats(); everything else behaves
    # exactly like the default manager.
    objects = ProductQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # stock_status is always derived from stock_quantity, so the two can
        # never drift out of sync the way a manually-set label could.
        if self.stock_quantity <= 0:
            self.stock_status = 'out'
        elif self.stock_quantity <= 2:
            self.stock_status = 'low'
        elif self.stock_quantity <= 5:
            self.stock_status = 'warn'
        else:
            self.stock_status = 'ok'
        super().save(*args, **kwargs)

    def is_in_stock(self):
        return self.stock_quantity > 0

    def max_purchasable(self, already_in_cart=0):
        """How many more of this product can still be added, given what's already in someone's cart."""
        return max(0, self.stock_quantity - already_in_cart)

    @staticmethod
    def reserve_stock(product_id, quantity):
        """
        Atomically decrements stock by `quantity`, but ONLY if at least that
        much is available — in a single database UPDATE. This is what
        actually prevents overselling under concurrent checkouts: two
        requests racing for the last unit can't both succeed, because the
        database itself enforces the `stock_quantity >= quantity` condition
        at the moment of the write, not in application code beforehand.
        Returns True if the reservation succeeded, False if there wasn't
        enough stock left.
        """
        updated = Product.objects.filter(
            id=product_id, stock_quantity__gte=quantity
        ).update(stock_quantity=F('stock_quantity') - quantity)
        if updated:
            # keep the derived status badge in sync (update() bypasses save())
            product = Product.objects.get(id=product_id)
            Product.objects.filter(id=product_id).update(stock_status=product._derive_stock_status())
        return updated == 1

    def _derive_stock_status(self):
        if self.stock_quantity <= 0:
            return 'out'
        elif self.stock_quantity <= 2:
            return 'low'
        elif self.stock_quantity <= 5:
            return 'warn'
        return 'ok'

    def discount_percent(self):
        if self.compare_at_price and self.compare_at_price > self.price:
            return round((1 - (self.price / self.compare_at_price)) * 100)
        return 0

    def approved_reviews(self):
        return self.reviews.filter(is_approved=True)

    def _rating_stats(self):
        """(number of approved reviews, their average rating).

        Two sources, in order:

          1. The queryset annotations from with_rating_stats(), when this
             product was loaded through them. Costs nothing.
          2. Otherwise one aggregate query, cached on the instance so that the
             several reads below (a card asks for the percentage *and* the
             count, twice over) still cost exactly one query, not one each.

        This is the only place either number is fetched, so average_rating(),
        review_count() and rating_percent() cannot drift apart or re-query.
        """
        if hasattr(self, 'approved_review_count') and hasattr(self, 'approved_review_average'):
            return self.approved_review_count, self.approved_review_average or 0

        cached = self.__dict__.get('_rating_stats_cache')
        if cached is None:
            summary = self.approved_reviews().aggregate(
                count=models.Count('id'), average=models.Avg('rating'))
            cached = (summary['count'], summary['average'] or 0)
            self.__dict__['_rating_stats_cache'] = cached
        return cached

    def average_rating(self):
        return self._rating_stats()[1]

    def review_count(self):
        return self._rating_stats()[0]

    def rating_percent(self):
        return round((self.average_rating() / 5) * 100)


class RepairType(models.Model):
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255)
    price_range = models.CharField(max_length=100, help_text="e.g. '₦25,000 – ₦120,000'")
    time_estimate = models.CharField(max_length=100, help_text="e.g. '2–4 hrs'")
    icon = models.CharField(max_length=10, default='🔧', help_text="Emoji shown on the repair card")
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.name


class RepairBooking(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In progress'),
        ('done', 'Done'),
        ('cancelled', 'Cancelled'),
    ]
    ticket_number = models.CharField(max_length=20, unique=True, blank=True)
    access_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True,
        help_text="Random token used in confirmation URLs so booking pages can't be guessed by ticket number alone")
    customer_name = models.CharField(max_length=150)
    phone_number = models.CharField(max_length=30)
    device = models.CharField(max_length=150)
    repair_type = models.ForeignKey(RepairType, on_delete=models.SET_NULL, null=True)
    issue_description = models.TextField(blank=True)
    preferred_time = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.ticket_number:
            last = RepairBooking.objects.order_by('id').last()
            next_id = (last.id + 1) if last else 1
            self.ticket_number = f'RBT-{1000 + next_id}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.ticket_number} — {self.customer_name}'


class Order(models.Model):
    STATUS_CHOICES = [
        ('new', 'New'),
        ('confirmed', 'Confirmed'),
        ('fulfilled', 'Fulfilled'),
        ('cancelled', 'Cancelled'),
    ]
    PAYMENT_METHOD_CHOICES = [
        ('online', 'Pay online now (card / bank transfer / USSD)'),
        ('delivery', 'Pay on delivery / pickup'),
    ]
    PAYMENT_STATUS_CHOICES = [
        ('unpaid', 'Unpaid'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='orders',
        help_text="Set automatically for logged-in customers. Left empty for guest checkout — guest orders stay "
                  "reachable through their confirmation link and the Track page, exactly as before.")
    customer_name = models.CharField(max_length=150)
    access_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True,
        help_text="Random token used in order URLs so one customer can't view another's order by guessing IDs")
    email = models.EmailField(help_text="Required by Paystack for online payments, and useful for receipts")
    phone_number = models.CharField(max_length=30)
    delivery_address = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='delivery')
    payment_status = models.CharField(max_length=20, choices=PAYMENT_STATUS_CHOICES, default='unpaid')
    payment_reference = models.CharField(max_length=100, blank=True, help_text="Paystack transaction reference")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def total(self):
        return sum(item.subtotal() for item in self.items.all())

    def __str__(self):
        return f'Order #{self.id} — {self.customer_name}'


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=200)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    def subtotal(self):
        return self.price * self.quantity

    def __str__(self):
        return f'{self.product_name} x{self.quantity}'


class CustomerProfile(models.Model):
    """
    Reusable details for a logged-in customer, so a returning shopper doesn't
    retype their name, phone and address at every checkout.

    Kept deliberately separate from Order: an Order stores its own snapshot of
    these same values, so editing this profile later never rewrites the
    details of an order that has already been placed.
    """
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    default_name = models.CharField(max_length=150, blank=True)
    default_phone = models.CharField(max_length=30, blank=True)
    default_email = models.EmailField(blank=True,
        help_text="Used to prefill checkout and receipts. This is separate from the account's login details.")
    delivery_address = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Profile — {self.user.get_username()}'

    @classmethod
    def for_user(cls, user):
        """Always hands back a profile, creating an empty one on first use."""
        profile, _ = cls.objects.get_or_create(user=user)
        return profile

    def prefill(self):
        """Initial values for the checkout form, falling back to the account itself."""
        return {
            'customer_name': self.default_name or self.user.get_full_name() or self.user.get_username(),
            'email': self.default_email or self.user.email,
            'phone_number': self.default_phone,
            'delivery_address': self.delivery_address,
        }

    def is_empty(self):
        return not any([self.default_name, self.default_phone, self.default_email, self.delivery_address])


class CartItem(models.Model):
    """
    The cart for a logged-in customer, stored in the database.

    Guests keep using the session-backed Cart in shop/cart.py; this is what
    lets an account holder's cart survive logging out, closing the browser,
    or coming back on a different device. A deleted product takes its cart
    rows with it (CASCADE) — unlike OrderItem, a cart line is not a record
    worth preserving.
    """
    MAX_PER_ITEM = 20  # same per-line ceiling the Add-to-cart form enforces

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='cart_items')
    quantity = models.PositiveIntegerField(default=1)
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['added_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'product'], name='unique_cart_item_per_user_and_product'),
            models.CheckConstraint(check=models.Q(quantity__gte=1), name='cart_item_quantity_at_least_one'),
        ]

    def __str__(self):
        return f'{self.product} x{self.quantity} ({self.user.get_username()})'

    def subtotal(self):
        return self.product.price * self.quantity


class WishlistItem(models.Model):
    """A product saved to a logged-in customer's wishlist.

    Guests keep their wishlist in the session (see shop/session_lists.py). This
    is what lets an account holder's saved items survive logging out — which a
    session-backed list cannot do, because django.contrib.auth.logout() flushes
    the entire session.

    A deleted product takes its rows with it (CASCADE), matching CartItem: a
    saved line for something that no longer exists is not worth preserving.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='wishlist_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
        related_name='wishlisted_by')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Added order. This is the order the list is displayed and read back
        # in, and mirrors the insertion order the session-backed version got
        # for free from its dict.
        ordering = ['added_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'product'],
                name='unique_wishlist_item_per_user_and_product'),
        ]

    def __str__(self):
        return f'{self.product} — {self.user.get_username()}'


class CompareItem(models.Model):
    """A product on a logged-in customer's compare list.

    Deliberately the same shape as WishlistItem rather than one model with a
    kind flag: the compare list is capped at 4 and evicts its oldest entry,
    which is real behaviour the wishlist does not have.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name='compare_items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE,
        related_name='compared_by')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # See WishlistItem: added order is what makes "the oldest entry" mean
        # the same thing here as it did in the session-backed version.
        ordering = ['added_at', 'id']
        constraints = [
            models.UniqueConstraint(fields=['user', 'product'],
                name='unique_compare_item_per_user_and_product'),
        ]

    def __str__(self):
        return f'{self.product} — {self.user.get_username()}'


class ContactMessage(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True)
    phone_number = models.CharField(max_length=30, blank=True)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.name} — {self.created_at:%Y-%m-%d}'


class Review(models.Model):
    RATING_CHOICES = [(i, f'{i} star{"s" if i != 1 else ""}') for i in range(1, 6)]

    product = models.ForeignKey(Product, related_name='reviews', on_delete=models.CASCADE)
    reviewer_name = models.CharField(max_length=150)
    rating = models.PositiveSmallIntegerField(choices=RATING_CHOICES)
    title = models.CharField(max_length=150, blank=True)
    body = models.TextField(blank=True)
    verified_purchase = models.BooleanField(default=False, help_text="Tick only if you've confirmed this customer actually bought the item")
    is_approved = models.BooleanField(default=False, help_text="Only approved reviews are shown publicly")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.reviewer_name} — {self.rating}★ on {self.product}'


class Testimonial(models.Model):
    customer_name = models.CharField(max_length=150)
    customer_location = models.CharField(max_length=100, blank=True, help_text="e.g. 'Makurdi, Benue'")
    quote = models.TextField()
    rating = models.PositiveSmallIntegerField(default=5, choices=[(i, str(i)) for i in range(1, 6)])
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', '-created_at']

    def __str__(self):
        return f'{self.customer_name} ({self.rating}★)'


class FAQ(models.Model):
    question = models.CharField(max_length=255)
    answer = models.TextField()
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'id']
        verbose_name = 'FAQ'
        verbose_name_plural = 'FAQs'

    def __str__(self):
        return self.question
