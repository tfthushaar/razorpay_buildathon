"""Phase 8: a real load test against a genuinely running server, not the in-process TestClient
tests already use (test_api.py's own concurrency tests dispatch through the same threadpool
machinery a real server does, but never leave the process or touch a real socket).

Answers the actual question LIMITATIONS.md's "not horizontally scaled" entry leaves open: is
single-instance FastAPI + SQLite actually a bottleneck at realistic concurrency, or is that a
speculative worry with no real evidence behind it? Measures, at increasing concurrency levels, real
wall-clock latency and error rate for POST /api/run against a live server -- not a projection, not a
TestClient stand-in.

Usage:
    cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000   # in one terminal
    python scripts/load_test.py                                                  # in another
"""

import concurrent.futures
import json
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://localhost:8000"
CONCURRENCY_LEVELS = [1, 4, 8, 16, 32]
REQUEST_BODY = json.dumps({"seed": 42, "main_n": 30, "stress_n": 10, "threshold": 0.90, "provider": "mock"}).encode("utf-8")


def _one_request(i: int) -> tuple[bool, float, int | None]:
    t0 = time.perf_counter()
    req = urllib.request.Request(
        f"{BASE_URL}/api/run", data=REQUEST_BODY, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
            return resp.status == 200, time.perf_counter() - t0, resp.status
    except urllib.error.HTTPError as e:
        return False, time.perf_counter() - t0, e.code
    except Exception:
        return False, time.perf_counter() - t0, None


def _check_server_up() -> bool:
    try:
        with urllib.request.urlopen(f"{BASE_URL}/api/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def main() -> None:
    if not _check_server_up():
        print(f"No server responding at {BASE_URL}/api/health -- start it first:")
        print("  cd backend && .venv/Scripts/python.exe -m uvicorn app.main:app --port 8000")
        sys.exit(1)

    print(f"{'concurrency':>11} | {'requests':>8} | {'succeeded':>9} | {'errors':>6} | {'mean_s':>7} | {'p95_s':>7} | {'max_s':>7} | {'wall_s':>7}")
    for concurrency in CONCURRENCY_LEVELS:
        n = concurrency * 3  # a few rounds at each level, not just one single burst
        t0 = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(_one_request, range(n)))
        wall = time.perf_counter() - t0

        succeeded = sum(1 for ok, _, _ in results if ok)
        errors = n - succeeded
        latencies = sorted(lat for _, lat, _ in results)
        mean_lat = statistics.mean(latencies)
        p95_lat = latencies[int(len(latencies) * 0.95) - 1] if latencies else 0.0
        max_lat = max(latencies) if latencies else 0.0

        print(f"{concurrency:>11} | {n:>8} | {succeeded:>9} | {errors:>6} | {mean_lat:>7.3f} | {p95_lat:>7.3f} | {max_lat:>7.3f} | {wall:>7.3f}")

        if errors:
            error_codes = sorted({code for ok, _, code in results if not ok})
            print(f"             error codes seen: {error_codes}")


if __name__ == "__main__":
    main()
