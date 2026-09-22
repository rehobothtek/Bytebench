from urllib.parse import quote

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import (Product, RepairType, Order, OrderItem, RepairBooking, ContactMessage, Review,
                     Testimonial, FAQ, CustomerProfile)
from .forms import (RepairBookingForm, CheckoutForm, ContactForm, TrackForm, ReviewForm,
                    RegisterForm, CustomerProfileForm)
from .cart import get_cart
from .session_lists import get_wishlist, get_compare
from .notifications import notify_new_order, notify_new_repair_booking, notify_new_contact_message, notify_oversold_order
from .paystack import verify_transaction, PaystackVerificationError
import re
import secrets

RECENTLY_VIEWED_SESSION_KEY = 'recently_viewed'
RECENTLY_VIEWED_MAX = 8


def whatsapp_link(message: str) -> str:
    return f'https://wa.me/{settings.WHATSAPP_NUMBER}?text={quote(message)}'


def _card_context(request):
    return {
        'wishlist_ids': {int(pid) for pid in get_wishlist(request).ids.keys()},
        'compare_ids': {int(pid) for pid in get_compare(request).ids.keys()},
    }


# --- Home ---------------------------------------------------------------------

def home(request):
    # with_rating_stats() carries each card's review count and average along
    # with the products, so a grid of them is one query instead of one per card.
    featured = Product.objects.filter(is_active=True, is_featured=True).with_rating_stats()[:6]
    if not featured:
        featured = Product.objects.filter(is_active=True).with_rating_stats()[:6]
    bestsellers = Product.objects.filter(is_active=True, is_bestseller=True).with_rating_stats()[:6]
    new_arrivals = Product.objects.filter(is_active=True).order_by('-created_at').with_rating_stats()[:6]
    repair_types = RepairType.objects.filter(is_active=True)[:4]
    testimonials = Testimonial.objects.filter(is_active=True)[:6]
    faqs = FAQ.objects.filter(is_active=True)

    return render(request, 'shop/home.html', {
        'featured': featured, 'bestsellers': bestsellers, 'new_arrivals': new_arrivals,
        'repair_types': repair_types, 'testimonials': testimonials, 'faqs': faqs,
        **_card_context(request),
    })


# --- Shop / products ------------------------------------------------------------

def shop(request):
    category = request.GET.get('category', 'all')
    query = request.GET.get('q', '').strip()
    products = Product.objects.filter(is_active=True)
    if category != 'all':
        products = products.filter(category=category)
    if query:
        products = products.filter(name__icontains=query) | products.filter(spec__icontains=query)
    # Annotated last, on the combined queryset — attaching it to either side of
    # the OR above would not carry across.
    products = products.with_rating_stats()
    return render(request, 'shop/shop.html', {
        'products': products, 'active_category': category, 'query': query,
        **_card_context(request),
    })


def search_suggestions(request):
    query = request.GET.get('q', '').strip()
    results = []
    if len(query) >= 2:
        products = Product.objects.filter(is_active=True, name__icontains=query)[:6]
        results = [
            {'name': p.name, 'price': f'₦{p.price:,.0f}', 'url': f'/product/{p.id}/',
             'image': p.image.url if p.image else '', 'emoji': p.emoji}
            for p in products
        ]
    return JsonResponse({'results': results})


def _track_recently_viewed(request, product_id):
    seen = request.session.get(RECENTLY_VIEWED_SESSION_KEY, [])
    product_id = str(product_id)
    seen = [pid for pid in seen if pid != product_id]
    seen.insert(0, product_id)
    request.session[RECENTLY_VIEWED_SESSION_KEY] = seen[:RECENTLY_VIEWED_MAX]
    request.session.modified = True


def _product_jsonld(request, product):
    """schema.org Product data for the page's structured-data block.

    Built entirely from fields the product already has. Two deliberate
    omissions:

      - No aggregateRating. The star rating shown on the page is real, but the
        reviews seeded with the project are explicitly marked sample content in
        the README, and republishing those numbers as structured data would put
        ratings in front of search engines that nobody can stand behind. Add
        this once there are genuine reviews.
      - No brand, SKU, or GTIN: Product has no such fields, and inventing them
        is worse than omitting them.
    """
    url = request.build_absolute_uri(request.path)
    data = {
        '@context': 'https://schema.org',
        '@type': 'Product',
        'name': product.name,
        'url': url,
        'category': product.get_category_display(),
        'offers': {
            '@type': 'Offer',
            'url': url,
            'price': f'{product.price:.2f}',
            'priceCurrency': 'NGN',
            'availability': (
                'https://schema.org/InStock' if product.is_in_stock()
                else 'https://schema.org/OutOfStock'
            ),
        },
    }
    if product.spec:
        data['description'] = product.spec
    if product.image:
        data['image'] = request.build_absolute_uri(product.image.url)
    return data


