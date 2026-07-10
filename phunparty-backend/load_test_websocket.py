"""
Lightweight PhunParty WebSocket load harness.

Examples:
    python load_test_websocket.py connect --ws-url ws://localhost:8000/ws/session/ABC123 --token TOKEN --clients 100
    python load_test_websocket.py answer-burst --ws-url ws://localhost:8000/ws/session/ABC123 --token TOKEN --players P1,P2 --question-id Q001 --answer A

This is intentionally app-specific rather than a generic HTTP load test. It
measures WebSocket connection and message latency for the traffic shapes that
matter most to PhunParty.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlencode

import aiohttp


@dataclass
class Sample:
    ok: bool
    latency_ms: float
    error: str | None = None


def percentile(values: list[float], percent: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = min(len(values) - 1, max(0, round((percent / 100) * (len(values) - 1))))
    return values[index]


def print_summary(name: str, samples: Iterable[Sample]) -> None:
    sample_list = list(samples)
    latencies = [sample.latency_ms for sample in sample_list if sample.ok]
    failures = [sample for sample in sample_list if not sample.ok]

    print(f"\n{name}")
    print(f"  attempts: {len(sample_list)}")
    print(f"  ok:       {len(latencies)}")
    print(f"  failed:   {len(failures)}")
    if latencies:
        print(f"  p50 ms:   {percentile(latencies, 50):.1f}")
        print(f"  p95 ms:   {percentile(latencies, 95):.1f}")
        print(f"  p99 ms:   {percentile(latencies, 99):.1f}")
        print(f"  max ms:   {max(latencies):.1f}")
        print(f"  avg ms:   {statistics.mean(latencies):.1f}")
    if failures:
        for failure in failures[:5]:
            print(f"  error:    {failure.error}")


def websocket_url(base_url: str, token: str, client_type: str, player_id: str | None):
    params = {
        "token": token,
        "client_type": client_type,
    }
    if player_id:
        params["player_id"] = player_id
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}{urlencode(params)}"


async def connect_one(
    session: aiohttp.ClientSession,
    url: str,
    hold_seconds: float,
) -> Sample:
    started = time.perf_counter()
    try:
        async with session.ws_connect(url, heartbeat=20) as ws:
            await ws.receive(timeout=10)
            latency_ms = (time.perf_counter() - started) * 1000
            await asyncio.sleep(hold_seconds)
            await ws.close()
            return Sample(ok=True, latency_ms=latency_ms)
    except Exception as exc:
        return Sample(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=repr(exc),
        )


async def run_connect(args) -> None:
    connector = aiohttp.TCPConnector(limit=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        semaphore = asyncio.Semaphore(args.concurrency)

        async def guarded_connect(index: int) -> Sample:
            async with semaphore:
                player_id = f"{args.player_prefix}{index}" if args.mobile else None
                url = websocket_url(
                    args.ws_url,
                    args.token,
                    "mobile" if args.mobile else "web",
                    player_id,
                )
                return await connect_one(session, url, args.hold_seconds)

        samples = await asyncio.gather(
            *(guarded_connect(index) for index in range(args.clients))
        )
    print_summary("connection capacity", samples)


async def answer_one(
    session: aiohttp.ClientSession,
    url: str,
    question_id: str,
    answer: str,
) -> Sample:
    started = time.perf_counter()
    try:
        async with session.ws_connect(url, heartbeat=20) as ws:
            await ws.receive(timeout=10)
            await ws.send_str(
                json.dumps(
                    {
                        "type": "submit_answer",
                        "data": {
                            "question_id": question_id,
                            "answer": answer,
                        },
                    }
                )
            )

            deadline = time.perf_counter() + 10
            while time.perf_counter() < deadline:
                message = await ws.receive(timeout=10)
                if message.type == aiohttp.WSMsgType.TEXT:
                    payload = json.loads(message.data)
                    if payload.get("type") in {
                        "answer_submitted",
                        "answer_rejected",
                        "error",
                    }:
                        latency_ms = (time.perf_counter() - started) * 1000
                        await ws.close()
                        return Sample(ok=True, latency_ms=latency_ms)
                if message.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break

            return Sample(
                ok=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                error="timed out waiting for answer response",
            )
    except Exception as exc:
        return Sample(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            error=repr(exc),
        )


async def run_answer_burst(args) -> None:
    player_ids = [
        player.strip() for player in args.players.split(",") if player.strip()
    ]
    connector = aiohttp.TCPConnector(limit=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        semaphore = asyncio.Semaphore(args.concurrency)

        async def guarded_answer(player_id: str) -> Sample:
            async with semaphore:
                url = websocket_url(args.ws_url, args.token, "mobile", player_id)
                return await answer_one(
                    session,
                    url,
                    args.question_id,
                    args.answer,
                )

        samples = await asyncio.gather(*(guarded_answer(pid) for pid in player_ids))
    print_summary("trivia answer burst", samples)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    connect = subparsers.add_parser("connect")
    connect.add_argument("--ws-url", required=True)
    connect.add_argument("--token", required=True)
    connect.add_argument("--clients", type=int, default=100)
    connect.add_argument("--concurrency", type=int, default=100)
    connect.add_argument("--hold-seconds", type=float, default=5)
    connect.add_argument("--mobile", action="store_true")
    connect.add_argument("--player-prefix", default="LOAD")
    connect.set_defaults(func=run_connect)

    answer = subparsers.add_parser("answer-burst")
    answer.add_argument("--ws-url", required=True)
    answer.add_argument("--token", required=True)
    answer.add_argument("--players", required=True)
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--concurrency", type=int, default=100)
    answer.set_defaults(func=run_answer_burst)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
