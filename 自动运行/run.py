"""Run a bounded, resumable queue of Codex research submissions."""
import argparse
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from codex_driver import CodexDriver, PreflightError
from desktop_driver import DesktopDriver
from evidence import build_report, rehearsal_candidate
from workspace_io import ROOT, check_disk, exclusive, now, read, save, write_file
from 本地裁判 import LocalJudge


PACKS = ("relay-49-frontier", "square-18", "square-19")


def gate(snapshot, stop_at, monitored):
    if snapshot.get("auth") != "chatgpt" or snapshot.get("ordinary_usage_allowed") is not True:
        raise ValueError("未确认官方 ChatGPT 套餐可用。")
    windows = snapshot.get("windows") or []
    if not windows or any(not isinstance(w.get("minutes"), int) or not isinstance(w.get("reset"), int)
                          or w.get("used") is None or w["used"] >= stop_at for w in windows):
        raise ValueError(f"套餐窗口缺少读数，或已达到本批次的 {stop_at}% 停止线。")
    try:
        credits = Decimal(str(snapshot["credits"]))
        if not credits.is_finite() or credits < 0:
            raise ValueError
    except (KeyError, ValueError, InvalidOperation):
        raise ValueError("无法核对充值余额。") from None
    if not monitored:
        raise ValueError("官方个人 Codex 尚无此工具可验证的仅套餐扣费锁；严格模式停在读数。逐轮监控属于单独选择。")


def same_period(before, after):
    def keys(s):
        return sorted((w["minutes"], w["reset"]) for w in s["windows"]
                      if type(w.get("minutes")) is int and type(w.get("reset")) is int)
    first, last = keys(before), keys(after)
    return bool(first and first == last and len(first) == len(before["windows"]) == len(after["windows"]))


def compare_snapshots(before, after):
    if Decimal(str(before["credits"])) != Decimal(str(after["credits"])):
        raise ValueError("两次读数间账户 credits 余额有变动，停止本批次；变动来源待核实。")
    if not same_period(before, after):
        raise ValueError("两次读数间额度窗口已重置或窗口来源不明，停止本批次。")


def decide(judge, text, author):
    # Keep malformed model output as a spent attempt, with the verbatim reply in the ledger.
    try:
        candidate = json.loads(text)
        if not isinstance(candidate, dict):
            raise ValueError("候选顶层应为 JSON 对象。")
    except (ValueError, TypeError) as error:
        metric = judge.metric()
        return {"accepted": False, "message": f"JSON 无法验收：{error}", "before": metric,
                "after": metric, "checkpoint": None}
    return judge.grade(candidate, author)


def execute(directory, plan, driver, skip_uncertain=False):
    manifest = directory / "批次.json"
    if manifest.exists() and read(manifest) != plan:
        raise ValueError("该目录已有不同参数的批次。沿用原参数续跑，或通过 --out 指定新目录。")
    if not manifest.exists():
        save(manifest, plan)
    records = []
    real = plan["mode"] == "run"
    stop_at = plan["stop_at"]
    monitored = plan["monitored"]
    last_snapshot = None
    author = "Codex 订阅自动任务" if real else "离线演练"
    note = "批次完成。"
    try:
        # Rebuild only from committed replies. The JSON ledger is authoritative if power
        # fails between updating a judge database and persisting its receipt.
        with tempfile.TemporaryDirectory(prefix="judge-", dir=directory) as scratch:
            for pack in plan["packs"]:
                judge = LocalJudge(Path(scratch), pack)
                feedback = ""
                for turn in range(1, plan["rounds"] + 1):
                    check_disk(directory)
                    path = directory / f"{pack}-{turn:02d}.json"
                    rec = read(path) if path.exists() else {"pack": pack, "turn": turn, "status": "ready"}
                    records.append(rec)
                    if rec["status"] in ("inflight", "uncertain"):
                        if not skip_uncertain:
                            raise ValueError(f"{pack} 第 {turn} 轮已送出或可能已送出，结果未知，未重发。核查后用 --skip-uncertain 跳过。")
                        rec["status"] = "skipped"
                        save(path, rec)
                    if rec["status"] == "skipped":
                        feedback = "上一轮请求结果不明，未取得候选。"
                        continue
                    if rec["status"] == "done":
                        rebuilt = decide(judge, rec["model"]["text"], author)
                        if rebuilt["after"] != rec["grade"]["after"] or rebuilt["accepted"] != rec["grade"]["accepted"]:
                            raise ValueError("重放与既有裁判回执不一致，已暂停。")
                        if real:
                            compare_snapshots(rec["before"], rec["after"])
                            last_snapshot = rec["after"]
                        if rebuilt.get("checkpoint") and rebuilt["after"]["hash"] == rebuilt["checkpoint"]["tip"]:
                            save(directory / f"{pack}-最新存档.json", rebuilt["checkpoint"])
                        feedback = rec["feedback"]
                        continue
                    if rec["status"] == "ready":
                        prompt = judge.task()
                        if not prompt.strip():
                            records.pop()
                            break
                        request = ("只返回一个完整候选 JSON 对象，使用题面当前父档，交接说明写明本轮策略。\n"
                                   + (f"上一轮的尝试及结果：{feedback}\n" if feedback else "") + prompt)
                        rec.update(prompt=request, metric_before=judge.metric())
                        save(path, rec)
                        if real:
                            before = driver.snapshot()
                            rec.update(before=before)
                            save(path, rec)
                            gate(before, stop_at, monitored)
                            if last_snapshot:
                                compare_snapshots(last_snapshot, before)
                        rec.update(status="inflight", started=now())
                        save(path, rec)
                        tick = time.monotonic()
                        try:
                            if real:
                                response = driver.infer(request, expected_before=before)
                            else:
                                candidate = rehearsal_candidate(pack, turn, prompt)
                                response = {"text": json.dumps(candidate, ensure_ascii=False), "usage": None}
                        except PreflightError as error:
                            rec.update(status="ready", error=str(error), ended=now())
                            save(path, rec)
                            raise
                        except Exception as error:
                            rec.update(status="uncertain", error=str(error), ended=now())
                            save(path, rec)
                            raise
                        rec.update(model=response, model_seconds=round(time.monotonic() - tick, 4),
                                   ended=now(), status="responded")
                        save(path, rec)
                        fresh = True
                    else:
                        fresh = False
                    if rec["status"] != "responded":
                        raise ValueError(f"批次记录状态异常：{rec['status']}")
                    meter_error = None
                    if real and not rec.get("after"):
                        try:
                            rec.update(after=driver.snapshot(), delayed_after=not fresh)
                        except Exception as error:
                            meter_error = error
                    grade = decide(judge, rec["model"]["text"], author)
                    try:
                        strategy = str(json.loads(rec["model"]["text"]).get("handoff", ""))[:450]
                    except (TypeError, ValueError, AttributeError):
                        strategy = ""
                    feedback = (strategy + " " + str(grade["message"]))[:800]
                    rec.update(grade=grade, feedback=feedback, status="responded" if meter_error else "done")
                    save(path, rec)
                    if grade.get("checkpoint") and grade["after"]["hash"] == grade["checkpoint"]["tip"]:
                        save(directory / f"{pack}-最新存档.json", grade["checkpoint"])
                    if meter_error:
                        raise RuntimeError(f"已保存模型输出及裁判结果；后读数失败，恢复时只补读数：{meter_error}") from meter_error
                    if real:
                        last_snapshot = rec["after"]
                        compare_snapshots(rec["before"], last_snapshot)
                        gate(last_snapshot, stop_at, monitored)
                    print(f"{pack} {turn}/{plan['rounds']}  {grade['message']}", flush=True)
        if any(r["status"] == "skipped" for r in records):
            note = "队列已结束，结果未知的请求单列为跳过。"
    except (Exception, KeyboardInterrupt) as error:
        note = str(error) or "操作中断，已落盘的轮次可以恢复。"
        raise
    finally:
        write_file(directory / "对照报告.md", build_report(directory, records, plan, note))


