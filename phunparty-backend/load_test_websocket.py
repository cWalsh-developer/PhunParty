"""
Lightweight PhunParty WebSocket load harness.

Examples:
    python load_test_websocket.py connect --ws-url ws://localhost:8000/ws/session/ABC123 --players-file players.json --clients 100
    python load_test_websocket.py answer-burst --ws-url ws://localhost:8000/ws/session/ABC123 --players-file players.json --question-id Q001 --answer A

This is intentionally app-specific rather than a generic HTTP load test. It
measures WebSocket connection and message latency for the traffic shapes that
matter most to PhunParty.

Player fixture format:
    [{"player_id": "P1", "token": "..."}, {"player_id": "P2", "token": "..."}]
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
    status: str = "ok"


@dataclass(frozen=True)
class PlayerCredential:
    player_id: str
    token: str


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
    statuses: dict[str, int] = {}
    for sample in sample_list:
        statuses[sample.status] = statuses.get(sample.status, 0) + 1

    print(f"\n{name}")
    print(f"  attempts: {len(sample_list)}")
    print(f"  ok:       {len(latencies)}")
    print(f"  failed:   {len(failures)}")
    for status, count in sorted(statuses.items()):
        print(f"  {status}: {count}")
    if latencies:
        print(f"  p50 ms:   {percentile(latencies, 50):.1f}")
        print(f"  p95 ms:   {percentile(latencies, 95):.1f}")
        print(f"  p99 ms:   {percentile(latencies, 99):.1f}")
        print(f"  max ms:   {max(latencies):.1f}")
        print(f"  avg ms:   {statistics.mean(latencies):.1f}")
    if failures:
        for failure in failures[:5]:
            print(f"  error:    {failure.error}")


def load_player_credentials(path: str | None) -> list[PlayerCredential]:
    if not path:
        return []
    with open(path, "r", encoding="utf-8") as handle:
        raw_players = json.load(handle)
    if not isinstance(raw_players, list):
        raise ValueError("players fixture must be a JSON list")

    credentials = []
    for index, item in enumerate(raw_players):
        if not isinstance(item, dict):
            raise ValueError(f"players fixture item {index} must be an object")
        player_id = str(item.get("player_id") or "").strip()
        token = str(item.get("token") or "").strip()
        if not player_id or not token:
            raise ValueError(f"players fixture item {index} needs player_id and token")
        credentials.append(PlayerCredential(player_id=player_id, token=token))
    return credentials


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
            message = await ws.receive(timeout=10)
            latency_ms = (time.perf_counter() - started) * 1000
            if message.type != aiohttp.WSMsgType.TEXT:
                return Sample(
                    ok=False,
                    latency_ms=latency_ms,
                    status="handshake_failed",
                    error=f"unexpected websocket message type {message.type}",
                )
            payload = json.loads(message.data)
            if payload.get("type") != "connection_established":
                return Sample(
                    ok=False,
                    latency_ms=latency_ms,
                    status="handshake_failed",
                    error=f"expected connection_established, got {payload.get('type')}",
                )
            await asyncio.sleep(hold_seconds)
            await ws.close()
            return Sample(ok=True, latency_ms=latency_ms, status="connected")
    except Exception as exc:
        return Sample(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            status="connection_error",
            error=repr(exc),
        )


async def run_connect(args) -> None:
    credentials = load_player_credentials(args.players_file)
    connector = aiohttp.TCPConnector(limit=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=30)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        semaphore = asyncio.Semaphore(args.concurrency)

        async def guarded_connect(index: int) -> Sample:
            async with semaphore:
                credential = credentials[index] if index < len(credentials) else None
                player_id = (
                    credential.player_id
                    if credential
                    else f"{args.player_prefix}{index}" if args.mobile else None
                )
                token = credential.token if credential else args.token
                if not token:
                    return Sample(
                        ok=False,
                        latency_ms=0,
                        status="missing_token",
                        error="provide --players-file or --token",
                    )
                url = websocket_url(
                    args.ws_url,
                    token,
                    "mobile" if args.mobile else "web",
                    player_id,
                )
                return await connect_one(session, url, args.hold_seconds)

        client_count = (
            min(args.clients, len(credentials)) if credentials else args.clients
        )
        samples = await asyncio.gather(
            *(guarded_connect(index) for index in range(client_count))
        )
    print_summary("connection capacity", samples)


async def answer_one(
    session: aiohttp.ClientSession,
    url: str,
    question_id: str,
    answer: str,
    ready_queue: asyncio.Queue[str],
    release_event: asyncio.Event,
    player_id: str,
) -> Sample:
    started = time.perf_counter()
    try:
        async with session.ws_connect(url, heartbeat=20) as ws:
            message = await ws.receive(timeout=10)
            if message.type != aiohttp.WSMsgType.TEXT:
                return Sample(
                    ok=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status="handshake_failed",
                    error=f"unexpected websocket message type {message.type}",
                )
            payload = json.loads(message.data)
            if payload.get("type") != "connection_established":
                return Sample(
                    ok=False,
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status="handshake_failed",
                    error=f"expected connection_established, got {payload.get('type')}",
                )
            ready_queue.put_nowait(player_id)
            await release_event.wait()
            answer_started = time.perf_counter()
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
                    message_type = payload.get("type")
                    if message_type == "answer_submitted":
                        latency_ms = (time.perf_counter() - answer_started) * 1000
                        await ws.close()
                        return Sample(
                            ok=True,
                            latency_ms=latency_ms,
                            status="answer_submitted",
                        )
                    if message_type in {"answer_rejected", "error"}:
                        latency_ms = (time.perf_counter() - answer_started) * 1000
                        await ws.close()
                        return Sample(
                            ok=False,
                            latency_ms=latency_ms,
                            status=message_type,
                            error=json.dumps(payload.get("data") or payload),
                        )
                if message.type in {
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                }:
                    break

            return Sample(
                ok=False,
                latency_ms=(time.perf_counter() - started) * 1000,
                status="answer_timeout",
                error="timed out waiting for answer response",
            )
    except Exception as exc:
        return Sample(
            ok=False,
            latency_ms=(time.perf_counter() - started) * 1000,
            status="answer_error",
            error=repr(exc),
        )


async def run_answer_burst(args) -> None:
    credentials = load_player_credentials(args.players_file)
    if not credentials:
        if not args.token or not args.players:
            raise ValueError(
                "answer-burst requires --players-file or --token plus --players"
            )
        player_ids = [
            player.strip() for player in args.players.split(",") if player.strip()
        ]
        credentials = [
            PlayerCredential(player_id=player_id, token=args.token)
            for player_id in player_ids
        ]
        print(
            "warning: using one token for multiple player IDs does not simulate "
            "distinct authenticated players; prefer --players-file"
        )

    connector = aiohttp.TCPConnector(limit=args.concurrency)
    timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=30)

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        semaphore = asyncio.Semaphore(args.concurrency)
        ready_queue: asyncio.Queue[str] = asyncio.Queue()
        release_event = asyncio.Event()

        async def guarded_answer(credential: PlayerCredential) -> Sample:
            async with semaphore:
                url = websocket_url(
                    args.ws_url,
                    credential.token,
                    "mobile",
                    credential.player_id,
                )
                return await answer_one(
                    session,
                    url,
                    args.question_id,
                    args.answer,
                    ready_queue,
                    release_event,
                    credential.player_id,
                )

        tasks = [asyncio.create_task(guarded_answer(item)) for item in credentials]
        ready_players = set()
        ready_deadline = time.perf_counter() + args.ready_timeout
        while (
            len(ready_players) < len(credentials)
            and time.perf_counter() < ready_deadline
        ):
            try:
                player_id = await asyncio.wait_for(
                    ready_queue.get(),
                    timeout=max(0.1, ready_deadline - time.perf_counter()),
                )
                ready_players.add(player_id)
            except asyncio.TimeoutError:
                break
            if any(task.done() and task.exception() for task in tasks):
                break

        print(f"connected and ready: {len(ready_players)}/{len(credentials)}")
        release_event.set()
        samples = await asyncio.gather(*tasks)
    print_summary("trivia answer burst", samples)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    connect = subparsers.add_parser("connect")
    connect.add_argument("--ws-url", required=True)
    connect.add_argument("--token")
    connect.add_argument("--players-file")
    connect.add_argument("--clients", type=int, default=100)
    connect.add_argument("--concurrency", type=int, default=100)
    connect.add_argument("--hold-seconds", type=float, default=5)
    connect.add_argument("--mobile", action="store_true")
    connect.add_argument("--player-prefix", default="LOAD")
    connect.set_defaults(func=run_connect)

    answer = subparsers.add_parser("answer-burst")
    answer.add_argument("--ws-url", required=True)
    answer.add_argument("--token")
    answer.add_argument("--players")
    answer.add_argument("--players-file")
    answer.add_argument("--question-id", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--concurrency", type=int, default=100)
    answer.add_argument("--ready-timeout", type=float, default=15)
    answer.set_defaults(func=run_answer_burst)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