def product_detail(request, product_id):
    product = get_object_or_404(Product.objects.with_rating_stats(), id=product_id, is_active=True)

    if request.method == 'POST':
        form = ReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.product = product
            review.save()
            messages.success(request, "Thanks for your review — it'll appear once we've checked it over.")
            return redirect('shop:product_detail', product_id=product.id)
    else:
        form = ReviewForm()

    related = Product.objects.filter(category=product.category, is_active=True).exclude(id=product.id).with_rating_stats()[:4]

    recently_viewed_ids = [pid for pid in request.session.get(RECENTLY_VIEWED_SESSION_KEY, []) if pid != str(product.id)]
    recently_viewed_map = {str(p.id): p for p in Product.objects.filter(id__in=recently_viewed_ids, is_active=True).with_rating_stats()}
    recently_viewed = [recently_viewed_map[pid] for pid in recently_viewed_ids if pid in recently_viewed_map][:4]

    _track_recently_viewed(request, product.id)

    return render(request, 'shop/product_detail.html', {
        'product': product, 'related': related, 'recently_viewed': recently_viewed,
        'reviews': product.approved_reviews(), 'review_form': form,
        'in_wishlist': get_wishlist(request).contains(product.id),
        'in_compare': get_compare(request).contains(product.id),
        'product_jsonld': _product_jsonld(request, product),
        **_card_context(request),
    })


# --- Wishlist --------------------------------------------------------------------

@require_POST
def wishlist_toggle(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    wishlist = get_wishlist(request)
    added = wishlist.toggle(product_id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'added': added, 'wishlist_count': wishlist.count()})
    messages.success(request, f'{"Added" if added else "Removed"} {product.name} {"to" if added else "from"} your wishlist.')
    return redirect(request.META.get('HTTP_REFERER', 'shop:home'))


def wishlist_detail(request):
    wishlist = get_wishlist(request)
    return render(request, 'shop/wishlist.html', {'products': wishlist.products(), **_card_context(request)})


# --- Compare -----------------------------------------------------------------------

@require_POST
def compare_toggle(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    compare = get_compare(request)
    added = compare.toggle(product_id)
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True, 'added': added, 'compare_count': compare.count()})
    messages.success(request, f'{"Added" if added else "Removed"} {product.name} {"to" if added else "from"} comparison.')
    return redirect(request.META.get('HTTP_REFERER', 'shop:home'))


def compare_detail(request):
    compare = get_compare(request)
    return render(request, 'shop/compare.html', {'products': compare.products(), **_card_context(request)})


# --- Cart ------------------------------------------------------------------------

@require_POST
def cart_add(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)
    try:
        requested = max(1, min(20, int(request.POST.get('quantity', 1))))
    except (TypeError, ValueError):
        requested = 1

    cart = get_cart(request)
    already_in_cart = cart.quantity_of(product_id)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

    if not product.is_in_stock():
        error = f'{product.name} is currently out of stock.'
        if is_ajax:
            return JsonResponse({'ok': False, 'error': error}, status=400)
        messages.error(request, error)
        return redirect(request.META.get('HTTP_REFERER', 'shop:home'))

    available = product.max_purchasable(already_in_cart)
    if available <= 0:
        error = f"You already have all {product.stock_quantity} available of {product.name} in your cart."
        if is_ajax:
            return JsonResponse({'ok': False, 'error': error}, status=400)
        messages.error(request, error)
        return redirect(request.META.get('HTTP_REFERER', 'shop:home'))

    quantity = min(requested, available)
    capped = quantity < requested
    cart.add(product_id, quantity)

    if is_ajax:
        payload = {'ok': True, 'cart_count': cart.count(), 'product_name': product.name}
        if capped:
            payload['warning'] = f'Only {available} of {product.name} available — added what we could.'
        return JsonResponse(payload)

    if capped:
        messages.warning(request, f'Only {available} of {product.name} available — added what we could.')
    else:
        messages.success(request, f'Added {product.name} to your cart.')
    return redirect(request.META.get('HTTP_REFERER', 'shop:home'))


