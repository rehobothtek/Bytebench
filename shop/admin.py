import logging

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Product, RepairType, RepairBooking, Order, OrderItem, ContactMessage,
    Review, Testimonial, FAQ, CustomerProfile, CartItem, WishlistItem, CompareItem,
)

# Admin activity is logged under the app's own logger (see LOGGING in
# config/settings.py), which is configured out of the box — so a status change
# has somewhere to land without any new model or migration. Shop events and
# admin events then appear in the same stream.
logger = logging.getLogger('shop.admin')

admin.site.site_header = 'RehoBothTek Admin'
admin.site.site_title = 'RehoBothTek Admin'
admin.site.index_title = 'Manage stock, repairs & orders'


class ReviewInline(admin.TabularInline):
    model = Review
    extra = 0
    fields = ('reviewer_name', 'rating', 'title', 'is_approved', 'verified_purchase', 'created_at')
    readonly_fields = ('created_at',)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('thumbnail', 'name', 'category', 'condition', 'price', 'compare_at_price',
                     'stock_quantity', 'stock_status', 'is_active', 'is_featured', 'is_bestseller')
    list_display_links = ('name',)
    list_filter = ('category', 'condition', 'stock_status', 'is_active', 'is_featured', 'is_bestseller')
    search_fields = ('name', 'spec')
    list_editable = ('stock_quantity', 'is_active', 'is_featured', 'is_bestseller', 'compare_at_price')
    readonly_fields = ('thumbnail_large', 'stock_status')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    # Same fourteen fields as before, grouped so the two things an owner
    # actually changes day to day — the shelf count and the price — are not
    # buried in a single column of every field the model has.
    fieldsets = (
        ('Product', {
            'fields': ('name', 'category', 'condition', 'spec', 'emoji'),
        }),
        ('Pricing', {
            'fields': ('price', 'compare_at_price'),
            'description': 'Set an original price higher than the price to show a discount badge automatically.',
        }),
        ('Stock', {
            'fields': ('stock_quantity', 'stock_status'),
            'description': 'The quantity is the source of truth — the status badge is derived from it and is not editable.',
        }),
        ('Photo', {
            'fields': ('image', 'thumbnail_large'),
            'description': 'Leave the photo empty and the emoji above is shown instead.',
        }),
        ('Where it appears', {
            'fields': ('is_active', 'is_featured', 'is_bestseller'),
        }),
    )
    inlines = [ReviewInline]

    def thumbnail(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="width:40px;height:40px;object-fit:cover;border-radius:6px;">', obj.image.url)
        return obj.emoji

    def thumbnail_large(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-width:240px;border-radius:8px;">', obj.image.url)
        return '(no photo uploaded yet — the emoji will be shown instead)'
    thumbnail_large.short_description = 'Preview'


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ('product', 'reviewer_name', 'rating', 'is_approved', 'verified_purchase', 'created_at')
    list_filter = ('is_approved', 'verified_purchase', 'rating')
    search_fields = ('reviewer_name', 'title', 'body', 'product__name')
    list_editable = ('is_approved',)
    # Without this the changelist runs one query per row to print the product.
    list_select_related = ('product',)
    readonly_fields = ('created_at',)
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    actions = ('approve_reviews',)

    @admin.action(description='Approve selected reviews (publish them on the site)')
    def approve_reviews(self, request, queryset):
        """Only ever sets is_approved — approval is the one thing that decides
        whether a review is public, and this action must not be able to touch
        anything else about a review."""
        approved = queryset.filter(is_approved=False).update(is_approved=True)
        self.message_user(
            request,
            f'{approved} review(s) approved and now visible on the site.'
            if approved else 'Those reviews were already approved.',
        )


@admin.register(Testimonial)
class TestimonialAdmin(admin.ModelAdmin):
    list_display = ('customer_name', 'customer_location', 'rating', 'is_active', 'display_order')
    list_editable = ('is_active', 'display_order')
    list_filter = ('is_active', 'rating')
    search_fields = ('customer_name', 'customer_location', 'quote')
    ordering = ('display_order', '-created_at')


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ('question', 'display_order', 'is_active')
    list_editable = ('display_order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('question', 'answer')
    ordering = ('display_order', 'id')


@admin.register(RepairType)
class RepairTypeAdmin(admin.ModelAdmin):
    list_display = ('icon', 'name', 'price_range', 'time_estimate', 'is_active', 'display_order')
    list_editable = ('display_order', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('name', 'description')
    ordering = ('display_order', 'id')


@admin.register(RepairBooking)
class RepairBookingAdmin(admin.ModelAdmin):
    list_display = ('ticket_number', 'customer_name', 'phone_number', 'device',
                    'repair_type', 'status', 'created_at')
    list_filter = ('status', 'repair_type')
    search_fields = ('ticket_number', 'customer_name', 'phone_number', 'device', 'issue_description')
    list_editable = ('status',)
    # Prints the repair type for every row from the join rather than per row.
    list_select_related = ('repair_type',)
    readonly_fields = ('ticket_number', 'created_at', 'confirmation_link')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    fieldsets = (
        ('Ticket', {
            'fields': ('ticket_number', 'status', 'created_at', 'confirmation_link'),
            'description': "The confirmation link is what the customer was given — use it to resend "
                           "their ticket page if they lose it.",
        }),
        ('Customer', {
            'fields': ('customer_name', 'phone_number'),
        }),
        ('Job', {
            'fields': ('device', 'repair_type', 'issue_description', 'preferred_time'),
        }),
    )

    def confirmation_link(self, obj):
        if not obj.pk:
            return '(save first)'
        path = f'/repairs/confirmation/{obj.ticket_number}/{obj.access_token}/'
        return format_html('<a href="{0}" target="_blank">{0}</a>', path)
    confirmation_link.short_description = 'Customer confirmation link'


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product_name', 'price', 'quantity')
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'user', 'phone_number', 'payment_method',
                    'payment_status', 'status', 'total_display', 'created_at')
    list_filter = ('status', 'payment_method', 'payment_status')
    search_fields = ('customer_name', 'phone_number', 'email', 'payment_reference',
                     'user__username', 'user__email', '=id')
    list_editable = ('status', 'payment_status')
    list_select_related = ('user',)
    readonly_fields = ('payment_reference', 'created_at', 'total_display', 'confirmation_link')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    inlines = [OrderItemInline]
    fieldsets = (
        ('Customer', {
            'fields': ('user', 'customer_name', 'email', 'phone_number', 'delivery_address'),
            'description': 'Guest orders leave the user blank — they stay reachable through the '
                           "customer's confirmation link and the Track page, exactly as before.",
        }),
        ('Payment', {
            'fields': ('payment_method', 'payment_status', 'payment_reference'),
            'description': 'The reference is what Paystack recorded and is set by the payment flow, '
                           'not by hand.',
        }),
        ('Fulfilment', {
            'fields': ('status', 'created_at', 'total_display', 'confirmation_link'),
        }),
    )

    def get_queryset(self, request):
        """Prefetch the lines, because the Total column is derived from them.

        Order.total() sums its items rather than reading a stored column (it is
        deliberately not denormalised), so without this the changelist runs one
        extra query per order just to print a figure. The total itself is
        unchanged — same items, same arithmetic, one query instead of N.
        """
        return super().get_queryset(request).prefetch_related('items')

    def total_display(self, obj):
        return f'₦{obj.total():.0f}'
    total_display.short_description = 'Total'

    def save_model(self, request, obj, form, change):
        """Log which admin changed an order's status, and what it changed from.

        list_editable lets status and payment status be changed straight from
        the changelist, which is convenient and stays — but it leaves no trace
        of who did it or what the previous value was. This writes that to the
        app's log. It names only the order, the two states and the admin's
        username: no customer details, no reference, no token.
        """
        if change and obj.pk:
            previous = Order.objects.filter(pk=obj.pk).values('status', 'payment_status').first()
            if previous:
                changes = [
                    f'{field}: {previous[field]} -> {getattr(obj, field)}'
                    for field in ('status', 'payment_status')
                    if previous[field] != getattr(obj, field)
                ]
                if changes:
                    logger.info('Order #%s changed by %s — %s',
                                obj.pk, request.user.get_username(), '; '.join(changes))
        super().save_model(request, obj, form, change)

    def confirmation_link(self, obj):
        if not obj.pk:
            return '(save first)'
        path = f'/order/{obj.id}/{obj.access_token}/confirmation/'
        return format_html('<a href="{0}" target="_blank">{0}</a>', path)
    confirmation_link.short_description = 'Customer confirmation link'


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    """Read-only, so a line can be found and inspected without any way to
    change or delete it — order lines are the record a total is derived from,
    and the Order page is where they are edited (and there, not at all)."""
    list_display = ('id', 'order', 'product_name', 'quantity', 'price', 'subtotal_display')
    search_fields = ('product_name', 'order__customer_name', '=order__id')
    list_filter = ('order__payment_status',)
    list_select_related = ('order', 'product')
    ordering = ('-id',)

    def subtotal_display(self, obj):
        return f'₦{obj.subtotal():.0f}'
    subtotal_display.short_description = 'Subtotal'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CustomerProfile)
class CustomerProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'default_name', 'default_phone', 'default_email',
                    'delivery_address', 'updated_at')
    search_fields = ('user__username', 'user__email', 'default_name', 'default_phone', 'default_email')
    readonly_fields = ('created_at', 'updated_at')
    # Prints the account for every row from the join rather than per row.
    list_select_related = ('user',)
    ordering = ('-updated_at',)
    fieldsets = (
        ('Account', {
            'fields': ('user',),
            'description': 'The login itself is managed on the user page — nothing here changes it.',
        }),
        ('Defaults used at checkout', {
            'fields': ('default_name', 'default_phone', 'default_email', 'delivery_address'),
            'description': "These only prefill the checkout form. Every order keeps the details it "
                           "was actually placed with, so editing these never rewrites an order.",
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
        }),
    )


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'quantity', 'subtotal_display', 'updated_at')
    search_fields = ('user__username', 'user__email', 'product__name')
    readonly_fields = ('added_at', 'updated_at')
    # Two foreign keys are printed per row, so both joins are needed.
    list_select_related = ('user', 'product')
    ordering = ('-updated_at',)
    # Deliberately no bulk actions: a cart line is a customer's own pending
    # basket, and "empty every cart" is not something to put one click away.

    def subtotal_display(self, obj):
        return f'₦{obj.subtotal():.0f}'
    subtotal_display.short_description = 'Basket value'


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'added_at')
    search_fields = ('user__username', 'user__email', 'product__name')
    readonly_fields = ('added_at',)
    # Two foreign keys are printed per row, so both joins are needed.
    list_select_related = ('user', 'product')
    ordering = ('-added_at',)
    # No bulk actions and nothing editable, for the same reason CartItemAdmin
    # has none: this list belongs to the customer, not to us.


@admin.register(CompareItem)
class CompareItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'added_at')
    search_fields = ('user__username', 'user__email', 'product__name')
    readonly_fields = ('added_at',)
    list_select_related = ('user', 'product')
    ordering = ('-added_at',)


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'phone_number', 'message_preview', 'is_read', 'created_at')
    list_filter = ('is_read',)
    search_fields = ('name', 'email', 'phone_number', 'message')
    list_editable = ('is_read',)
    readonly_fields = ('name', 'email', 'phone_number', 'message', 'created_at')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    fieldsets = (
        ('Message', {
            'fields': ('name', 'email', 'phone_number', 'message', 'created_at'),
            'description': 'What the customer submitted — kept read-only so the record of it cannot '
                           'be edited after the fact.',
        }),
        ('Follow-up', {
            'fields': ('is_read',),
            'description': 'Tick when you have dealt with it.',
        }),
    )

    def message_preview(self, obj):
        text = ' '.join(obj.message.split())
        return text[:70] + ('…' if len(text) > 70 else '')
    message_preview.short_description = 'Message'
