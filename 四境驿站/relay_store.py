"""Local checkpoint storage and independent replay of research relay bundles."""

from contextlib import closing, contextmanager
import hashlib
import json
import os
import re
import sqlite3
import unicodedata

from packs import get_pack


FORMAT = "research-relay/v1"
MAX_CHAIN_LENGTH = 2048
MAX_AUTHOR_BYTES = 128
MAX_HANDOFF_BYTES = 4096
_CHECKPOINT_KEYS = {"hash", "state", "solution", "handoff"}
_STATE_KEYS = {
    "pack_id", "version", "parent_hash", "score", "total", "author",
    "solution_hash", "handoff_hash",
}
_HASH = re.compile(r"[0-9a-f]{64}")


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _digest(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _check_hash(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("存档哈希必须是 64 位小写十六进制字符串")


def _check_keys(value, expected, label):
    if not isinstance(value, dict) or value.keys() != expected:
        raise ValueError(f"{label}字段不完整或包含未识别的字段")


def _check_text(value, label, limit, allowed_controls=""):
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文本")
    if len(value) > limit:
        raise ValueError(f"{label}最多允许 {limit} 个 UTF-8 字节")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label}包含无效的 Unicode 字符") from exc
    if size > limit:
        raise ValueError(f"{label}最多允许 {limit} 个 UTF-8 字节")
    if any(unicodedata.category(char) == "Cc" and char not in allowed_controls for char in value):
        raise ValueError(f"{label}包含不允许的控制字符")


def _check_author(author):
    _check_text(author, "署名", MAX_AUTHOR_BYTES)
    author = author.strip()
    if not author:
        raise ValueError("署名不能为空")
    return author


def _check_handoff(handoff):
    _check_text(handoff, "交接说明", MAX_HANDOFF_BYTES, "\t\r\n")


def _pack(pack_id, resolve_pack=get_pack):
    if not isinstance(pack_id, str):
        raise ValueError("题包编号必须是文本")
    if len(pack_id) > 64:
        raise ValueError("题包编号过长")
    return resolve_pack(pack_id)


def _checkpoint(pack_id, version, parent_hash, author, result, handoff):
    solution = result["solution"]
    state = {
        "pack_id": pack_id,
        "version": version,
        "parent_hash": parent_hash,
        "score": result["score"],
        "total": result["total"],
        "author": author,
        "solution_hash": _digest(solution),
        "handoff_hash": _digest(handoff),
    }
    return {"hash": _digest(state), "state": state, "solution": solution, "handoff": handoff}


def _seed_checkpoint(pack_id, pack):
    return _checkpoint(pack_id, pack.version, None, "seed", pack.evaluate(pack.seed()), "")


def _require_improvement(parent, score):
    previous_score = parent["state"]["score"]
    if score <= previous_score:
        raise ValueError(f"候选必须提高父档得分（父档 {previous_score}，候选 {score}）")


def _find(db, checkpoint_hash):
    row = db.execute("SELECT payload FROM checkpoints WHERE hash = ?", (checkpoint_hash,)).fetchone()
    return json.loads(row[0]) if row is not None else None


def _fetch(db, checkpoint_hash):
    checkpoint = _find(db, checkpoint_hash)
    if checkpoint is None:
        raise ValueError(f"找不到存档 {checkpoint_hash}")
    return checkpoint


def _insert(db, checkpoint):
    state = checkpoint["state"]
    payload = _json(checkpoint)
    cursor = db.execute(
        """
        INSERT INTO checkpoints (hash, pack_id, version, parent_hash, score, payload)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(hash) DO NOTHING
        """,
        (checkpoint["hash"], state["pack_id"], state["version"], state["parent_hash"],
         state["score"], payload),
    )
    if cursor.rowcount == 0 and _json(_fetch(db, checkpoint["hash"])) != payload:
        raise ValueError(f"存档 {checkpoint['hash']} 与本地同哈希的记录冲突")


def _existing_solution(db, checkpoint):
    existing = _find(db, checkpoint["hash"])
    if existing is not None:
        if existing != checkpoint:
            raise ValueError(f"存档 {checkpoint['hash']} 与本地同哈希的记录冲突")
        return existing

    state = checkpoint["state"]
    # An identical solution always has the same score under the same judge version.
    rows = db.execute(
        """
        SELECT payload FROM checkpoints WHERE pack_id = ? AND version = ? AND score = ?
        ORDER BY hash ASC
        """,
        (state["pack_id"], state["version"], state["score"]),
    )
    for (payload,) in rows:
        record = json.loads(payload)
        if record["state"]["solution_hash"] == state["solution_hash"]:
            return record
    return None


def _ancestry(checkpoint, load_parent):
    chain = [checkpoint]
    while checkpoint["state"]["parent_hash"] is not None:
        if len(chain) >= MAX_CHAIN_LENGTH:
            raise ValueError("存档谱系超过允许长度")
        checkpoint = load_parent(checkpoint["state"]["parent_hash"])
        chain.append(checkpoint)
    return list(reversed(chain))


def _record(chain):
    lineage = [
        {"hash": node["hash"], "author": node["state"]["author"], "score": node["state"]["score"]}
        for node in chain
    ]
    return {**chain[-1], "lineage": lineage}


def credited_hashes(history):
    """Return one deterministic representative per solution, keeping lineage intact."""
    representatives = {}
    for record in history:
        state = record["state"]
        key = (state["pack_id"], state["version"], state["solution_hash"])
        checkpoint_hash = record["hash"]
        representatives[key] = min(checkpoint_hash, representatives.get(key, checkpoint_hash))
    return set(representatives.values())


def _verify_chain(bundle, resolve_pack=get_pack):
    _check_keys(bundle, {"format", "tip", "chain"}, "交接包")
    if bundle["format"] != FORMAT:
        raise ValueError("不支持该交接包格式")
    _check_hash(bundle["tip"])
    candidates = bundle["chain"]
    if not isinstance(candidates, list) or not (1 <= len(candidates) <= MAX_CHAIN_LENGTH):
        raise ValueError(f"交接包需包含 1 至 {MAX_CHAIN_LENGTH} 个存档")

    verified = []
    for index, candidate in enumerate(candidates, start=1):
        try:
            _check_keys(candidate, _CHECKPOINT_KEYS, "存档")
            _check_hash(candidate["hash"])
            state = candidate["state"]
            _check_keys(state, _STATE_KEYS, "存档状态")
            if not verified:
                pack_id = state["pack_id"]
                pack = _pack(pack_id, resolve_pack)
            if state["pack_id"] != pack_id or state["version"] != pack.version:
                raise ValueError("题包或版本与本地裁判不匹配")
            author = _check_author(state["author"])
            handoff = candidate["handoff"]
            _check_handoff(handoff)
            if verified:
                parent = verified[-1]
                if state["parent_hash"] != parent["hash"]:
                    raise ValueError("父档与交接包中的上一条记录不匹配")
                result = pack.evaluate(candidate["solution"])
                _require_improvement(parent, result["score"])
                expected = _checkpoint(pack_id, pack.version, parent["hash"], author, result, handoff)
            else:
                expected = _seed_checkpoint(pack_id, pack)
                pack.evaluate(candidate["solution"])
            if type(state["score"]) is not int or type(state["total"]) is not int or candidate != expected:
                raise ValueError("存档的起点、得分、内容或哈希与本地复核结果不符")
            verified.append(expected)
        except ValueError as exc:
            raise ValueError(f"交接包第 {index} 条存档无效：{exc}") from exc

    if bundle["tip"] != verified[-1]["hash"]:
        raise ValueError("交接包的末档哈希与实际链尾不符")
    return verified


class Store:
    """Transactional access to an SQLite file. Each operation opens its own connection."""

    def __init__(self, db_path, resolve_pack=get_pack):
        self.db_path = os.fspath(db_path)
        self.resolve_pack = resolve_pack
        if not self.db_path or self.db_path in (":memory:", b":memory:"):
            raise ValueError("请指定用于持久化接力的 SQLite 文件路径")
        with self._transaction(write=True) as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS checkpoints (
                    hash TEXT PRIMARY KEY NOT NULL,
                    pack_id TEXT NOT NULL,
                    version TEXT NOT NULL,
                    parent_hash TEXT REFERENCES checkpoints(hash),
                    score INTEGER NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE INDEX IF NOT EXISTS checkpoint_ranking
                ON checkpoints(pack_id, version, score DESC, hash ASC)
                """
            )

    @contextmanager
    def _transaction(self, write=False):
        try:
            with closing(sqlite3.connect(self.db_path)) as db, db:
                db.execute("PRAGMA foreign_keys = ON")
                db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield db
        except sqlite3.Error as exc:
            raise ValueError(f"无法读写接力存档：{exc}") from exc

    def seed(self, pack_id):
        checkpoint = _seed_checkpoint(pack_id, _pack(pack_id, self.resolve_pack))
        with self._transaction(write=True) as db:
            _insert(db, checkpoint)
        return _record([checkpoint])

    def best_hash(self, pack_id):
        pack = _pack(pack_id, self.resolve_pack)
        with self._transaction() as db:
            row = db.execute(
                "SELECT hash FROM checkpoints WHERE pack_id=? AND version=? "
                "ORDER BY score DESC, hash ASC LIMIT 1", (pack_id, pack.version)
            ).fetchone()
        return row[0] if row else None

    def best(self, pack_id):
        pack = _pack(pack_id, self.resolve_pack)
        with self._transaction(write=True) as db:
            row = db.execute(
                """
                SELECT payload FROM checkpoints WHERE pack_id = ? AND version = ?
                ORDER BY score DESC, hash ASC LIMIT 1
                """,
                (pack_id, pack.version),
            ).fetchone()
            if row is None:
                checkpoint = _seed_checkpoint(pack_id, pack)
                _insert(db, checkpoint)
            else:
                checkpoint = json.loads(row[0])
            return _record(_ancestry(checkpoint, lambda parent_hash: _fetch(db, parent_hash)))

    def get(self, checkpoint_hash):
        _check_hash(checkpoint_hash)
        with self._transaction() as db:
            checkpoint = _fetch(db, checkpoint_hash)
            return _record(_ancestry(checkpoint, lambda parent_hash: _fetch(db, parent_hash)))

    def submit(self, pack_id, parent_hash, author, solution, handoff):
        pack = _pack(pack_id, self.resolve_pack)
        _check_hash(parent_hash)
        author = _check_author(author)
        _check_handoff(handoff)
        result = pack.evaluate(solution)
        checkpoint = _checkpoint(pack_id, pack.version, parent_hash, author, result, handoff)
        with self._transaction(write=True) as db:
            parent = _fetch(db, parent_hash)
            if parent["state"]["pack_id"] != pack_id or parent["state"]["version"] != pack.version:
                raise ValueError("父档的题包或版本与候选不匹配")
            _require_improvement(parent, result["score"])
            existing = _existing_solution(db, checkpoint)
            if existing is None:
                _insert(db, checkpoint)
            else:
                checkpoint = existing
            return _record(_ancestry(checkpoint, lambda parent_hash: _fetch(db, parent_hash)))

    def export(self, checkpoint_hash):
        _check_hash(checkpoint_hash)
        with self._transaction() as db:
            checkpoint = _fetch(db, checkpoint_hash)
            chain = _ancestry(checkpoint, lambda parent_hash: _fetch(db, parent_hash))
        return {"format": FORMAT, "tip": checkpoint_hash, "chain": chain}

    def ingest(self, bundle, *, improvement_only=False):
        chain = _verify_chain(bundle, self.resolve_pack)
        with self._transaction(write=True) as db:
            if improvement_only:
                tip = chain[-1]["state"]
                best = db.execute("SELECT MAX(score) FROM checkpoints WHERE pack_id=? AND version=?",
                                  (tip["pack_id"], tip["version"])).fetchone()[0]
                if best is not None and tip["score"] <= best:
                    return None
            for checkpoint in chain:
                _insert(db, checkpoint)
        return _record(chain)

    def history(self, pack_id):
        pack = _pack(pack_id, self.resolve_pack)
        with self._transaction() as db:
            rows = db.execute(
                """
                SELECT hash, payload FROM checkpoints WHERE pack_id = ? AND version = ?
                ORDER BY score DESC, hash ASC
                """,
                (pack_id, pack.version),
            )
            checkpoints = {key: json.loads(payload) for key, payload in rows}
        return [_record(_ancestry(node, checkpoints.__getitem__)) for node in checkpoints.values()]
