"""Project-wide middleware."""
from django.template.loader import render_to_string


class StaticNotFoundMiddleware:
    """Serve one plain "page not found" page for every HTML 404, whether DEBUG is on or off.

    With DEBUG on, Django's own 404 page lists every URL pattern in the project (including
    /cron/ and /partner/v1/) and echoes the requested path. This swaps any text/html 404 for a
    static page that says nothing about the site. JSON 404s from the mobile and partner APIs are
    left alone, so their clients still get a body they can parse.

    Listed above CommonMiddleware, so APPEND_SLASH redirects (/privacy -> /privacy/) happen first.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if (response.status_code == 404 and not response.streaming
                and response.get('Content-Type', '').startswith('text/html')):
            response.content = render_to_string('404.html')
            if response.has_header('Content-Length'):
                response['Content-Length'] = str(len(response.content))
        return response
