"""Measure download speed by fetching the same URL several times sequentially."""

from __future__ import annotations

import argparse
import dataclasses
import math
import sys
import time

import httpx

DEFAULT_REQUESTS = 10
DEFAULT_TIMEOUT = 30.0

KB = 1_000.0
MB = 1_000_000.0
GB = 1_000_000_000.0

# Some hosts (e.g. Wikimedia) reject requests with a generic library User-Agent.
USER_AGENT = "source-checker/0.1.0 (+https://github.com/MikleMasterMind/source-checker)"


class RequestTimeout(Exception):
    """The response was not fully received within the per-request time limit."""


@dataclasses.dataclass
class Stats:
    """Aggregated results of one benchmark run."""

    timings: list[float] = dataclasses.field(default_factory=list)
    total_bytes: int = 0
    failures: int = 0
    interrupted: bool = False


def format_size(num_bytes: int) -> str:
    """Human-readable size in decimal units (1 GB = 1,000,000,000 bytes)."""
    if num_bytes >= GB:
        return f"{num_bytes / GB:.2f} GB"
    if num_bytes >= MB:
        return f"{num_bytes / MB:.2f} MB"
    if num_bytes >= KB:
        return f"{num_bytes / KB:.1f} KB"
    return f"{num_bytes} B"


def format_duration(seconds: float) -> str:
    """Human-readable duration (minutes are used above one minute)."""
    if seconds < 1.0:
        return f"{seconds * 1_000:.0f} ms"
    if seconds >= 60.0:
        minutes, rest = divmod(seconds, 60.0)
        return f"{minutes:.0f} m {rest:02.0f} s"
    return f"{seconds:.2f} s"


def download_once(client: httpx.Client, url: str, limit: float) -> tuple[int, float]:
    """Perform a single GET request; return (bytes downloaded, elapsed seconds).

    The whole response must arrive within `limit` seconds, otherwise
    RequestTimeout is raised (httpx timeouts act as a connect/stall backstop).
    """
    start = time.perf_counter()
    deadline = start + limit
    downloaded = 0
    with client.stream("GET", url) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            downloaded += len(chunk)
            if time.perf_counter() > deadline:
                raise RequestTimeout(f"response not completed within {limit:g} s")
    return downloaded, time.perf_counter() - start


def run_requests(url: str, count: int, timeout: float) -> Stats:
    """Run `count` sequential requests, printing a line for each."""
    stats = Stats()
    width = len(str(count))
    print(f"URL: {url}")
    print(f"Sequential requests: {count}, time limit: {timeout:g} s per request")
    print()
    headers = {"User-Agent": USER_AGENT}
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            for i in range(1, count + 1):
                try:
                    downloaded, elapsed = download_once(client, url, timeout)
                except (httpx.HTTPError, RequestTimeout) as exc:
                    stats.failures += 1
                    reason = str(exc).splitlines()[:1] or [exc.__class__.__name__]
                    print(f"#{i:>{width}}  FAIL  {reason[0]}")
                    continue
                stats.timings.append(elapsed)
                stats.total_bytes += downloaded
                print(f"#{i:>{width}}  {format_duration(elapsed):>9}  {format_size(downloaded):>10}")
    except KeyboardInterrupt:
        stats.interrupted = True
    print()
    return stats


def print_summary(stats: Stats, count: int) -> int:
    """Print aggregate stats and return the process exit code."""
    if stats.interrupted:
        print("Interrupted — partial results:")
    if not stats.timings:
        if stats.interrupted:
            print("No completed requests.")
        else:
            print(f"All {stats.failures} requests failed.")
        return 1
    total_time = sum(stats.timings)
    avg_time = total_time / len(stats.timings)
    speed = stats.total_bytes / total_time if total_time > 0 else 0.0
    print(f"Successful requests:  {len(stats.timings)}/{count}")
    if stats.failures:
        print(f"Failed requests:      {stats.failures}")
    print(f"Average request time: {format_duration(avg_time)}")
    print(f"Total downloaded:     {format_size(stats.total_bytes)}")
    print(f"Average speed:        {speed / MB:.2f} MB/s ({speed * 8 / MB:.1f} Mbps)")
    return 0


def positive_int(value: str) -> int:
    """argparse type: an integer of at least 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"{value!r} must be at least 1")
    return number


def positive_float(value: str) -> float:
    """argparse type: a positive finite number."""
    try:
        number = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number") from None
    if not (math.isfinite(number) and number > 0):
        raise argparse.ArgumentTypeError(f"{value!r} must be a positive finite number")
    return number


def parse_args() -> argparse.Namespace:
    """Parse and validate command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Measure download speed: performs several sequential GET requests to a URL, "
            "waits for each response to complete and reports the average download speed."
        )
    )
    parser.add_argument("url", help="URL to download (e.g. a large image)")
    parser.add_argument(
        "-n",
        "--requests",
        type=positive_int,
        default=DEFAULT_REQUESTS,
        help=f"number of sequential requests (default: {DEFAULT_REQUESTS})",
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=DEFAULT_TIMEOUT,
        help=f"per-request time limit in seconds (default: {DEFAULT_TIMEOUT:g})",
    )
    return parser.parse_args()


def main() -> int:
    """Entry point: parse arguments, run the benchmark and report results."""
    args = parse_args()
    stats = run_requests(args.url, args.requests, args.timeout)
    code = print_summary(stats, args.requests)
    return 130 if stats.interrupted else code


if __name__ == "__main__":
    sys.exit(main())