@require_POST
def cart_update(request, product_id):
    action = request.POST.get('action')
    cart = get_cart(request)
    current = cart.quantity_of(product_id)
    if action == 'increase':
        product = Product.objects.filter(id=product_id).first()
        if product and current + 1 > product.stock_quantity:
            messages.error(request, f'Only {product.stock_quantity} of {product.name} available.')
        else:
            cart.set_quantity(product_id, current + 1)
    elif action == 'decrease':
        cart.set_quantity(product_id, current - 1)
    elif action == 'remove':
        cart.remove(product_id)
    return redirect('shop:cart_detail')


def cart_detail(request):
    cart = get_cart(request)
    return render(request, 'shop/cart.html', {'items': cart.items(), 'total': cart.total()})


def _validate_cart_stock(cart_items):
    """Re-checks stock at checkout time (it may have changed since items were added to the cart)."""
    problems = []
    for item in cart_items:
        product = item['product']
        if item['quantity'] > product.stock_quantity:
            if product.stock_quantity == 0:
                problems.append(f'{product.name} just sold out.')
            else:
                problems.append(f'Only {product.stock_quantity} of {product.name} left — you have {item["quantity"]} in your cart.')
    return problems


def _save_customer_details(request, form, order):
    """Remembers what a logged-in customer just typed, so next time is prefilled.

    Runs only after the order itself has been created, and only when the
    customer left the "remember these details" box ticked. The order keeps its
    own copy of these values either way — this never edits an order.
    """
    if not request.user.is_authenticated or not form.cleaned_data.get('save_details'):
        return
    profile = CustomerProfile.for_user(request.user)
    profile.default_name = order.customer_name
    profile.default_phone = order.phone_number
    profile.default_email = order.email
    profile.delivery_address = order.delivery_address
    profile.save()


def checkout(request):
    cart = get_cart(request)
    if cart.count() == 0:
        messages.info(request, 'Your cart is empty — add something first.')
        return redirect('shop:shop')

    if request.method == 'POST':
        form = CheckoutForm(request.POST)
        if form.is_valid():
            items = cart.items()
            problems = _validate_cart_stock(items)
            if problems:
                for p in problems:
                    messages.error(request, p)
                return redirect('shop:cart_detail')

            order = form.save(commit=False)
            # Ownership lives on the order itself so "My Orders" can find it
            # later. Guests are left as NULL and keep using their order link.
            if request.user.is_authenticated:
                order.user = request.user
            if order.payment_method == 'online':
                order.payment_reference = f'RBT-{secrets.token_hex(6)}'
            order.save()
            for item in items:
                OrderItem.objects.create(order=order, product=item['product'], product_name=item['product'].name,
                                          price=item['product'].price, quantity=item['quantity'])

            _save_customer_details(request, form, order)

            if order.payment_method == 'online':
                # Stock is reserved only once payment is actually verified —
                # see verify_payment() — so an abandoned payment attempt
                # never permanently ties up stock nobody paid for.
                cart.clear()
                return redirect('shop:payment', order_id=order.id, token=order.access_token)

            # Pay-on-delivery is a firm commitment, so reserve stock now,
            # atomically, and roll back the whole order if anything's
            # changed since we checked a moment ago (very rare race).
            with transaction.atomic():
                shortfalls = []
                for item in items:
                    if not Product.reserve_stock(item['product'].id, item['quantity']):
                        shortfalls.append(item['product'].name)
                if shortfalls:
                    transaction.set_rollback(True)

            if shortfalls:
                order.delete()
                messages.error(request, f"Sorry, stock changed while you were checking out: {', '.join(shortfalls)}. Please adjust your cart.")
                return redirect('shop:cart_detail')

            cart.clear()
            notify_new_order(order)
            return redirect('shop:order_confirmation', order_id=order.id, token=order.access_token)
    else:
        if request.user.is_authenticated:
            # Prefilled from what this customer saved last time — they can still
            # edit anything, and the order keeps whatever they actually submit.
            form = CheckoutForm(initial=CustomerProfile.for_user(request.user).prefill())
        else:
            form = CheckoutForm()

    return render(request, 'shop/checkout.html', {'form': form, 'items': cart.items(), 'total': cart.total()})


def order_confirmation(request, order_id, token):
    order = get_object_or_404(Order, id=order_id, access_token=token)
    lines = '\n'.join(f'• {item.product_name} x{item.quantity} — ₦{item.subtotal():,.0f}' for item in order.items.all())
    message = (
        f"Hi RehoBothTek! I just placed order #{order.id}:\n\n{lines}\n\n"
        f"Total: ₦{order.total():,.0f}\n\nName: {order.customer_name}\nPhone: {order.phone_number}\nAddress: {order.delivery_address}"
    )
    return render(request, 'shop/order_confirmation.html', {'order': order, 'whatsapp_url': whatsapp_link(message)})


