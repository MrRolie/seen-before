"""Client for the retrieval service, standard library only (importing serve.py would load PyTorch: measured
3.8 s on the first semantic call of every agent session in the test run, PROTOCOL.md amendment 7)."""
import json
import urllib.request

PORT = 8765


def search(text: str, k: int = 5, repo: str | None = None, mode: str = "dense"):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/search", method="POST",
                                 data=json.dumps({"text": text, "k": k, "repo": repo, "mode": mode}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return [tuple(h) for h in json.load(r)["hits"]]
