"""Official Codex app-server transport for quota reads and one-turn candidates."""

from collections import deque
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import math
import os
from pathlib import Path
from queue import Empty, Queue
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic


class DriverError(RuntimeError):
    """A sanitized error suitable for a local run log."""


class PreflightError(DriverError):
    """The driver definitively stopped before submitting a turn."""


_MAX_PROMPT_BYTES = 256 * 1024
_MAX_MESSAGE_BYTES = 4 * 1024 * 1024
_MIN_DISK_BYTES = 10 * 1024 ** 3
_SAFE_ITEMS = {"userMessage", "agentMessage", "reasoning", "plan"}
_USAGE_FIELDS = {
    "totalTokens": "total_tokens",
    "inputTokens": "input_tokens",
    "cachedInputTokens": "cached_input_tokens",
    "cacheWriteInputTokens": "cache_write_input_tokens",
    "outputTokens": "output_tokens",
    "reasoningOutputTokens": "reasoning_output_tokens",
}
_PASSTHROUGH_ENV = (
    "CODEX_HOME", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "LOCALAPPDATA",
    "APPDATA", "PROGRAMDATA", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
    "TEMP", "TMP", "TMPDIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "CODEX_CA_CERTIFICATE", "SSL_CERT_FILE", "SSL_CERT_DIR",
)


def _object_schema(properties):
    return {
        "type": "object", "properties": properties, "required": list(properties),
        "additionalProperties": False,
    }


_ROWS = {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}
_CANDIDATE_SCHEMA = _object_schema({
    "pack_id": {"type": "string"},
    "parent_hash": {"type": "string"},
    "solution": {"anyOf": [_object_schema({field: _ROWS}) for field in ("blocks", "poses")]},
    "handoff": {"type": "string"},
})


def _command(directory):
    executable = shutil.which("codex.exe" if os.name == "nt" else "codex")
    if not executable:
        raise DriverError("未找到已安装的官方 Codex CLI。")
    config = (
        'forced_login_method="chatgpt"',
        'model_provider="openai"',
        'chatgpt_base_url="https://chatgpt.com/backend-api/"',
        'openai_base_url=""',
        'web_search="disabled"',
        f'sqlite_home={json.dumps(directory, ensure_ascii=False)}',
        f'log_dir={json.dumps(directory, ensure_ascii=False)}',
        'notify=[]',
        'otel.exporter="none"',
        'otel.trace_exporter="none"',
        'otel.metrics_exporter="none"',
        'analytics.enabled=false',
    )
    disabled = (
        "shell_tool", "apps", "plugins", "hooks", "memories", "multi_agent_v2",
        "tool_search", "code_mode", "image_generation", "browser_use", "computer_use",
        "fast_mode", "daemon_auto_start", "goals", "remote_plugin", "in_app_browser",
    )
    command = [str(Path(executable).resolve()), "app-server", "--listen", "stdio://"]
    for value in config:
        command.extend(("-c", value))
    for feature in disabled:
        command.extend(("--disable", feature))
    return command


def _require_disk(directory):
    system_root = Path(os.environ.get("SystemDrive", "") + os.sep)
    paths = {Path.cwd(), Path(__file__).resolve().parent, Path(directory), system_root}
    for path in paths:
        try:
            free_bytes = shutil.disk_usage(path).free
        except OSError:
            raise DriverError("无法读取任务相关磁盘的剩余空间，已停止。") from None
        if free_bytes < _MIN_DISK_BYTES:
            raise DriverError(f"相关磁盘 {path.anchor} 剩余 {free_bytes / 1024 ** 3:.2f} GiB，已停止。")


def _reject_nonfinite(value):
    raise ValueError("non-finite JSON number")


