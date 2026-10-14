"""Render Web Service entry point for the polling Telegram bot.

Render Free offers Web Services, which must listen on the assigned PORT.
The bot itself uses Telegram long polling, so a tiny HTTP health endpoint is
started on PORT while the existing bot runs in the main thread.
"""
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"Namangan Tourism Telegram bot is running.\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            body = b"Not found\n"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, format, *args):
        # Keep Render logs focused on bot messages.
        return


port = int(os.environ.get("PORT", "10000"))
server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
Thread(target=server.serve_forever, daemon=True).start()

# Run the Telegram poller in the main thread so fatal configuration/API errors
# stop the process and are visible to Render instead of leaving a false health check.
import bot  # noqa: E402
bot.main()