def main():
    parser = argparse.ArgumentParser(description="用官方 Codex 订阅串行完成可复核任务，保存每轮证据。")
    parser.add_argument("mode", choices=["check", "replay", "run"])
    parser.add_argument("--out", type=Path, help="批次输出与断点目录")
    parser.add_argument("--packs", nargs="+", choices=PACKS, default=list(PACKS[:2]))
    parser.add_argument("--rounds", type=int, default=5, choices=range(1, 6))
    parser.add_argument("--model", help="官方 Codex 模型，缺省使用驱动的官方默认值")
    parser.add_argument("--stop-at", type=int, choices=range(1, 100), default=90, metavar="1..99")
    parser.add_argument("--monitored", action="store_true", help="接受单次请求越过套餐线的风险后选择；逐轮读数只能监测")
    parser.add_argument("--desktop", action="store_true", help="由当前官方 Codex 桌面会话自动读取官方用量并接题")
    parser.add_argument("--skip-uncertain", action="store_true", help="人工核对后跳过结果未知的请求")
    args = parser.parse_args()
    if len(set(args.packs)) != len(args.packs):
        parser.error("题包不能重复。")
    if args.mode != "run" and (args.monitored or args.skip_uncertain):
        parser.error("计费与跳过选项仅适用于 run。")
    if args.mode == "replay" and args.desktop:
        parser.error("离线演练无需模型入口。")
    default_name = "run-monitored" if args.monitored else args.mode
    if args.desktop:
        default_name += "-desktop"
    directory = (args.out or ROOT / "验收" / "自动运行" / default_name).resolve()
    with exclusive(directory):
        check_disk(directory)
        driver = None
        if args.desktop:
            driver = DesktopDriver(directory, args.model)
        elif args.mode != "replay":
            driver = CodexDriver(model=args.model, monitored=args.monitored, stop_at=args.stop_at)
        try:
            if args.mode == "check":
                snapshot = driver.snapshot()
                save(directory / "官方读数.json", snapshot)
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
                print(directory / "官方读数.json")
                return
            plan = {"version": 1, "mode": args.mode, "packs": args.packs, "rounds": args.rounds,
                    "model": args.model, "stop_at": args.stop_at, "monitored": args.monitored}
            if args.desktop:
                plan["source"] = "Codex Desktop"
            try:
                execute(directory, plan, driver, args.skip_uncertain)
            finally:
                if (directory / "对照报告.md").exists():
                    print(directory / "对照报告.md")
        finally:
            if driver is not None and hasattr(driver, "close"):
                driver.close()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, TimeoutError) as exc:
        sys.exit(f"暂停：{exc}")
