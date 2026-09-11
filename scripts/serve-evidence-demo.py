# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

"""Serve the public no-account target in baseline or deliberately changed form."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8848)
    parser.add_argument("--variant", choices=("baseline", "changed"), default="baseline")
    args = parser.parse_args()
    content = (Path(__file__).resolve().parent.parent / "examples/evidence-demo/index.html").read_text(encoding="utf-8")
    if args.variant == "changed":
        content = content.replace("textContent='Added to cart'", "textContent='Unable to add item'")
    body = content.encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Evidence demo {args.variant}: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
