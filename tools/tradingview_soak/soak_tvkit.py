"""24-hour BTC soak using the OANDA experiment's tvkit polling/recovery path."""
import argparse
import asyncio
import json
import logging
import math
import os
import resource
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
SYMBOL = "BINANCE:BTCUSDT"
TARGET_SECONDS = 24 * 3600
MAX_WALL_SECONDS = TARGET_SECONDS + 120
os.environ.pop("TVKIT_BROWSER", None)
os.environ.pop("TVKIT_AUTH_TOKEN", None)


def sessions(a, b):
    """Crypto is expected to trade continuously, including weekends."""
    day = datetime.fromtimestamp(a, UTC).date()
    last = datetime.fromtimestamp(b, UTC).date()
    while day <= last:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
        end = start + 86400
        if start < b and end > a:
            yield start, end, str(day)
        day += timedelta(days=1)


def open_seconds(a, b):
    return sum(max(0, min(b, end) - max(a, start)) for start, end, _ in sessions(a, b))


def next_wake_at(last_boundary, deadline):
    """Anchor wakeup to the last processed close, including its +10s offset."""
    final_boundary = int(deadline//900)*900
    finish = max(deadline, final_boundary+60)
    return min(last_boundary+900+10, finish) if last_boundary < final_boundary else finish


def atomic_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def rss_mb():
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 2)
    return 0


def validate_bars(bars, minutes):
    stamps = [int(b.timestamp) for b in bars]
    assert len(stamps) >= 15, "Too few bars"
    assert stamps == sorted(set(stamps)), "Duplicate/unordered timestamps"
    for bar in bars:
        assert all(math.isfinite(getattr(bar, k)) for k in ("open", "high", "low", "close"))
        assert bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
        assert bar.timestamp <= time.time(), "Future timestamp"
        assert bar.timestamp == int(bar.timestamp) and int(bar.timestamp) % (minutes*60) == 0, "Misaligned BTC bar"
        assert math.isfinite(bar.volume) and bar.volume >= 0, "Invalid volume"
    assert all((b-a) >= minutes*60 and (b-a) % (minutes*60) == 0 for a, b in zip(stamps, stamps[1:]))


def load_client():
    from tvkit.api.chart.ohlcv import OHLCV
    from tvkit.api.chart.services.connection_service import ConnectionService
    identities = []
    original = ConnectionService.get_data_stream

    async def observed(self):
        async for message in original(self):
            if message.get("m") == "symbol_resolved":
                identities.append(message["p"][-1])
            yield message

    ConnectionService.get_data_stream = observed
    return OHLCV, identities


