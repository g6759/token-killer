"""Fail-closed, shared ordinary-plan gate using the official read-only RPC.

No credit balances or account identifiers leave this module. Missing explicit
ordinaryUsageAllowed is UNKNOWN, even when a CLI reports remaining percentages.
This client gate is not an account billing switch and cannot recall sent work.
"""
from copy import deepcopy
from datetime import datetime, timezone
import math
from threading import Lock
from time import monotonic, time

SOURCE = "Codex app-server account/rateLimits/read"
MAX_AGE_SECONDS = 30
MESSAGES = {
    "available": ("普通套餐可用。", "Ordinary plan available."),
    "blocked": ("普通套餐不可用，已停止模型请求。", "Ordinary plan unavailable; model requests stopped."),
    "unknown": ("无法确认普通套餐可用，模型请求已暂停。", "Ordinary plan unconfirmed; model requests paused."),
}


class QuotaError(ValueError):
    """No new inference may be dispatched."""


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _reset_epoch(value):
    if _number(value):
        return float(value)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                return parsed.timestamp()
        except ValueError:
            pass
    return None


def _result(status, windows, sampled_at, reason):
    zh, en = MESSAGES[status]
    return {"status": status, "windows": windows, "sampled_at": sampled_at,
            "message_zh": zh, "message_en": en, "message": f"{zh} / {en}",
            "reason": reason, "source": SOURCE, "max_age_seconds": MAX_AGE_SECONDS}


def normalize_allowance(payload, *, sampled_at=None):
    """Only all-valid, unexpired applicable windows plus explicit True permit work."""
    sampled_at = sampled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not isinstance(payload, dict):
        return _result("unknown", [], sampled_at, "missing_reading")
    ordinary = payload.get("ordinaryUsageAllowed")
    buckets = payload.get("rateLimitsByLimitId")
    if isinstance(buckets, dict) and "codex" in buckets:
        bucket = buckets["codex"]
    else:
        bucket = payload.get("rateLimits")
    windows, valid = [], isinstance(bucket, dict)
    if valid:
        if bucket.get("limitId") not in (None, "codex"):
            valid = False
        for name in ("primary", "secondary"):
            window = bucket.get(name)
            if window is None:
                continue
            if not isinstance(window, dict):
                valid = False
                continue
            used, minutes, reset = window.get("usedPercent"), window.get("windowDurationMins"), window.get("resetsAt")
            reset_epoch = _reset_epoch(reset)
            if (not _number(used) or not 0 <= used <= 100
                    or type(minutes) is not int or minutes <= 0
                    or reset_epoch is None or reset_epoch <= time()):
                valid = False
                continue
            windows.append({"minutes": minutes, "remaining_percent": 100 - used, "resets_at": reset})
    if ordinary is False:
        return _result("blocked", windows, sampled_at, "ordinary_disallowed")
    if ordinary is not True:
        return _result("unknown", windows, sampled_at, "ordinary_permission_missing")
    if not valid or not windows:
        return _result("unknown", windows, sampled_at, "invalid_or_missing_windows")
    if any(w["remaining_percent"] <= 0 for w in windows) or bucket.get("spendControlReached") is True:
        return _result("blocked", windows, sampled_at, "plan_window_exhausted")
    return _result("available", windows, sampled_at, None)


def read_allowance(*, timeout=8):
    """Use the existing official account reader; never infer ordinary permission."""
    from weekly_meter import _read_app_server
    try:
        return normalize_allowance(_read_app_server(timeout))
    except Exception:
        result = normalize_allowance(None)
        result["reason"] = "official_read_failed"
        return result


class QuotaGate:
    """One serialized reading shared by all lanes; failures replace old success."""
    def __init__(self, reader=read_allowance, *, clock=monotonic, max_age=MAX_AGE_SECONDS):
        self.reader, self.clock, self.max_age = reader, clock, max_age
        self._state_lock, self._read_lock = Lock(), Lock()
        self._sample_clock = None
        self._value = _result("unknown", [], None, "not_checked")

    def _fresh(self):
        return (self._sample_clock is not None and 0 <= self.clock() - self._sample_clock < self.max_age
                and all((_reset_epoch(w["resets_at"]) or 0) > time() for w in self._value["windows"]))

    def snapshot(self):
        with self._state_lock:
            value = deepcopy(self._value)
            if not self._fresh():
                value = _result("unknown", value["windows"], value["sampled_at"], "stale_or_unchecked")
            return value

    def _read(self):
        with self._state_lock:
            self._value = _result("unknown", self._value["windows"], self._value["sampled_at"], "refreshing")
            self._sample_clock = None
        try:
            value = self.reader()
            if not isinstance(value, dict) or value.get("status") not in MESSAGES:
                value = normalize_allowance(None)
        except Exception:
            value = normalize_allowance(None)
        with self._state_lock:
            self._value, self._sample_clock = deepcopy(value), self.clock()
        return deepcopy(value)

    def refresh(self):
        with self._read_lock:
            return self._read()

    def require(self, *, force=False):
        with self._read_lock:
            with self._state_lock:
                fresh = self._fresh()
                value = deepcopy(self._value)
            if force or not fresh:
                value = self._read()
        if value["status"] != "available":
            raise QuotaError(value["message"])
        return value


_SHARED_GATE = QuotaGate(reader=lambda: read_allowance())


def get_shared_gate():
    """Lab and both standalone game services use the same process-local reading."""
    return _SHARED_GATE
