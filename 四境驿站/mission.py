"""Bounded sets of judged submissions issued by a running local game."""
from collections import OrderedDict
from hashlib import sha256
import json
from pathlib import Path
from secrets import token_hex


ROUND_LIMITS = (1, 3, 5)


class MissionBook:
    """Access is serialized by the local HTTP server's submit lock."""

    def __init__(self):
        self.tickets = OrderedDict()

    def start(self, pack_id, rounds):
        if type(rounds) is not int or rounds not in ROUND_LIMITS:
            raise ValueError("请选择 1、3 或 5 轮")
        key = token_hex(12)
        if len(self.tickets) >= 256:
            self.tickets.popitem(last=False)
        self.tickets[key] = {"pack_id": pack_id, "rounds": rounds, "results": {}}
        return {"id": key, "pack_id": pack_id, "remaining": rounds, "rounds": rounds}

    def play(self, key, turn, payload, judge):
        if not isinstance(key, str) or key not in self.tickets:
            raise ValueError("任务已结束或本机服务已重启；此前有效成果仍在存档中")
        ticket = self.tickets[key]
        if type(turn) is not int or not 1 <= turn <= ticket["rounds"]:
            raise ValueError("已达到本次轮数上限或轮号无效")
        candidate = payload.get("candidate") if isinstance(payload, dict) else None
        if not isinstance(candidate, dict) or candidate.get("pack_id") != ticket["pack_id"]:
            raise ValueError("候选与本次任务不匹配")
        fingerprint = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        results = ticket["results"]
        if turn in results:
            original, receipt = results[turn]
            if original != fingerprint:
                raise ValueError("这轮已有回执，新候选请使用下一轮")
            return receipt
        if turn != len(results) + 1:
            raise ValueError("请按次序提交每一轮")
        try:
            result = judge(payload)
            message = result["message"]
            accepted = result.get("new_result", True)
        except ValueError as exc:
            message = str(exc)
            accepted = False
        receipt = {"id": key, "pack_id": ticket["pack_id"], "turn": turn,
                   "remaining": ticket["rounds"] - turn, "accepted": accepted, "message": message}
        results[turn] = fingerprint, receipt
        return receipt


def instructions(origin, pack_id, rounds, first_task):
    script = Path(__file__).with_name("ai_turn.py").resolve()
    cli = f'python "{script}" --url {origin}'
    return "\n".join([
        f"在玩家当前已登录的订阅 AI 工具中，做一次最多 {rounds} 轮的 {pack_id} 研究任务。",
        "只用当前订阅；遇额度耗尽、要求付费、用户停止或工具无法访问本机时停下。",
        "每轮由模型提出或调整构造，使用本机程序校验。保留真实失败，避免重复候选。",
        f"具备本机终端时先运行 {cli} start --pack {pack_id} --rounds {rounds}。保存返回的任务 id，只开这一份任务。",
        "按最新题面生成一个 UTF-8 JSON 文件，格式为题面给出的四个字段。",
        f'第 N 轮运行 {cli} play --id 任务id --turn N --author "玩家署名" --file "候选.json"。',
        "命令会返回本轮回执和剩余轮数。有下一轮时读取随附的新题面，调整候选；remaining 为 0 时停止。",
        "遇网络重试，沿用原轮号和原文件。请在总结中给出验证结果和每轮的实际改动。",
        "如果当前 AI 只能聊天，请返回一份候选 JSON 供玩家粘贴。该入口每次由玩家触发。",
        "", first_task,
    ])
