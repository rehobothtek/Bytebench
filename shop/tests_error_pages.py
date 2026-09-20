"""The custom 404 and 500 pages (F-46).

Both are exercised for real rather than by rendering the templates directly: a
URL that does not exist, and a view that raises. DEBUG is off — the state the
test runner puts the suite in anyway — so these are the pages a production
visitor would actually be served.

The counterpart tests check the other half of the requirement: with DEBUG on,
Django's own technical pages must still be what appears. Those are the only
thing that makes a traceback readable during development, and a custom 500
template quietly taking their place would be a real regression.

The failing view raises a message containing a word that appears nowhere else
in the project, so "no exception detail reaches the browser" is checked against
something that genuinely could leak.

Run with:  python manage.py test shop.tests_error_pages
"""
from django.http import HttpResponse
from django.test import TestCase, override_settings
from django.urls import path

# Included so that {% url 'shop:home' %} and friends still resolve inside the
# error templates while ROOT_URLCONF points at this module.
from config.urls import urlpatterns as project_urlpatterns

SECRET_MESSAGE = 'kaboom-do-not-leak-9f3a'


def explodes(request):
    """Stands in for a view hitting an unhandled error in production."""
    raise RuntimeError(SECRET_MESSAGE)


def survives(request):
    return HttpResponse('ok')


# __explodes__/ is listed first so it wins over the project's own patterns.
urlpatterns = [
    path('__explodes__/', explodes),
    path('__survives__/', survives),
] + project_urlpatterns


@override_settings(ROOT_URLCONF=__name__)
class ErrorPageTests(TestCase):
    """DEBUG is off here, which is how the suite already runs."""

    def setUp(self):
        # The test client re-raises by default so that a broken view fails a
        # test loudly. Here the 500 response *is* the thing under test.
        self.client.raise_request_exception = False

    def _server_error(self):
        """GET the exploding URL, capturing the ERROR django.request logs so a
        deliberate 500 doesn't spray a traceback through the test output."""
        with self.assertLogs('django.request', level='ERROR'):
            return self.client.get('/__explodes__/')

    def test_a_missing_url_gets_the_custom_404(self):
        response = self.client.get('/no-such-page-anywhere/')
        self.assertEqual(response.status_code, 404)
        self.assertTemplateUsed(response, '404.html')
        self.assertContains(response, "That page isn't here", status_code=404)

    def test_an_unhandled_error_gets_the_custom_500(self):
        response = self._server_error()
        self.assertEqual(response.status_code, 500)
        self.assertTemplateUsed(response, '500.html')
        self.assertContains(response, 'Something went wrong on our end', status_code=500)

    def test_the_500_page_leaks_nothing_about_the_exception(self):
        body = self._server_error().content.decode()
        self.assertNotIn(SECRET_MESSAGE, body)
        self.assertNotIn('RuntimeError', body)
        self.assertNotIn('Traceback', body)
        self.assertNotIn('tests_error_pages', body)
        self.assertNotIn('explodes', body)

    def test_the_500_page_renders_without_a_request_or_any_context(self):
        """Django renders 500.html with no request and no context processors.

        So it cannot use anything shop/context_processors.py provides — the site
        name has to come from a literal fallback. This is also why neither page
        extends base.html: the commonest cause of a 500 is the database being
        unreachable, which is exactly what that context processor would then try
        to query, turning one error into two.
        """
        response = self._server_error()
        self.assertContains(response, 'RehoBothTek', status_code=500)

    def test_neither_error_page_ships_markup_django_did_not_render(self):
        pages = {'/no-such-page-anywhere/': self.client.get('/no-such-page-anywhere/'),
                 '/__explodes__/': self._server_error()}
        for url, response in pages.items():
            with self.subTest(url=url):
                body = response.content.decode()
                self.assertNotIn('{#', body, 'a multi-line {# #} leaked into the page')
                self.assertNotIn('{%', body)
                self.assertNotIn('{{', body)

    def test_the_404_page_asks_not_to_be_indexed(self):
        response = self.client.get('/no-such-page-anywhere/')
        self.assertContains(response, 'name="robots" content="noindex"', status_code=404)

    def test_the_error_pages_still_look_like_the_site(self):
        """The visual language is the shared stylesheet and colour tokens."""
        response = self.client.get('/no-such-page-anywhere/')
        self.assertContains(response, 'css/style.css', status_code=404)
        self.assertContains(response, 'class="logo"', status_code=404)

    def test_a_working_view_is_unaffected(self):
        """The urlconf override must not have broken ordinary routing."""
        self.assertEqual(self.client.get('/__survives__/').content, b'ok')
        self.assertEqual(self.client.get('/').status_code, 200)


@override_settings(ROOT_URLCONF=__name__)
class DebugBehaviourTests(TestCase):
    """The other half of F-46: DEBUG on must still mean Django's own pages."""

    def setUp(self):
        self.client.raise_request_exception = False

    @override_settings(DEBUG=True)
    def test_debug_still_gets_the_technical_404(self):
        response = self.client.get('/no-such-page-anywhere/')
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, 'tried these URL patterns, in this order', status_code=404)
        self.assertNotContains(response, "That page isn't here", status_code=404)

    @override_settings(DEBUG=True)
    def test_debug_still_gets_the_technical_500_with_its_traceback(self):
        # Captured rather than printed: a deliberate 500 logs at ERROR on
        # django.request, and that handler writes to the console.
        with self.assertLogs('django.request', level='ERROR'):
            response = self.client.get('/__explodes__/')
        self.assertEqual(response.status_code, 500)
        # The exception really is shown in development, which is the whole
        # point of the technical page — and proof the custom one is not in play.
        self.assertIn(SECRET_MESSAGE, response.content.decode())
        self.assertNotContains(response, 'Something went wrong on our end', status_code=500)
