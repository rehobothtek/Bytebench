"""Markup-level accessibility regressions for the fixes in this batch: the
toast live region (F-32), the quick-view dialog (F-33), the search box label
(F-34), the FAQ disclosure (F-35), the hover contrast (F-36) and the states
that used to be signalled by colour alone (F-37).

Two kinds of assertion appear here and they are worth telling apart:

  * Rendered markup, fetched through the test client. That is the real thing.
  * The contents of static/css/style.css, and of the inline <script> in
    templates/base.html. There is no JavaScript runner and no headless browser
    in this project, so a behaviour that only exists in that script can be
    checked no further than "the branch is still written". Those assertions are
    labelled as such rather than pretending to be behavioural.

Run with:  python manage.py test shop.tests_accessibility
"""
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from .models import FAQ, Order, OrderItem, Product

CSS_PATH = Path(settings.STATICFILES_DIRS[0]) / 'css' / 'style.css'


# --- Reading the stylesheet -------------------------------------------------

def css_text():
    """The stylesheet, comments stripped.

    Stripping matters for the selector lookup below: a comment sitting directly
    above a rule would otherwise be glued onto that rule's selector by the
    regex in rules(), and the lookup would miss.
    """
    return re.sub(r'/\*.*?\*/', '', CSS_PATH.read_text(encoding='utf-8'), flags=re.DOTALL)


def rules(text):
    """Every rule as {selector: body}.

    Built this way — rather than by searching for a selector string — so that
    '.faq-answer' cannot accidentally match the tail of
    '.faq-item.open .faq-answer'. At-rules leave their inner rules in the map
    under their own selectors, which is all this needs.
    """
    return {
        selector.strip(): body
        for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text)
    }


def root_colours(text):
    return dict(re.findall(r'(--[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})', rules(text)[':root']))


