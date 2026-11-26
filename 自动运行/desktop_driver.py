"""Local handoff to the active official Codex desktop chat. No network client."""
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import time


class DesktopDriver:
    def __init__(self, directory, model=None, timeout=300):
        self.directory = Path(directory)
        self.timeout = timeout
        self.model = model

    def _exchange(self, action, parameters):
        # A fresh ID prevents stale or copied replies from authorizing another request.
        request_id = secrets.token_hex(16)
        request = self.directory / "桌面请求.json"
        response = self.directory / f"桌面回复-{request_id}.json"
        temp = request.with_suffix(".tmp")
        temp.write_text(json.dumps({"id": request_id, "action": action, **parameters},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(request)
        print(f"等待当前 Codex 桌面会话处理 {action}：{request}", flush=True)
        deadline = time.monotonic() + self.timeout
        check_at = time.monotonic() + 45
        try:
            while time.monotonic() < deadline:
                if time.monotonic() >= check_at:
                    for location in (self.directory, Path(__file__).resolve().parent, Path(tempfile.gettempdir())):
                        if shutil.disk_usage(location).free < 10 * 1024**3:
                            raise OSError("桌面任务所用磁盘可用空间低于 10 GiB。 / Free disk space below 10 GiB.")
                    check_at = time.monotonic() + 45
                if response.exists():
                    reply = json.loads(response.read_text(encoding="utf-8-sig"))
                    if reply.get("id") != request_id or reply.get("action") != action:
                        raise ValueError("桌面回执与本次请求不匹配。")
                    return reply
                time.sleep(0.4)
            raise TimeoutError(f"桌面会话没有在时限内返回 {action}。")
        finally:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)

    def snapshot(self):
        reply = self._exchange("read_usage", {})
        limits = reply["codex"]
        windows = []
        for window in (limits.get("primary"), limits.get("secondary")):
            if window is None:
                continue
            used = window["usedPercent"]
            if type(used) not in (int, float) or not 0 <= used <= 100:
                raise ValueError("官方用量回执中的窗口无效。")
            windows.append({"minutes": window.get("windowDurationMins"), "used": used,
                            "reset": window.get("resetsAt")})
        credits = limits.get("credits") or {}
        return {"source": "Codex Desktop get_usage_limits", "auth": "chatgpt",
                "at": datetime.now(timezone.utc).isoformat(), "windows": windows,
                "weekly_used": next((w["used"] for w in windows if w["minutes"] == 10080), None),
                "ordinary_usage_allowed": reply.get("ordinaryUsageAllowed"),
                "credits": credits.get("balance"), "has_credits": credits.get("hasCredits"),
                "unlimited_credits": credits.get("unlimited")}

    def infer(self, prompt, *, expected_before):
        reply = self._exchange("candidate", {"prompt": prompt, "model": self.model,
                                             "observed_at": expected_before["at"]})
        if not isinstance(reply.get("text"), str) or not reply["text"].strip():
            raise ValueError("桌面回合未返回模型原文。")
        if self.model and reply.get("model") != self.model:
            raise ValueError("桌面回合未确认使用指定模型。")
        return {"text": reply["text"], "model": reply.get("model"), "usage": reply.get("usage"),
                "source": "Codex Desktop"}


def reply_to_pending(directory, request_id, payload):
    """Receive already redacted official fields or original candidate text from stdin."""
    directory = Path(directory)
    current = json.loads((directory / "桌面请求.json").read_text(encoding="utf-8"))
    if current["id"] != request_id:
        raise ValueError("桌面任务编号已变更。")
    if current["action"] == "read_usage":
        limits = payload["codex"]
        fields = ("primary", "secondary", "credits")
        body = {"ordinaryUsageAllowed": payload["ordinaryUsageAllowed"],
                "codex": {key: limits.get(key) for key in fields}}
    else:
        body = {key: payload.get(key) for key in ("text", "model", "usage")}
    target = directory / f"桌面回复-{request_id}.json"
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps({**body, "id": request_id, "action": current["action"]},
                              ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)


if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="将官方桌面会话的回执交给正在运行的队列")
    parser.add_argument("directory", type=Path)
    parser.add_argument("request_id")
    args = parser.parse_args()
    reply_to_pending(args.directory, args.request_id, json.load(sys.stdin))
