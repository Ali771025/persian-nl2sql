from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RedisLease:
    lock: object
    token: int


class RedisKnowledgeCache:
    """Redis cache and distributed ingestion lock with lazy connectivity."""

    name = "redis"

    def __init__(self, url: str, *, key_prefix: str = "nl2sql"):
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("redis package is required for distributed knowledge mode") from exc
        self.url = url
        self.key_prefix = key_prefix.strip(":")
        self.client = redis.Redis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=5,
            health_check_interval=30,
        )

    def _key(self, value: str) -> str:
        return f"{self.key_prefix}:{value}"

    def get_json(self, key: str) -> object | None:
        value = self.client.get(self._key(key))
        return json.loads(value) if value else None

    def set_json(self, key: str, value: object, ttl_seconds: int) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        self.client.setex(self._key(key), ttl_seconds, encoded)

    def acquire_lock(self, name: str, timeout_seconds: int) -> object | None:
        lock = self.client.lock(
            self._key(f"lock:{name}"),
            timeout=timeout_seconds,
            blocking_timeout=0,
            thread_local=False,
        )
        if not lock.acquire(blocking=False):
            return None
        token = int(self.client.incr(self._key(f"fence:{name}")))
        return RedisLease(lock=lock, token=token)

    @staticmethod
    def release_lock(lock: object) -> None:
        try:
            target = lock.lock if isinstance(lock, RedisLease) else lock
            target.release()  # type: ignore[attr-defined]
        except Exception:
            # Expired locks are already safe to ignore. A new owner has a new token.
            return

    @staticmethod
    def renew_lock(lock: object, timeout_seconds: int) -> bool:
        try:
            target = lock.lock if isinstance(lock, RedisLease) else lock
            return bool(
                target.extend(  # type: ignore[attr-defined]
                    additional_time=timeout_seconds,
                    replace_ttl=True,
                )
            )
        except Exception:
            return False

    @staticmethod
    def fencing_token(lock: object) -> int:
        return lock.token if isinstance(lock, RedisLease) else 0

    def publish_snapshot(self, payload: dict[str, object], fencing_token: int) -> bool:
        """Publish the commit marker only when the caller owns the newest lease."""
        script = """
        local latest_lease = tonumber(redis.call('GET', KEYS[1]) or '-1')
        local published = tonumber(redis.call('GET', KEYS[2]) or '-1')
        local incoming = tonumber(ARGV[1])
        if incoming < latest_lease or incoming < published then return 0 end
        redis.call('SET', KEYS[2], ARGV[1])
        redis.call('SET', KEYS[3], ARGV[2])
        return 1
        """
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        accepted = self.client.eval(
            script,
            3,
            self._key("fence:knowledge-ingestion"),
            self._key("snapshot:published-fence"),
            self._key("snapshot:active"),
            int(fencing_token),
            encoded,
        )
        return bool(accepted)

    def active_snapshot(self) -> dict[str, object] | None:
        value = self.get_json("snapshot:active")
        return value if isinstance(value, dict) else None

    def status(self) -> dict[str, object]:
        try:
            connected = bool(self.client.ping())
            info: dict[str, Any] = self.client.info(section="server") if connected else {}
            return {
                "enabled": True,
                "connected": connected,
                "backend": "redis",
                "version": str(info.get("redis_version", "")),
            }
        except Exception as exc:
            return {
                "enabled": True,
                "connected": False,
                "backend": "redis",
                "error": type(exc).__name__,
            }

    def close(self) -> None:
        self.client.close()