# --- Online payment (Paystack) ---------------------------------------------------

def payment(request, order_id, token):
    order = get_object_or_404(Order, id=order_id, access_token=token, payment_method='online')
    if order.payment_status == 'paid':
        return redirect('shop:order_confirmation', order_id=order.id, token=order.access_token)

    paystack_configured = not settings.PAYSTACK_PUBLIC_KEY.endswith('REPLACE_WITH_YOUR_OWN_KEY')

    return render(request, 'shop/payment.html', {
        'order': order, 'amount_kobo': int(order.total() * 100),
        'paystack_public_key': settings.PAYSTACK_PUBLIC_KEY,
        'paystack_configured': paystack_configured,
    })


def verify_payment(request, order_id, token):
    order = get_object_or_404(Order, id=order_id, access_token=token, payment_method='online')
    reference = request.GET.get('reference', '')

    if order.payment_status == 'paid':
        return redirect('shop:order_confirmation', order_id=order.id, token=order.access_token)

    expected_kobo = int(order.total() * 100)
    try:
        verify_transaction(
            reference,
            expected_kobo,
            expected_reference=order.payment_reference,
            expected_email=order.email,
        )
    except PaystackVerificationError as exc:
        order.payment_status = 'failed'
        order.save(update_fields=['payment_status'])
        messages.error(request, f"We couldn't confirm that payment ({exc}). You can try again below.")
        return redirect('shop:payment', order_id=order.id, token=order.access_token)

    # Payment is genuinely verified. Now — and only now — reserve stock,
    # atomically per item. If something sold out in the meantime (rare:
    # would need a concurrent pay-on-delivery order for the same item while
    # this payment was in flight), we can't undo a successful charge, so we
    # flag it for you to resolve manually rather than silently losing track.
    oversold = []
    with transaction.atomic():
        for item in order.items.all():
            if item.product_id is None:
                continue
            if not Product.reserve_stock(item.product_id, item.quantity):
                oversold.append(item.product_name)

    order.payment_status = 'paid'
    order.payment_reference = reference
    order.save(update_fields=['payment_status', 'payment_reference'])

    notify_new_order(order)
    if oversold:
        notify_oversold_order(order, oversold)

    return redirect('shop:order_confirmation', order_id=order.id, token=order.access_token)


# --- Repairs ------------------------------------------------------------------

def repairs(request):
    repair_types = RepairType.objects.filter(is_active=True)
    if request.method == 'POST':
        form = RepairBookingForm(request.POST)
        if form.is_valid():
            booking = form.save()
            notify_new_repair_booking(booking)
            return redirect('shop:repair_confirmation', ticket_number=booking.ticket_number, token=booking.access_token)
    else:
        form = RepairBookingForm()
    return render(request, 'shop/repairs.html', {'repair_types': repair_types, 'form': form})


def repair_confirmation(request, ticket_number, token):
    booking = get_object_or_404(RepairBooking, ticket_number=ticket_number, access_token=token)
    message = (
        f"Hi RehoBothTek, I just booked a repair:\n\nTicket: {booking.ticket_number}\n"
        f"Name: {booking.customer_name}\nPhone: {booking.phone_number}\n"
        f"Device: {booking.device}\nRepair type: {booking.repair_type}\n"
        f"Preferred time: {booking.preferred_time}\nIssue: {booking.issue_description}"
    )
    return render(request, 'shop/repair_confirmation.html', {'booking': booking, 'whatsapp_url': whatsapp_link(message)})


# --- About / Contact ------------------------------------------------------------

def about(request):
    return render(request, 'shop/about.html')


def contact(request):
    if request.method == 'POST':
        form = ContactForm(request.POST)
        if form.is_valid():
            msg = form.save()
            notify_new_contact_message(msg)
            messages.success(request, "Thanks — we've got your message and will reply soon.")
            return redirect('shop:contact')
    else:
        form = ContactForm()
    return render(request, 'shop/contact.html', {'form': form})


# --- Order / repair tracking ----------------------------------------------------
# Deliberately NOT token-based: this is the intended "I don't have my link
# anymore" recovery path, so it uses a different ownership proof instead —
# the reference number AND the phone number used, both required together.

def _normalize_phone(value):
    return re.sub(r'\D', '', value or '')


