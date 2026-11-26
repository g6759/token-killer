"""Local transport for an already running AI session; never calls a model provider."""
import argparse
from http.client import HTTPConnection
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


def request_local(base, route, payload):
    target = urlsplit(base)
    if (target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port
            or target.username or target.password or target.path not in ("", "/") or target.query or target.fragment):
        raise ValueError("--url 须为运行中的本机游戏地址，例如 http://127.0.0.1:8765")
    origin = f"http://127.0.0.1:{target.port}"
    conn = HTTPConnection("127.0.0.1", target.port, timeout=15)
    try:
        conn.request("POST", route, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                     {"Content-Type": "application/json", "Origin": origin})
        response = conn.getresponse()
        result = json.load(response)
        if response.status != 200:
            raise ValueError(result.get("error", f"HTTP {response.status}"))
        return result
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="将订阅工具内的候选提交给本机游戏")
    parser.add_argument("--url", required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start")
    start.add_argument("--pack", required=True)
    start.add_argument("--rounds", required=True, type=int, choices=(1, 3, 5))
    play = sub.add_parser("play")
    play.add_argument("--id", required=True)
    play.add_argument("--turn", type=int, required=True)
    play.add_argument("--file", type=Path, required=True)
    play.add_argument("--author", required=True)
    args = parser.parse_args()
    if args.action == "start":
        payload = {"pack_id": args.pack, "rounds": args.rounds}
    else:
        from relay import read_json
        payload = {"id": args.id, "turn": args.turn, "author": args.author,
                   "candidate": read_json(args.file, 128 * 1024)}
    result = request_local(args.url, "/api/mission/" + args.action, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        sys.exit(f"操作未完成：{exc}")
