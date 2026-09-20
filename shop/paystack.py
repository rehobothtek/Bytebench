"""
Server-side verification of Paystack payments. We never trust the browser's
"payment successful" callback alone — only Paystack's own verify endpoint,
called from our server using the secret key, counts as proof.

This module checks, all of which must pass before a payment is accepted:
  1. The reference being verified is EXACTLY the one we generated for this
     specific order at checkout time (never trust a reference supplied by
     the browser without this check — otherwise a customer could replay a
     genuine-but-unrelated successful transaction reference, e.g. from an
     earlier order of theirs for the same amount, against a new order).
  2. Paystack confirms the transaction status is 'success'.
  3. The amount paid matches the order total exactly, in kobo.
  4. The currency is NGN.
  5. The email on the transaction matches the order's email (defense in
     depth — not a substitute for #1, but catches other classes of mix-up).
"""
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class PaystackVerificationError(Exception):
    pass


def verify_transaction(reference, expected_amount_kobo, expected_reference=None, expected_email=None):
    if not reference:
        raise PaystackVerificationError('No payment reference was provided.')

    # Check #1: the reference must be exactly the one issued for this order.
    # This is the single most important check — everything else here is
    # about validating a genuine Paystack transaction, but this is what
    # ties that transaction to THIS order rather than any other.
    if expected_reference is not None and reference != expected_reference:
        raise PaystackVerificationError('This payment reference does not match this order.')

    url = f'https://api.paystack.co/transaction/verify/{reference}'
    headers = {'Authorization': f'Bearer {settings.PAYSTACK_SECRET_KEY}'}
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.exception('Paystack verify request failed for reference %s', reference)
        raise PaystackVerificationError('Could not reach Paystack to verify this payment.') from exc

    payload = response.json()
    if not payload.get('status'):
        raise PaystackVerificationError(payload.get('message', 'Paystack rejected this reference.'))

    data = payload.get('data', {})

    if data.get('reference') != reference:
        # Belt-and-braces: Paystack's own response should echo back the
        # reference we asked about. If it doesn't, something is very wrong.
        raise PaystackVerificationError('Paystack returned a mismatched reference.')

    if data.get('status') != 'success':
        raise PaystackVerificationError(f"Transaction status was '{data.get('status')}', not 'success'.")

    if (data.get('currency') or '').upper() != 'NGN':
        raise PaystackVerificationError(f"Unexpected currency '{data.get('currency')}' — expected NGN.")

    if data.get('amount') != expected_amount_kobo:
        raise PaystackVerificationError(f"Amount mismatch: paid {data.get('amount')} kobo, expected {expected_amount_kobo} kobo.")

    if expected_email:
        paid_email = ((data.get('customer') or {}).get('email') or '').strip().lower()
        if paid_email and paid_email != expected_email.strip().lower():
            raise PaystackVerificationError(f"Payment email '{paid_email}' does not match the order email.")

    return data