class _AppServer:
    def __init__(self, process, deadline, directory):
        self.process = process
        self.deadline = deadline
        self.messages = Queue()
        self.pending = deque()
        self.next_id = 1
        self.thread_id = None
        self.turn_id = None
        self.operation = "initialize"
        self.directory = directory
        self.disk_check_at = monotonic() + 45
        self.account_changed = False
        self.mcp_started = False
        self.reader = Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while line := self.process.stdout.readline(_MAX_MESSAGE_BYTES + 1):
                if len(line) > _MAX_MESSAGE_BYTES:
                    break
                value = json.loads(line, parse_constant=_reject_nonfinite)
                if not isinstance(value, dict):
                    break
                self.messages.put(value)
        except (OSError, ValueError):
            pass
        finally:
            self.messages.put(None)

    def _send(self, message):
        # A child that stops reading must not block the parent on a full pipe.
        finished = Queue(maxsize=1)
        line = (json.dumps(message, ensure_ascii=True) + "\n").encode("ascii")

        def write():
            try:
                self.process.stdin.write(line)
                self.process.stdin.flush()
                finished.put(True)
            except (OSError, ValueError):
                finished.put(False)

        Thread(target=write, daemon=True).start()
        if not self._wait_for(finished):
            raise DriverError("无法向官方 Codex 进程写入请求。")

    def _wait_for(self, queue):
        while True:
            now = monotonic()
            if now >= self.deadline:
                raise DriverError(f"官方 Codex 请求超时（{self.operation}）。")
            if now >= self.disk_check_at:
                _require_disk(self.directory)
                self.disk_check_at = now + 45
            try:
                return queue.get(timeout=min(self.deadline, self.disk_check_at) - now)
            except Empty:
                continue

    def _interrupt(self, params=None):
        turn_id = self.turn_id or (params or {}).get("turnId")
        if self.thread_id and turn_id:
            try:
                self._send({"id": self.next_id, "method": "turn/interrupt",
                            "params": {"threadId": self.thread_id, "turnId": turn_id}})
            except DriverError:
                pass

    def _receive(self):
        event = self._wait_for(self.messages)
        if event is None:
            raise DriverError("官方 Codex 进程退出或返回了无法读取的协议消息。")
        if "id" in event and "method" in event:
            # Never answer an approval, elicitation, or token refresh request.
            self._interrupt(event.get("params"))
            if event.get("method") == "account/chatgptAuthTokens/refresh":
                raise DriverError("官方 Codex 要求刷新 ChatGPT 登录，已停止。")
            raise DriverError("官方 Codex 发起工具、审批或认证请求，已中止。")
        if event.get("method") == "account/updated":
            self.account_changed = True
        if event.get("method") == "mcpServer/startupStatus/updated":
            self.mcp_started = True
        return event

    def request(self, method, params=None, during_turn=False):
        self.operation = method
        request_id = self.next_id
        self.next_id += 1
        self._send({"id": request_id, "method": method, "params": params or {}})
        while True:
            event = self._receive()
            if event.get("id") == request_id:
                if "error" in event or not isinstance(event.get("result"), dict):
                    raise DriverError(f"官方 Codex 拒绝或未完成 {method}。")
                return event["result"]
            if during_turn:
                self.turn_guard(event)
                self.pending.append(event)

    def turn_guard(self, event):
        method = event.get("method")
        params = event.get("params") or {}
        if method in {"item/started", "item/completed"}:
            item_type = (params.get("item") or {}).get("type")
            if item_type not in _SAFE_ITEMS:
                self._interrupt(params)
                raise DriverError("检测到 Codex 工具调用，已中止。")
        if method == "account/updated":
            self._interrupt(params)
            raise DriverError("ChatGPT 登录状态在请求期间发生变化，已中止。")
        if method == "mcpServer/startupStatus/updated":
            self._interrupt(params)
            raise DriverError("检测到 Codex MCP 工具启动，已中止。")
        if method == "error":
            error = params.get("error") or {}
            code = error.get("codexErrorInfo")
            if code == "usageLimitExceeded":
                self._interrupt(params)
                raise DriverError("官方 Codex 报告套餐用量已达限制，已中止。")
            self._interrupt(params)
            raise DriverError("官方 Codex 报告本轮推理错误，已中止。")

    def turn_events(self):
        self.operation = "turn/completed"
        while True:
            event = self.pending.popleft() if self.pending else self._receive()
            self.turn_guard(event)
            yield event

    def initialize(self):
        self.request("initialize", {
            "clientInfo": {"name": "game_bench_runner", "title": None, "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "requestAttestation": False,
                             "explicitGatewayOauth": True},
        })
        self._send({"method": "initialized"})


