import http.server, os, socketserver, sys
import sys
ROOT = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.realpath(__file__)))), "web")
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8731
class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        # The one route the host answers that is not a file. Without it the
        # page 404s on its first request, which is not a page fault.
        if self.path.split("?")[0] == "/mode":
            body = b'{"require_link": false}'
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/static/"):
            path = path[len("/static"):]
        if path in ("", "/"):
            path = "/index.html"
        return os.path.join(ROOT, path.lstrip("/"))
    def log_message(self, *a): pass
socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), H) as s:
    s.serve_forever()