def track(request):
    result = None
    error = None
    if request.method == 'POST':
        form = TrackForm(request.POST)
        if form.is_valid():
            ref = form.cleaned_data['reference_number'].strip()
            phone_digits = _normalize_phone(form.cleaned_data['phone_number'])
            if len(phone_digits) < 7:
                # The phone number is the ownership proof on this path, and it is
                # matched against the last 7 digits of the number on the record.
                # Anything shorter isn't a real phone number, and would "match" a
                # large share of orders by accident, so refuse it up front.
                error = ('Please enter the full phone number you used on the order — '
                         'at least 7 digits, including the local prefix.')
            elif ref.upper().startswith('RBT-') or not ref.isdigit():
                booking = RepairBooking.objects.filter(ticket_number__iexact=ref).first()
                if booking and _normalize_phone(booking.phone_number).endswith(phone_digits[-7:]):
                    result = {'type': 'repair', 'object': booking}
                else:
                    error = "We couldn't find a repair ticket matching that number and phone."
            else:
                order = Order.objects.filter(id=ref).first()
                if order and _normalize_phone(order.phone_number).endswith(phone_digits[-7:]):
                    result = {'type': 'order', 'object': order}
                else:
                    error = "We couldn't find an order matching that number and phone."
    else:
        form = TrackForm()
    return render(request, 'shop/track.html', {'form': form, 'result': result, 'error': error})


# --- Customer accounts ----------------------------------------------------------
# Registration and login both end up calling django.contrib.auth.login(), which
# fires user_logged_in — and that is where a guest's session cart is handed over
# to the account (see shop/signals.py). Login and logout themselves use Django's
# built-in views, mounted at /accounts/ in config/urls.py, so password checking,
# session handling and password resets are all Django's own code.

def register(request):
    if request.user.is_authenticated:
        return redirect('shop:account')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save()
            profile = CustomerProfile.for_user(user)
            profile.default_name = form.cleaned_data['first_name']
            profile.default_email = form.cleaned_data['email']
            profile.default_phone = form.cleaned_data['phone_number']
            profile.save()
            login(request, user)  # triggers the guest-cart transfer
            messages.success(request, f'Welcome to {settings.SITE_NAME}, {user.get_username()} — your account is ready.')
            return redirect(_safe_next(request) or 'shop:account')
    else:
        form = RegisterForm()
    # Carried back on a re-render too, so a validation error doesn't lose where
    # the customer was heading.
    return render(request, 'shop/register.html', {
        'form': form,
        'next': request.POST.get('next') or request.GET.get('next', ''),
    })


def _safe_next(request):
    """Where to send someone after registering — same rule Django's own login view uses."""
    target = request.POST.get('next') or request.GET.get('next')
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()},
                                                  require_https=request.is_secure()):
        return target
    return None


@login_required
def account(request):
    profile = CustomerProfile.for_user(request.user)

    if request.method == 'POST':
        form = CustomerProfileForm(request.POST, instance=profile)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your saved details have been updated.')
            return redirect('shop:account')
    else:
        form = CustomerProfileForm(instance=profile)

    # Prefetched because the table below shows each order's total, and
    # Order.total() sums its items — without this that is one query per row.
    orders = request.user.orders.prefetch_related('items')
    return render(request, 'shop/account.html', {
        'form': form,
        'profile': profile,
        'order_count': orders.count(),
        'recent_orders': orders[:5],
    })


@login_required
def order_history(request):
    # select_related/prefetch keep the list to a couple of queries rather than
    # one per order for its items.
    orders = request.user.orders.prefetch_related('items')
    return render(request, 'shop/order_history.html', {'orders': orders})


@login_required
def order_detail(request, order_id):
    # Scoped to the logged-in customer: an id typed into the URL can never
    # reach someone else's order. Guests keep using their tokenised order link.
    #
    # items__product is prefetched because the page lists each line and links
    # to the product it points at — one query per line otherwise. The order's
    # own total() also sums its items, and the prefetch is what stops that
    # being a fresh query every time the template reads it.
    order = get_object_or_404(
        Order.objects.prefetch_related('items__product'), id=order_id, user=request.user)
    lines = '\n'.join(f'• {item.product_name} x{item.quantity} — ₦{item.subtotal():,.0f}' for item in order.items.all())
    message = (
        f"Hi {settings.SITE_NAME}! I'd like to ask about order #{order.id}:\n\n{lines}\n\n"
        f"Total: ₦{order.total():,.0f}"
    )
    return render(request, 'shop/order_detail.html', {'order': order, 'whatsapp_url': whatsapp_link(message)})