@contextmanager
def _local_server(timeout):
    creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    environment = {
        name: os.environ[name]
        for name in _PASSTHROUGH_ENV
        if name in os.environ and (os.name != "nt" or name.isupper())
    }
    with TemporaryDirectory(prefix="codex-game-turn-") as directory:
        _require_disk(directory)
        try:
            process = subprocess.Popen(
                _command(directory), cwd=directory, env=environment, creationflags=creation_flags,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
        except OSError:
            raise DriverError("无法启动官方 Codex CLI。") from None
        server = _AppServer(process, monotonic() + timeout, directory)
        try:
            server.initialize()
            yield server, directory
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            server.reader.join(timeout=2)
            process.stdout.close()
            process.stdin.close()


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _boolean(value):
    return value if isinstance(value, bool) else None


def _window(payload):
    used = payload.get("usedPercent")
    if not _number(used) or not 0 <= used <= 100:
        raise DriverError("官方 Codex 返回了无效的额度百分比。")
    minutes = payload.get("windowDurationMins")
    reset = payload.get("resetsAt")
    return {"used": used, "minutes": minutes if type(minutes) is int and minutes > 0 else None,
            "reset": reset if type(reset) is int and reset > 0 else None}


def _snapshot(server):
    account_response = server.request("account/read", {"refreshToken": False})
    account = _mapping(account_response.get("account"))
    if account.get("type") != "chatgpt" or account_response.get("requiresOpenaiAuth") is not True:
        raise DriverError("官方 Codex CLI 未识别到可用的 ChatGPT 套餐登录。")
    server.account_changed = False
    limits_response = server.request("account/rateLimits/read")
    if server.account_changed:
        raise DriverError("ChatGPT 登录状态在额度读取期间发生变化，已停止。")
    buckets = _mapping(limits_response.get("rateLimitsByLimitId"))
    limits = _mapping(buckets.get("codex"))
    if not limits:
        legacy = _mapping(limits_response.get("rateLimits"))
        if legacy.get("limitId") not in (None, "codex"):
            raise DriverError("官方 Codex 未返回通用套餐额度窗口。")
        limits = legacy
    if not limits:
        raise DriverError("官方 Codex 未返回通用套餐额度窗口。")
    windows = [_window(limits[name]) for name in ("primary", "secondary")
               if isinstance(limits.get(name), dict)]
    week = next((window["used"] for window in windows if window["minutes"] == 10080), None)
    credits = _mapping(limits.get("credits"))
    balance = credits.get("balance")
    return {
        "at": datetime.now(timezone.utc).isoformat(), "auth": "chatgpt",
        "weekly_used": week, "windows": windows,
        "credits": balance if isinstance(balance, str) else None,
        "has_credits": _boolean(credits.get("hasCredits")),
        "unlimited_credits": _boolean(credits.get("unlimited")),
        "ordinary_usage_allowed": _boolean(limits_response.get("ordinaryUsageAllowed")),
    }


def _authorize(snapshot, monitored, stop_at, expected_before):
    if snapshot["ordinary_usage_allowed"] is not True:
        raise DriverError("普通套餐用量已停止，或官方未确认可使用。")
    if not snapshot["windows"] or any(window["used"] >= 100 for window in snapshot["windows"]):
        raise DriverError("套餐额度窗口已用尽或缺失。")
    if snapshot["weekly_used"] is None or snapshot["weekly_used"] >= stop_at:
        raise DriverError("套餐周额度达到本轮停止线，或周窗口未确认。")
    balance = snapshot["credits"]
    try:
        amount = Decimal(balance) if balance is not None else None
    except InvalidOperation:
        amount = None
    if (amount is None or not amount.is_finite() or amount < 0
            or snapshot["has_credits"] is None or snapshot["unlimited_credits"] is not False):
        raise DriverError("用量余额状态未知或无限，已停止。")
    if (amount > 0 or snapshot["has_credits"]) and not monitored:
        raise DriverError("检测到可用余额，默认停止以避免消耗充值额度。")
    if expected_before is None:
        return
    credit_fields = ("credits", "has_credits", "unlimited_credits")
    if any(snapshot[key] != expected_before.get(key) for key in credit_fields):
        raise DriverError("套餐余额状态与本轮首次读数不同，已停止。")
    first_windows = [(w.get("minutes"), w.get("reset")) for w in expected_before.get("windows", [])]
    current_windows = [(w["minutes"], w["reset"]) for w in snapshot["windows"]]
    if current_windows != first_windows:
        raise DriverError("套餐额度窗口与本轮首次读数不同，已停止。")


class CodexDriver:
    def __init__(self, model=None, *, monitored=False, timeout=180, stop_at=90):
        if model is not None and (not isinstance(model, str)
                                  or not re.fullmatch(r"[A-Za-z0-9._-]+", model)):
            raise DriverError("Codex 模型标识无效。")
        if not _number(timeout) or timeout <= 0:
            raise DriverError("Codex 请求超时必须为正秒数。")
        if not _number(stop_at) or not 0 < stop_at <= 100:
            raise DriverError("套餐周额度停止线必须大于 0 且不超过 100。")
        self.model = model
        self.monitored = monitored is True
        self.timeout = timeout
        self.stop_at = stop_at

    def snapshot(self):
        """Query official account and quota state without submitting any turn."""
        with _local_server(min(self.timeout, 30)) as (server, _):
            return _snapshot(server)

    def infer(self, prompt: str, *, expected_before=None):
        """Submit one ephemeral standard-tier turn and return the original response."""
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES:
            raise PreflightError("候选请求不能为空或超过 256 KiB。")
        submitting = False
        try:
            with _local_server(self.timeout) as (server, directory):
                _authorize(_snapshot(server), self.monitored, self.stop_at, expected_before)
                model = self._open_thread(server, directory)
                submitting = True
                turn = server.request("turn/start", {
                    "threadId": server.thread_id,
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "environments": [], "runtimeWorkspaceRoots": [], "serviceTierForTurn": "default",
                    "outputSchema": _CANDIDATE_SCHEMA,
                }, during_turn=True)
                server.turn_id = _mapping(turn.get("turn")).get("id")
                if not isinstance(server.turn_id, str) or not server.turn_id:
                    raise DriverError("官方 Codex 返回了无效的轮次标识。")
                return self._collect(server, model)
        except DriverError as error:
            if not submitting:
                raise PreflightError(str(error)) from None
            raise

    def _open_thread(self, server, directory):
        params = {
            "modelProvider": "openai", "allowProviderModelFallback": False,
            "cwd": directory, "runtimeWorkspaceRoots": [], "environments": [],
            "approvalPolicy": "never", "sandbox": "read-only",
            "dynamicTools": [], "ephemeral": True,
            "baseInstructions": "Solve only the supplied game puzzle. Answer with a single JSON candidate.",
            "developerInstructions": "Use no tools. Do not perform file, shell, network, or external actions.",
        }
        if self.model:
            params["model"] = self.model
        started = server.request("thread/start", params)
        model = started.get("model")
        sandbox = _mapping(started.get("sandbox"))
        thread = _mapping(started.get("thread"))
        server.thread_id = thread.get("id")
        if started.get("modelProvider") != "openai" or not isinstance(model, str) or not model:
            raise DriverError("官方 Codex 未确认使用 OpenAI ChatGPT 模型。")
        if self.model is not None and model != self.model:
            raise DriverError("官方 Codex 返回的模型与指定模型不一致。")
        if (not isinstance(server.thread_id, str) or not server.thread_id or thread.get("ephemeral") is not True
                or thread.get("environments") != [] or started.get("approvalPolicy") != "never"
                or sandbox.get("type") != "readOnly" or sandbox.get("networkAccess") is not False):
            raise DriverError("官方 Codex 未确认空执行环境、无审批、只读及临时线程设置。")
        if server.mcp_started or server.account_changed:
            raise DriverError("检测到 Codex 工具启动或账户变化，已停止。")
        return model

    def _collect(self, server, model):
        texts = {}
        usage = {}
        for event in server.turn_events():
            method = event.get("method")
            params = _mapping(event.get("params"))
            if params.get("threadId") != server.thread_id:
                continue
            if method == "turn/completed":
                turn = _mapping(params.get("turn"))
                if turn.get("id") != server.turn_id:
                    continue
                if turn.get("status") != "completed":
                    error = _mapping(turn.get("error"))
                    if error.get("codexErrorInfo") == "usageLimitExceeded":
                        raise DriverError("官方 Codex 报告套餐用量已达限制。")
                    raise DriverError("官方 Codex 未成功完成此轮。")
                break
            if params.get("turnId") != server.turn_id:
                continue
            if method == "item/completed":
                item = _mapping(params.get("item"))
                if item.get("type") == "agentMessage" and item.get("phase") in (None, "final_answer"):
                    text = item.get("text")
                    if isinstance(text, str) and isinstance(item.get("id"), str):
                        texts[item["id"]] = text
            elif method == "thread/tokenUsage/updated":
                token_usage = _mapping(params.get("tokenUsage"))
                total = _mapping(token_usage.get("total")) or _mapping(token_usage.get("last"))
                usage = {output_key: value for key, output_key in _USAGE_FIELDS.items()
                         if type(value := total.get(key)) is int and value >= 0}
        return {"text": "\n".join(texts.values()), "usage": usage, "model": model}
