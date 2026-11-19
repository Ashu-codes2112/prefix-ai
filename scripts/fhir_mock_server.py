"""A local FHIR R4 server over synthetic fixtures, to try the EHR sync without a real EHR.

    uv run python scripts/fhir_mock_server.py            # http://127.0.0.1:8090/fhir
    uv run python scripts/fhir_mock_server.py --port 8091

Then, signed in as an admin, open Admin -> Integrations, set the FHIR base URL to
http://127.0.0.1:8090/fhir, map service code 73761001 to COLONOSCOPY and choose a default
facility, and press "Sync now". The data is made up (prefix_api/fhir_fixtures/fixtures.json),
and appointments are always a few days ahead. Stop the server with Ctrl+C.
"""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from prefix_api.services.fhir.fixture_server import FixtureFhirServer

SERVER = FixtureFhirServer()


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        host = self.headers.get("Host", "127.0.0.1")
        status, body = SERVER.handle(f"http://{host}{self.path}")
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/fhir+json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Paths only: the fixtures are synthetic, but keep the habit of not logging bodies.
        print(f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"FHIR mock server on http://127.0.0.1:{args.port}/fhir (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