def relative_luminance(hex_colour):
    """WCAG 2.x relative luminance of a #rrggbb colour."""
    def linear(channel):
        return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(int(hex_colour[i:i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    lighter, darker = sorted((relative_luminance(first), relative_luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def resolved_colour(body, text, property_name):
    """The #rrggbb a declaration ends up as, following one var(--token) hop."""
    value = re.search(rf'{property_name}\s*:\s*([^;]+)', body)
    assert value, f'no {property_name} declaration in {body!r}'
    token = re.fullmatch(r'var\((--[a-z0-9-]+)\)', value.group(1).strip())
    if token:
        return root_colours(text)[token.group(1)]
    return value.group(1).strip()


def make_product(**kwargs):
    defaults = dict(
        name='Test Phone', category='phone', condition='new',
        spec='128GB · Blue · Battery 89%', price=Decimal('100000'),
        stock_quantity=10, emoji='📱',
    )
    defaults.update(kwargs)
    return Product.objects.create(**defaults)


def make_order(**kwargs):
    defaults = dict(
        customer_name='Ada Obi', email='ada@example.com',
        phone_number='08030000000', payment_method='delivery',
    )
    defaults.update(kwargs)
    order = Order.objects.create(**defaults)
    OrderItem.objects.create(order=order, product=make_product(name='Line'),
                             product_name='Line', price=Decimal('5000'), quantity=1)
    return order


# --- F-32: the toast --------------------------------------------------------

class ToastLiveRegionTests(TestCase):
    """F-32 — the toast is how every add-to-cart and every failure is reported,
    and it carried no live-region semantics at all, so it was silent."""

    def test_the_region_exists_before_anything_is_put_into_it(self):
        """A live region only announces changes made *after* it is in the DOM,
        so it has to ship empty rather than be created on demand."""
        html = self.client.get(reverse('shop:home')).content.decode()
        toast = re.search(r'<div class="toast"[^>]*>', html)
        self.assertIsNotNone(toast, 'the toast element is gone')
        self.assertIn('role="status"', toast.group(0))
        self.assertIn('aria-live="polite"', toast.group(0))
        self.assertIn('aria-atomic="true"', toast.group(0))

    def test_errors_are_routed_to_the_assertive_branch(self):
        # Script contents, not behaviour — see the module docstring.
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn("isError ? 'alert' : 'status'", html)
        self.assertIn("isError ? 'assertive' : 'polite'", html)


# --- F-33: the quick-view dialog --------------------------------------------

class QuickViewDialogTests(TestCase):
    """F-33 — the overlay looked like a dialog and was not one, and nothing
    moved focus into it or closed it from the keyboard."""

    def test_the_overlay_is_marked_up_as_a_dialog(self):
        html = self.client.get(reverse('shop:home')).content.decode()
        modal = re.search(r'<div class="quick-view-modal"[^>]*>', html)
        self.assertIsNotNone(modal)
        self.assertIn('role="dialog"', modal.group(0))
        self.assertIn('aria-modal="true"', modal.group(0))

    def test_the_dialog_is_labelled_by_its_own_heading(self):
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn('aria-labelledby="qvName"', html)
        # ...and that id really is on the heading the product name goes into.
        self.assertIn('<h3 id="qvName">', html)

    def test_escape_and_tab_containment_are_handled(self):
        # Script contents, not behaviour — see the module docstring. What this
        # can establish is that the dialog is not merely *declared* modal: the
        # key handler that makes the claim true is still there.
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn("e.key === 'Escape'", html)
        self.assertIn("e.key !== 'Tab'", html)
        self.assertIn("qvOverlay.classList.contains('open')", html)

    def test_focus_moves_into_the_dialog_and_back_to_the_card(self):
        html = self.client.get(reverse('shop:home')).content.decode()
        self.assertIn('openQuickView(btn)', html)
        self.assertIn('qvTrigger.focus()', html)


# --- F-34: the search field -------------------------------------------------

class SearchFieldLabelTests(TestCase):
    """F-34 — the search box was labelled by its placeholder, which vanishes as
    soon as anyone types, leaving the field unnamed."""

    def test_the_search_input_has_a_label_of_its_own(self):
        html = self.client.get(reverse('shop:shop')).content.decode()
        field = re.search(r'<input[^>]*id="liveSearchInput"[^>]*>', html)
        self.assertIsNotNone(field, 'the live search input is gone')
        self.assertIn('aria-label="Search products"', field.group(0))

    def test_the_visual_design_is_untouched(self):
        """The label was added without adding a visible element."""
        html = self.client.get(reverse('shop:shop')).content.decode()
        self.assertIn('placeholder="Search products…"', html)


# --- F-35: the FAQ disclosure -----------------------------------------------

class FaqDisclosureTests(TestCase):
    """F-35 — an FAQ answer was shown or hidden purely by animating its height,
    which says nothing at all to anyone not watching it."""

    def setUp(self):
        self.faq = FAQ.objects.create(
            question='Do you deliver outside Benue?',
            answer='Yes — nationwide, 2–4 working days.',
        )
        self.html = self.client.get(reverse('shop:home')).content.decode()

    def test_the_question_reports_whether_it_is_open(self):
        button = re.search(r'<button class="faq-question"[^>]*>', self.html)
        self.assertIsNotNone(button)
        self.assertIn('aria-expanded="false"', button.group(0))
        self.assertIn(f'aria-controls="faq-answer-{self.faq.id}"', button.group(0))

    def test_the_controlled_panel_is_the_answer(self):
        panel = re.search(rf'<div class="faq-answer" id="faq-answer-{self.faq.id}">', self.html)
        self.assertIsNotNone(panel, 'aria-controls points at an id that is not there')

    def test_the_script_keeps_the_state_in_step_with_the_visuals(self):
        self.assertIn("setAttribute('aria-expanded'", self.html)

    def test_a_closed_answer_is_taken_out_of_the_page_not_just_clipped(self):
        """max-height:0 alone leaves the text in the accessibility tree and in
        the tab order, so a closed FAQ still read out its own answer."""
        text = css_text()
        self.assertIn('visibility:hidden', rules(text)['.faq-answer'])
        self.assertIn('visibility:visible', rules(text)['.faq-item.open .faq-answer'])

    def test_the_open_close_animation_is_still_transitioned(self):
        self.assertIn('transition:max-height', rules(css_text())['.faq-answer'])


# --- F-36: hover contrast ---------------------------------------------------

class HoverContrastTests(TestCase):
    """F-36 — the filled blue button passed at rest and failed under the
    pointer, which is the one moment it is being aimed at."""

    def assert_white_text_is_legible(self, selector):
        text = css_text()
        body = rules(text)[selector]
        background = resolved_colour(body, text, 'background')
        ratio = contrast_ratio(background, '#ffffff')
        self.assertGreaterEqual(
            ratio, 4.5,
            f'white on {background} ({selector}) is {ratio:.2f}:1, below the 4.5:1 minimum')

    def test_the_primary_button_is_legible_at_rest(self):
        self.assert_white_text_is_legible('.btn-primary')

    def test_the_primary_button_is_still_legible_on_hover(self):
        # Resolved through the token rather than pinned to a hex, so the palette
        # stays free to change — as long as the result is readable.
        self.assert_white_text_is_legible('.btn-primary:hover')

    def test_the_hover_shade_is_darker_than_the_resting_one(self):
        text = css_text()
        resting = resolved_colour(rules(text)['.btn-primary'], text, 'background')
        hovered = resolved_colour(rules(text)['.btn-primary:hover'], text, 'background')
        self.assertLess(relative_luminance(hovered), relative_luminance(resting))


# --- F-37: states that were colour-only -------------------------------------

class ColourOnlyStateTests(TestCase):
    """F-37 — several states were signalled by a change of colour and nothing
    else, which is invisible to a screen reader and unreliable for anyone with
    a colour vision deficiency."""

    def test_the_wishlist_button_reports_its_state_when_not_saved(self):
        make_product()
        html = self.client.get(reverse('shop:shop')).content.decode()
        button = re.search(r'<button class="wishlist-btn[^"]*"[^>]*>', html, re.DOTALL)
        self.assertIsNotNone(button)
        self.assertIn('aria-pressed="false"', button.group(0))
        self.assertIn('aria-label="Save to wishlist"', button.group(0))

    def test_the_wishlist_button_reports_its_state_once_saved(self):
        product = make_product()
        self.client.post(reverse('shop:wishlist_toggle', args=[product.id]))
        html = self.client.get(reverse('shop:shop')).content.decode()
        button = re.search(r'<button class="wishlist-btn[^"]*"[^>]*>', html, re.DOTALL)
        self.assertIn('aria-pressed="true"', button.group(0))

    def test_the_script_flips_the_state_along_with_the_colour(self):
        html = self.client.get(reverse('shop:shop')).content.decode()
        self.assertIn("btn.setAttribute('aria-pressed'", html)

    def test_the_selected_category_filter_is_marked_as_current(self):
        html = self.client.get(reverse('shop:shop') + '?category=phone').content.decode()
        active = re.search(r'<a class="filter-btn active"[^>]*>Phones</a>', html)
        self.assertIsNotNone(active, 'the active filter chip is no longer marked active')
        self.assertIn('aria-current="true"', active.group(0))

    def test_only_the_selected_filter_is_marked_as_current(self):
        html = self.client.get(reverse('shop:shop') + '?category=phone').content.decode()
        self.assertEqual(html.count('aria-current="true"'), 1)

    def test_a_status_dot_with_no_text_beside_it_is_marked_decorative(self):
        """A dot inside a labelled row is fine — the text next to it says the
        same thing. A dot on its own in a panel header is read out as nothing
        useful, so it is hidden."""
        order = make_order()
        pages = [
            reverse('shop:home'),
            reverse('shop:about'),
            reverse('shop:order_confirmation', args=[order.id, order.access_token]),
        ]
        for url in pages:
            with self.subTest(url=url):
                html = self.client.get(url).content.decode()
                heads = re.findall(r'<div class="diag-head">(.*?)</div>', html, re.DOTALL)
                self.assertTrue(heads, f'no diagnostic panel found on {url}')
                for head in heads:
                    for dot in re.findall(r'<span class="led [a-z]+"[^>]*>', head):
                        self.assertIn('aria-hidden="true"', dot, f'{dot} on {url}')
