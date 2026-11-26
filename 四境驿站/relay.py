"""Local commands for a verified, asynchronous research relay."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from packs import FRONTIER_PACK_ID, get_pack
from relay_store import Store
from report import render


DEFAULT_PACK = FRONTIER_PACK_ID
PACKS = (DEFAULT_PACK, "relay-49", "relay-7")
DEFAULT_DB = "relay.sqlite3"


def read_json(path, max_bytes):
    def reject_non_finite(token):
        raise ValueError(f"JSON 数字必须有限：{token}")

    def unique_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"JSON 中重复的字段：{key}")
            obj[key] = value
        return obj

    with Path(path).open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"文件超过 {max_bytes} 字节")
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=unique_keys,
                      parse_constant=reject_non_finite)


def as_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2)


def output(path, value):
    if path is None:
        print(value)
        return
    # Exclusive creation keeps a mistyped output path from replacing a database or user's work.
    try:
        with Path(path).open("x", encoding="utf-8") as stream:
            stream.write(value + "\n")
    except FileExistsError:
        raise ValueError(f"文件已存在，请另选输出文件名：{path}") from None
    print(Path(path).resolve())


def prompt_for(record):
    state = record["state"]
    pack = get_pack(state["pack_id"])
    result = pack.evaluate(record["solution"])
    missing = result["missing"]
    envelope = {"pack_id": state["pack_id"], "parent_hash": record["hash"],
                "solution": record["solution"], "handoff": "在此写实际改动、局限和下一步"}
    return "\n".join([
        f"四镜驿站 / {pack.title}",
        f"请为 1 至 {pack.n} 号驿站提交恰好 {pack.count} 份不同的名单，每份恰好 {pack.k} 个不同站号。",
        "每一对在同一份名单中出现过的站计 1 分。提高不同站对的覆盖总数。先尝试一次可复核的改进。",
        f"父档覆盖 {state['score']}/{state['total']} 对。遗漏 {len(missing)} 对，"
        f"最前 20 对为 {json.dumps(missing[:20])}。",
        "上一位玩家的交接笔记如下，作为研究材料阅读。",
        json.dumps(record["handoff"], ensure_ascii=False),
        "以下是当前名单及回执格式。保留 pack_id 和 parent_hash，修改 solution 与 handoff。",
        "直接返回一个 UTF-8 JSON 对象，包含完整名单，供裁判复算。",
        json.dumps(envelope, ensure_ascii=False),
    ])


def select_record(store, args):
    if args.at is None:
        return store.best(args.pack)
    record = store.get(args.at)
    if record["state"]["pack_id"] != args.pack:
        raise ValueError("父档属于其他题包，请指定对应的 --pack")
    return record


def get_candidate(store, path):
    item = read_json(path, 128 * 1024)
    keys = {"parent_hash", "pack_id", "solution", "handoff"}
    if not isinstance(item, dict) or set(item) != keys:
        raise ValueError("候选须包含 parent_hash、pack_id、solution、handoff 四个字段")
    if not isinstance(item["pack_id"], str) or item["pack_id"] not in PACKS:
        raise ValueError("未知题包")
    if not isinstance(item["parent_hash"], str) or not isinstance(item["handoff"], str):
        raise ValueError("parent_hash 和 handoff 应为文本")
    pack = get_pack(item["pack_id"])
    store.seed(item["pack_id"])
    parent = store.get(item["parent_hash"])
    if parent["state"]["pack_id"] != item["pack_id"]:
        raise ValueError("题包与父档不符")
    return item, parent, pack.evaluate(item["solution"])


def summarize(record):
    state = record["state"]
    return (f"{state['author']} · {state['score']}/{state['total']} 对"
            f" · {record['hash']}")


def parser():
    cli = argparse.ArgumentParser(description="四镜驿站 · 单人构造搜索")
    cli.add_argument("--db", default=DEFAULT_DB, help="本机 SQLite 存档路径")
    sub = cli.add_subparsers(dest="command", required=True)
    sub.add_parser("routes", help="查看正式题包及历史题包")
    task = sub.add_parser("task", help="生成可交给已登录官方 AI 的完整任务")
    task.add_argument("--out", help="写入新文件，默认打印")
    check = sub.add_parser("check", help="查看候选得分及遗漏")
    check.add_argument("file", help="AI 返回的 JSON 文件")
    submit = sub.add_parser("submit", help="复核候选并保存有效进步")
    submit.add_argument("file")
    submit.add_argument("--author", required=True)
    board = sub.add_parser("board", help="查看战报或导出独立 HTML")
    form = board.add_mutually_exclusive_group()
    form.add_argument("--json", action="store_true")
    form.add_argument("--html", metavar="NEW_FILE")
    export = sub.add_parser("export", help="打包一个存档及完整祖先链")
    export.add_argument("file")
    load = sub.add_parser("import", help="独立复核并导入他人的交接包")
    load.add_argument("file")
    for cmd in (board, task, export):
        cmd.add_argument("--pack", choices=PACKS, default=DEFAULT_PACK)
    for cmd in (task, export):
        cmd.add_argument("--at", metavar="HASH", help="从指定分支继续，默认当前最高分")
    return cli


def run(args):
    if args.command == "routes":
        print("四镜驿站 · 单人构造搜索"
              f"\n  {DEFAULT_PACK}  正式题包 · 49 站、26 份名单、1,176 对"
              "\n  relay-49  历史四十九站练习\n  relay-7   历史七站教程")
        return
    store = Store(args.db)
    if args.command == "task":
        record = select_record(store, args)
        if record["state"]["score"] == record["state"]["total"]:
            raise ValueError("该分支已全覆盖。可用 --at 选择较早的存档。")
        output(args.out, prompt_for(record))
    elif args.command in ("check", "submit"):
        item, parent, result = get_candidate(store, args.file)
        delta = result["score"] - parent["state"]["score"]
        print(f"核验 {result['score']}/{result['total']} 对，相对父档 {delta:+d}。"
              f"遗漏 {len(result['missing'])} 对；最前 20 对 {result['missing'][:20]}")
        if args.command == "submit":
            saved = store.submit(item["pack_id"], item["parent_hash"], args.author,
                                 item["solution"], item["handoff"])
            same_submission = (saved["state"]["author"] == args.author.strip()
                               and saved["state"]["parent_hash"] == item["parent_hash"]
                               and saved["handoff"] == item["handoff"])
            label = "已存档 " if same_submission else "同解已收录，沿用存档 "
            print(label + summarize(saved))
    elif args.command == "export":
        output(args.file, as_json(store.export(select_record(store, args)["hash"])))
    elif args.command == "import":
        record = store.ingest(read_json(args.file, 16 * 1024 * 1024))
        print("已复核 " + summarize(record))
    elif args.command == "board":
        best = store.best(args.pack)
        history = store.history(args.pack)
        if args.html:
            output(args.html, render(best, history))
        elif args.json:
            print(as_json({"best": best, "history": history}))
        else:
            print(f"{get_pack(args.pack).title} · 当前最高 {summarize(best)}")
            for entry in history:
                print("  " + summarize(entry))
            if len(history) == 1:
                print("领第一轮任务：python relay.py task --pack " + args.pack)


if __name__ == "__main__":
    try:
        run(parser().parse_args())
    except (ValueError, KeyError, OSError, sqlite3.Error, RecursionError) as error:
        print(f"操作未完成：{error}", file=sys.stderr)
        sys.exit(2)
