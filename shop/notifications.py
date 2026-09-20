"""
Sends an automatic email to the shop owner the moment an order, repair
booking, or contact message comes in — no customer action required.
Wrapped in try/except so a broken email setup never blocks a sale.
"""
import logging
from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def _send(subject, body):
    try:
        send_mail(subject=subject, message=body, from_email=settings.DEFAULT_FROM_EMAIL,
                   recipient_list=[settings.ADMIN_NOTIFY_EMAIL], fail_silently=False)
    except Exception as exc:
        # Logged at ERROR (logger.exception includes the traceback) with enough
        # context to diagnose a broken mail setup from the logs alone: which
        # notification, who it was going to, and what failed. Deliberately still
        # swallowed — a broken email setup must never fail a customer's order —
        # and the exception text is never shown to the customer.
        logger.exception(
            'Notification email failed — subject=%r to=%s (%s: %s)',
            subject, settings.ADMIN_NOTIFY_EMAIL, type(exc).__name__, exc,
        )


def notify_new_order(order):
    lines = '\n'.join(f'  - {item.product_name} x{item.quantity} — ₦{item.subtotal():,.0f}' for item in order.items.all())
    if order.payment_method == 'online':
        payment_line = f'Payment: PAID ONLINE via Paystack (ref: {order.payment_reference})'
    else:
        payment_line = 'Payment: Pay on delivery / pickup'
    body = (
        f'New order #{order.id} on {settings.SITE_NAME}\n\n'
        f'Customer: {order.customer_name}\nEmail: {order.email}\nPhone: {order.phone_number}\n'
        f'Delivery address: {order.delivery_address}\n{payment_line}\n\n'
        f'Items:\n{lines}\n\nTotal: ₦{order.total():,.0f}\n\n'
        f'View/manage: /admin/shop/order/{order.id}/change/'
    )
    _send(f'New order #{order.id} — ₦{order.total():,.0f}', body)


def notify_new_repair_booking(booking):
    body = (
        f'New repair booking on {settings.SITE_NAME}\n\n'
        f'Ticket: {booking.ticket_number}\nCustomer: {booking.customer_name}\nPhone: {booking.phone_number}\n'
        f'Device: {booking.device}\nRepair type: {booking.repair_type}\nPreferred time: {booking.preferred_time}\n'
        f'Issue: {booking.issue_description}\n\nView/manage: /admin/shop/repairbooking/{booking.id}/change/'
    )
    _send(f'New repair booking {booking.ticket_number}', body)


def notify_new_contact_message(msg):
    body = (
        f'New contact form message on {settings.SITE_NAME}\n\n'
        f'From: {msg.name}\nEmail: {msg.email}\nPhone: {msg.phone_number}\n\n'
        f'Message:\n{msg.message}\n\nView/manage: /admin/shop/contactmessage/{msg.id}/change/'
    )
    _send(f'New message from {msg.name}', body)


def notify_oversold_order(order, item_names):
    """
    Sent only in the rare case where a customer's online payment was
    verified successfully, but stock for one or more items ran out in the
    meantime (e.g. another customer bought the last unit via pay-on-delivery
    while this payment was being processed). The payment already succeeded
    and cannot be silently undone here, so this needs a human to resolve it
    — refund, substitute, or source more stock.
    """
    names = ', '.join(item_names)
    body = (
        f'⚠️ Order #{order.id} was paid successfully, but ran out of stock for: {names}\n\n'
        f'This customer has already been charged. You will need to either fulfil this from new '
        f'stock, offer a substitute, or issue a refund via your Paystack dashboard.\n\n'
        f'Customer: {order.customer_name}\nEmail: {order.email}\nPhone: {order.phone_number}\n\n'
        f'View/manage: /admin/shop/order/{order.id}/change/'
    )
    _send(f'⚠️ ACTION NEEDED: Order #{order.id} oversold after payment', body)
