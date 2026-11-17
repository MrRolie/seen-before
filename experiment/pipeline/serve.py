#!/usr/bin/env python3
"""The retrieval models, loaded once and served on localhost, so no answer pays for loading them.

    prlimit --as=64000000000 .venv/bin/python -m experiment.pipeline.serve

POST /search {"text": ..., "k": 5, "repo": null, "mode": "dense"|"hybrid"} -> {"hits": [[id, score], ...]}
Single-threaded on purpose: one GPU, and the arms never run at the same time.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from experiment.pipeline import config as C
from experiment.pipeline.retrieve import Retriever

R = None


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        hits = R.search(body["text"], k=body.get("k", C.TOP_K), repo=body.get("repo"), mode=body.get("mode", "dense"))
        out = json.dumps({"hits": hits}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass


def search(text: str, k: int = C.TOP_K, repo: str | None = None, mode: str = "dense"):
    """Client side, standard library only."""
    import urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:{C.SERVE_PORT}/search", method="POST",
                                 data=json.dumps({"text": text, "k": k, "repo": repo, "mode": mode}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return [tuple(h) for h in json.load(r)["hits"]]


if __name__ == "__main__":
    R = Retriever.load()
    R.search("warm up", mode="hybrid")
    print(f"serving on 127.0.0.1:{C.SERVE_PORT}", flush=True)
    HTTPServer(("127.0.0.1", C.SERVE_PORT), H).serve_forever()