class Soak:
    def __init__(self, root):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "candles.sqlite")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS candles(tf INTEGER, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, first_seen REAL, PRIMARY KEY(tf,ts));
        CREATE TABLE IF NOT EXISTS expected(tf INTEGER, ts INTEGER, PRIMARY KEY(tf,ts));
        CREATE TABLE IF NOT EXISTS attempts(at REAL, tf INTEGER, success INTEGER, elapsed REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS issues(at REAL, kind TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS memory(at REAL, rss REAL);
        """)
        state_path = root / "state.json"
        self.state = json.loads(state_path.read_text()) if state_path.exists() else {
            "started_utc": datetime.now(UTC).isoformat(), "started_epoch": time.time(),
            "observed_market_open_seconds": 0, "session_dates": [], "process_starts": 0,
            "last_boundary": 0, "fault_checks": {}, "live_fault_checks": {}, "polling_offset_seconds": 10,
            "target_market_open_hours": 24, "delivery_budget_seconds": 60,
            "historical_replays_deduplicated": 0,
        }
        self.state.setdefault("historical_replays_deduplicated", 0)
        self.state.setdefault("deadline_epoch", self.state["started_epoch"] + TARGET_SECONDS)
        self.state.setdefault("deadline_utc", datetime.fromtimestamp(self.state["deadline_epoch"], UTC).isoformat())
        if self.state["process_starts"] == 0:
            self.state["last_boundary"] = int((self.state["started_epoch"]-10)//900)*900
        self.state["process_starts"] += 1
        self.OHLCV, self.identities = load_client()
        self.last_accounted = self.state["started_epoch"] if self.state["process_starts"] == 1 else time.time()

    def event(self, kind, **details):
        data = {"at_utc": datetime.now(UTC).isoformat(), "kind": kind, **details}
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(data) + "\n")
        print(json.dumps(data), flush=True)

    def account(self):
        now = time.time()
        self.state["observed_market_open_seconds"] += open_seconds(self.last_accounted, now)
        dates = set(self.state["session_dates"])
        dates.update(day for _, _, day in sessions(self.last_accounted, now))
        self.state["session_dates"] = sorted(dates)
        self.last_accounted = now
        self.db.execute("INSERT INTO memory VALUES (?,?)", (now, rss_mb()))
        self.db.commit()

    def summary(self, terminal=False):
        missing = self.db.execute("SELECT count(*) FROM expected e LEFT JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE c.ts IS NULL").fetchone()[0]
        overdue = self.db.execute("SELECT count(*) FROM expected e LEFT JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE c.ts IS NULL AND e.ts+e.tf*60+60<=?", (time.time(),)).fetchone()[0]
        late = self.db.execute("SELECT count(*) FROM expected e JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE c.first_seen-(c.ts+c.tf*60)>60").fetchone()[0]
        counts = {str(tf): self.db.execute("SELECT count(*) FROM expected WHERE tf=?", (tf,)).fetchone()[0] for tf in (15, 60)}
        issues = self.db.execute("SELECT count(*) FROM issues").fetchone()[0]
        failures = self.db.execute("SELECT count(*) FROM attempts WHERE success=0").fetchone()[0]
        latencies = [r[0] for r in self.db.execute("SELECT elapsed FROM attempts WHERE success=1 ORDER BY elapsed")]
        delivery = {str(tf): dict(zip(("observed", "average_seconds", "max_seconds"), self.db.execute("SELECT count(*),round(avg(c.first_seen-(c.ts+c.tf*60)),2),round(max(c.first_seen-(c.ts+c.tf*60)),2) FROM expected e JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE e.tf=?", (tf,)).fetchone())) for tf in (15, 60)}
        peak = self.db.execute("SELECT max(rss) FROM memory").fetchone()[0] or rss_mb()
        # Compare warmed first/last groups; report growth for review, not a proof of a leak.
        samples = [r[0] for r in self.db.execute("SELECT rss FROM memory ORDER BY at")]
        growth = round(sum(samples[-30:])/len(samples[-30:]) - sum(samples[5:35])/len(samples[5:35]), 2) if len(samples) >= 65 else None
        faults_ok = all(self.state.get(k, {}).get("status") == "PASS" for k in ("fault_checks", "live_fault_checks"))
        checks_ok = missing == 0 and late == 0 and issues == 0 and peak < 100 and (growth is not None and growth <= 10) and faults_ok and self.state["process_starts"] == 1 and all(int(counts[str(tf)]) >= minimum for tf, minimum in ((15, 96), (60, 24)))
        sufficient = self.state["observed_market_open_seconds"] >= TARGET_SECONDS
        result = {
            **self.state, "updated_utc": datetime.now(UTC).isoformat(), "symbol": SYMBOL, "tvkit_version": "0.18.0",
            "status": ("AUTOMATED_CHECKS_PASSED_REVIEW_PENDING" if sufficient and checks_ok else "REVIEW_REQUIRED") if terminal else "RUNNING",
            "observed_market_open_hours": round(self.state["observed_market_open_seconds"]/3600, 3),
            "expected_closed_candles": counts, "missing_candles": missing, "candles_later_than_60_seconds": late,
            "missing_candles_overdue": overdue,
            "data_integrity_issues": issues, "failed_requests_including_recovered": failures,
            "successful_requests": len(latencies),
            "request_latency_seconds": {"p50": round(latencies[len(latencies)//2], 2), "p95": round(latencies[max(0, math.ceil(len(latencies)*0.95)-1)], 2), "max": round(latencies[-1], 2)} if latencies else {},
            "closed_candle_delivery_seconds": delivery,
            "rss_current_mb": rss_mb(), "rss_peak_sampled_mb": peak, "rss_recent_vs_warmed_mb": growth,
            "rss_peak_os_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 2),
            "calendar": "Binance crypto 24/7, UTC boundaries; exchange outages count as missing coverage",
            "limitations": "Boundary polling at intended bot cadence, not maximum throughput or sustained streaming. BTC does not qualify OANDA gold/session behavior. Independent finalized OHLC comparison and systemd review remain required.",
        }
        atomic_json(self.root / "state.json", self.state)
        atomic_json(self.root / "summary.json", result)
        return result

    def expect_through(self, boundary):
        previous = max(self.state["last_boundary"], int(self.state["started_epoch"]//900)*900)
        boundary = min(boundary, int(self.state["deadline_epoch"]//900)*900)
        for end in range(previous+900, boundary+1, 900):
            for tf in (15, 60):
                if end % (tf*60) == 0 and open_seconds(end-tf*60, end) > 0:
                    self.db.execute("INSERT OR IGNORE INTO expected VALUES (?,?)", (tf, end-tf*60))
        self.db.commit()

    async def fetch(self, tf):
        for attempt in range(2):
            started = time.time()
            self.identities.clear()
            try:
                async def request():
                    async with self.OHLCV(max_attempts=3, base_backoff=1, max_backoff=4) as client:
                        return await client.get_historical_ohlcv(SYMBOL, str(tf), bars_count=60)
                bars = await asyncio.wait_for(request(), 25)
                validate_bars(bars, tf)
                assert self.identities and all(i.get("full_name") == SYMBOL and i.get("source_id") == "BINANCE" for i in self.identities), "Wrong/unverified source"
                now = time.time()
                closed = [b for b in bars if b.timestamp+tf*60 <= now]
                for bar in closed:
                    ts = int(bar.timestamp)
                    old = self.db.execute("SELECT o,h,l,c FROM candles WHERE tf=? AND ts=?", (tf, ts)).fetchone()
                    values = (bar.open, bar.high, bar.low, bar.close)
                    if old:
                        self.state["historical_replays_deduplicated"] += 1
                    if old and any(abs(a-b) > 0.00001 for a, b in zip(old, values)):
                        self.db.execute("INSERT INTO issues VALUES (?,?,?)", (now, "closed_ohlc_revision", json.dumps({"tf": tf, "ts": ts, "old": old, "new": values})))
                    self.db.execute("INSERT OR IGNORE INTO candles VALUES (?,?,?,?,?,?,?,?)", (tf, ts, *values, bar.volume, now))
                self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (started, tf, 1, now-started, None))
                self.db.commit()
                atomic_json(self.root / f"latest-{tf}m.json", {"received_utc": datetime.now(UTC).isoformat(), "symbol_info": self.identities[-1], "candles": [b.model_dump(mode="json") for b in bars]})
                self.event("fetch_ok", timeframe_minutes=tf, candles=len(bars), closed=len(closed), latest_start_utc=datetime.fromtimestamp(closed[-1].timestamp, UTC).isoformat(), elapsed_seconds=round(now-started, 2))
                return True
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.db.execute("INSERT INTO attempts VALUES (?,?,?,?,?)", (started, tf, 0, time.time()-started, error))
                if isinstance(exc, (AssertionError, ValueError)):
                    self.db.execute("INSERT INTO issues VALUES (?,?,?)", (time.time(), "invalid_response", error))
                self.db.commit()
                self.event("fetch_error", timeframe_minutes=tf, attempt=attempt+1, error=error)
                if attempt == 0:
                    await asyncio.sleep(2)
        return False

    async def fault_checks(self):
        async def exercise(exhaust):
            async with self.OHLCV(max_attempts=3, base_backoff=1, max_backoff=4) as client:
                stream = client.get_ohlcv(SYMBOL, "15", bars_count=15)
                for _ in range(15):
                    await anext(stream)
                service = client.connection_service
                old = service.ws
                if exhaust:
                    async def blocked():
                        raise OSError("Injected per-client connection outage (does not change VM networking)")
                    service._open_websocket = blocked
                old.transport.abort()
                try:
                    recovered = await anext(stream)
                    assert not exhaust, "Injected retry exhaustion didn't terminate"
                    assert service.ws is not old and client.message_service.ws is service.ws
                    assert recovered.timestamp > 0
                except Exception as exc:
                    from tvkit.api.chart.exceptions import StreamConnectionError
                    if not (exhaust and isinstance(exc, StreamConnectionError)):
                        raise
                finally:
                    await stream.aclose()
        start = time.time()
        try:
            await asyncio.wait_for(exercise(False), 35)
            await asyncio.wait_for(exercise(True), 35)
            assert await self.fetch(15), "Fresh client didn't recover after exhausted retry budget"
            result = {"status": "PASS", "at_utc": datetime.now(UTC).isoformat(), "elapsed_seconds": round(time.time()-start, 2), "checks": ["socket abort and automatic resubscription", "local connection failures exhaust retry budget", "new polling client restores access"]}
        except Exception as exc:
            result = {"status": "FAIL", "at_utc": datetime.now(UTC).isoformat(), "error": f"{type(exc).__name__}: {exc}"}
        self.event("fault_checks", **result)
        return result

    async def run(self, preflight=False):
        self.event("process_started", process_starts=self.state["process_starts"], rss_mb=rss_mb())
        ok15 = await self.fetch(15)
        ok60 = await self.fetch(60)
        if preflight:
            result = await self.fault_checks() if ok15 and ok60 else {"status": "FAIL"}
            atomic_json(self.root / "preflight.json", result)
            self.account()
            self.summary()
            if result["status"] != "PASS":
                raise RuntimeError("Preflight failed")
            return
        if not self.state["fault_checks"]:
            self.state["fault_checks"] = await self.fault_checks()
        while True:
            self.account()
            now = time.time()
            # Fetch at boundary+10 seconds, leaving forming candles out of closed data.
            boundary = min(int((now-10)//900)*900, int(self.state["deadline_epoch"]//900)*900)
            if boundary > self.state["last_boundary"]:
                self.expect_through(boundary)
                await self.fetch(15)
                if boundary % 3600 == 0:
                    await self.fetch(60)
                self.state["last_boundary"] = boundary
            # Retry a stale/missing closed candle within its delivery budget.
            # Failures stay in the audit even when a subsequent request recovers.
            for tf, in self.db.execute("SELECT DISTINCT e.tf FROM expected e LEFT JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE c.ts IS NULL AND e.ts+e.tf*60+60> ?", (time.time(),)).fetchall():
                await self.fetch(tf)
            # Repeat fault checks during open hours, away from a candle boundary.
            until_next = ((int(now//900)+1)*900+10)-time.time()
            if self.state["observed_market_open_seconds"] >= 7200 and not self.state["live_fault_checks"] and until_next > 120 and open_seconds(time.time(), time.time()+1) > 0:
                self.state["live_fault_checks"] = await self.fault_checks()
            # Finish the last due close, allowing its full 60-second delivery budget.
            final_boundary = int(self.state["deadline_epoch"]//900)*900
            done = now >= self.state["deadline_epoch"] and now >= final_boundary+60 and self.state["last_boundary"] >= final_boundary
            timed_out = now-self.state["started_epoch"] >= MAX_WALL_SECONDS
            report = self.summary(terminal=done or timed_out)
            if done or timed_out:
                if timed_out and not done:
                    report["status"] = "INCOMPLETE_MAX_WALL_TIME"
                    atomic_json(self.root / "summary.json", report)
                self.event("test_finished", status=report["status"], observed_market_open_hours=report["observed_market_open_hours"])
                return
            pending = self.db.execute("SELECT count(*) FROM expected e LEFT JOIN candles c ON c.tf=e.tf AND c.ts=e.ts WHERE c.ts IS NULL AND e.ts+e.tf*60+60>?", (time.time(),)).fetchone()[0]
            wake_at = next_wake_at(self.state["last_boundary"], self.state["deadline_epoch"])
            await asyncio.sleep(max(1, min(15 if pending else 60, wake_at-time.time())))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(Soak(args.state_dir).run(args.preflight))
