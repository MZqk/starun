import hashlib
import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import JSONResponse

RATE_LIMIT_CAPACITY = 60
RATE_LIMIT_REFILL_SECONDS = 60.0
RATE_LIMIT_MAX_ENTRIES = 10_000


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class TokenBucketLimiter:
    def __init__(
        self,
        *,
        capacity: int,
        refill_seconds: float,
        max_entries: int,
    ) -> None:
        if capacity <= 0 or refill_seconds <= 0 or max_entries <= 0:
            raise ValueError("rate limit settings must be positive")
        self.capacity = capacity
        self.refill_seconds = refill_seconds
        self.max_entries = max_entries
        self._buckets: OrderedDict[tuple[str, str], _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._buckets)

    def check(self, key: tuple[str, str]) -> int | None:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.pop(key, None)
            if bucket is None:
                self._prune_expired(now)
                if len(self._buckets) >= self.max_entries:
                    return max(1, math.ceil(self.refill_seconds))
                bucket = _Bucket(float(self.capacity), now)
            else:
                elapsed = max(0.0, now - bucket.updated_at)
                refill_rate = self.capacity / self.refill_seconds
                bucket.tokens = min(
                    float(self.capacity),
                    bucket.tokens + elapsed * refill_rate,
                )
                bucket.updated_at = now
            self._buckets[key] = bucket
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return None
            refill_rate = self.capacity / self.refill_seconds
            return max(1, math.ceil((1.0 - bucket.tokens) / refill_rate))

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _prune_expired(self, now: float) -> None:
        expired = [
            key
            for key, bucket in self._buckets.items()
            if now - bucket.updated_at >= self.refill_seconds
        ]
        for key in expired:
            self._buckets.pop(key, None)


_client_limiter = TokenBucketLimiter(
    capacity=RATE_LIMIT_CAPACITY,
    refill_seconds=RATE_LIMIT_REFILL_SECONDS,
    max_entries=RATE_LIMIT_MAX_ENTRIES,
)
_ip_limiter = TokenBucketLimiter(
    capacity=RATE_LIMIT_CAPACITY,
    refill_seconds=RATE_LIMIT_REFILL_SECONDS,
    max_entries=RATE_LIMIT_MAX_ENTRIES,
)


def rate_limit_response(
    request: Request,
    client_id: str | None,
    action: str,
) -> JSONResponse | None:
    client_host = request.client.host if request.client is not None else ""
    ip_identity = hashlib.sha256(client_host.encode()).hexdigest()
    retry_after = _ip_limiter.check((ip_identity, action))
    if retry_after is None and client_id:
        client_identity = hashlib.sha256(client_id.encode()).hexdigest()
        retry_after = _client_limiter.check((client_identity, action))
    if retry_after is None:
        return None
    return JSONResponse(
        status_code=429,
        content={
            "error_code": "rate_limit_exceeded",
            "message": "Too many requests. Please retry later.",
            "retryable": True,
            "quota_charged": False,
        },
        headers={"Retry-After": str(retry_after)},
    )


def reset_rate_limiters() -> None:
    _client_limiter.reset()
    _ip_limiter.reset()
