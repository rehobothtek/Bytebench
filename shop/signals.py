"""
Customer-account hooks.

The login-time cart transfer lives here rather than inside a view so it fires
for *every* way someone can end up logged in — the login form, registration,
the Django admin, and anything added later — without each of them having to
remember to call it. Connected once in apps.py.
"""
from django.contrib import messages
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver

from .cart import merge_session_cart_into_account


@receiver(user_logged_in)
def transfer_guest_cart(sender, request, user, **kwargs):
    """Move whatever the visitor had in their guest cart into their account cart.

    The messages below are sent with fail_silently=True on purpose: login() can
    legitimately be called on a request that never went through the messages
    middleware (the test client's login(), a management command, a shell), and
    a missing cart message is never a reason to fail someone's login.
    """
    if request is None:
        return

    result = merge_session_cart_into_account(request, user)

    if result['moved']:
        messages.success(
            request,
            f'Welcome back! {result["moved"]} item{"" if result["moved"] == 1 else "s"} '
            f'from your guest cart {"was" if result["moved"] == 1 else "were"} moved into your account cart.',
            fail_silently=True,
        )
    if result['skipped']:
        messages.warning(
            request,
            f'{result["skipped"]} item{"" if result["skipped"] == 1 else "s"} from your guest cart '
            f'couldn\'t be moved because {"it is" if result["skipped"] == 1 else "they are"} no longer available.',
            fail_silently=True,
        )
