from http.server import BaseHTTPRequestHandler, HTTPServer
import json

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body)
        except:
            data = body.decode()

        print("\n=== EVENT RECEIVED ===")
        print(json.dumps(data, indent=2))
        print("======================\n")

        self.send_response(200)
        self.end_headers()

server = HTTPServer(("localhost", 9000), Handler)
print("Listening for events on http://localhost:9000")
server.serve_forever()