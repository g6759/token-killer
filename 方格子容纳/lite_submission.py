"""Prepare a public, replayable packing witness from a locally validated layout."""
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
import json
import re
from urllib.parse import urlencode
from zipfile import ZIP_DEFLATED, ZipFile

if __package__:
    from .geometry import SCALE, _bounds, _Square, get_square_pack
    from .reference_sync import CASE_URL
else:
    from geometry import SCALE, _bounds, _Square, get_square_pack
    from reference_sync import CASE_URL

REPORT_URL = "https://squaremath.org/report.html"
ISSUE_URL = "https://github.com/jlevy/squares/issues/new"
_MESSAGES = {
    "reference": "请同步公开榜单，取得最近 24 小时的题目状态、已验证上界和最佳报告值。",
    "closed": "公开题页标记本题已证明，可导出布局供复核。",
    "known": "当前布局尚未严格小于 Friedman 的已知精确上界，可导出布局供复核。",
    "precision": "当前布局尚未明确优于最新公开上界及最佳报告值，可导出布局供复核。",
    "lower": "布局与公开的已验证下界冲突，请先复核坐标与来源。",
    "candidate": "布局已通过本地精确复核，并严格优于所核对的公开上界。可准备 GitHub 登记，等待项目方复核。",
}


def _fresh(timestamp):
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)
    except (TypeError, ValueError, OverflowError):
        return False
    return timedelta(0) <= age <= timedelta(days=1)


def _decimal_interval(card):
    # Reference cards may display rounded decimals even when their MathML is exact.
    value = card.get("value_text")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,150})?", value):
        raise ValueError("无法核对公开边长")
    unit = Fraction(1, 10 ** len(value.partition(".")[2]))
    return Fraction(value) - unit, Fraction(value) + unit


def _verdict(sources, side):
    snapshot = sources.get("submission_reference")
    if sources.get("case") != 26 or not isinstance(snapshot, dict):
        return "reference"
    stamp = snapshot.get("checked_at")
    if not _fresh(stamp):
        return "reference"
    if snapshot.get("open") is False:
        return "closed"
    if snapshot.get("open") is not True:
        return "reference"
    upper = snapshot.get("upper_bound")
    reported = snapshot.get("reported_upper_bound")
    case_url = CASE_URL.format(case=26)
    for card in (upper, reported):
        if not (isinstance(card, dict) and card.get("checked_at") == stamp
                and card.get("source_url") == case_url and card.get("evidence_url")):
            return "reference"
    if upper.get("proven") is not True:
        return "reference"

    # Friedman's construction has side (7 + 3 sqrt(2))/2. All signs are exact.
    term = 2 * side - 7
    if term >= 0 and term * term >= 18:
        return "known"
    try:
        if any(side >= _decimal_interval(card)[0] for card in (upper, reported)):
            return "precision"
        lower = sources.get("lower_bound") or {}
        if lower.get("proven") is True and side < _decimal_interval(lower)[0]:
            return "lower"
    except ValueError:
        return "reference"
    return "candidate"


def _local_result(record):
    if record["state"]["pack_id"] != "square-26":
        raise ValueError("轻量版收录 26 块方格。")
    return get_square_pack("square-26").evaluate(record["solution"])


def _view(result, sources):
    verdict = _verdict(sources, Fraction(result["exact_side"]))
    eligible = verdict == "candidate"
    file = f"n026-{result['solution_hash'][:16]}.yaml"
    issue = None
    if eligible:
        issue = ISSUE_URL + "?" + urlencode({
            "template": "result-registration.md",
            "title": "Upper bound candidate for s(26) — registration request",
            "body": f"Candidate upper bound for s(26): {result['exact_side']}.\n\n"
                    f"[Paste the completed result-draft.md here and attach the ZIP containing {file}.]",
        })
    return {"eligible": eligible, "message": _MESSAGES[verdict], "report_url": REPORT_URL,
            "issue_url": issue, "witness_file": file, "exact_side": result["exact_side"]}


def submission_view(record, sources):
    return _view(_local_result(record), sources)


def _witness(result, filename):
    polygons = [_Square.from_pose(pose).vertices() for pose in result["solution"]["poses"]]
    left, bottom, _, _ = _bounds(polygons)
    squares = [
        {"id": index, "corners": [[str((x - left) / SCALE), str((y - bottom) / SCALE)]
                                   for x, y in polygon]}
        for index, polygon in enumerate(polygons, 1)
    ]
    replay = f"uv run --frozen packing-witness verify witnesses/{filename}"
    return {
        "softschema": {"contract": "packing.squares:Witness/v2", "schema": "witness.schema.yaml",
                       "envelope": "witness", "status": "enforced"},
        "witness": {
            "id": f"W-n026-{result['solution_hash'][:16]}", "n": 26, "side": result["exact_side"],
            "square_size": "1", "representation": "corners", "scalar": {"kind": "rational"},
            "coordinates": {"origin": "lower-left", "axes": "x-right-y-up", "angle_unit": "not-applicable"},
            "squares": squares,
            "claim": {"coordinate_provenance": "verified", "method": "exact-algebraic",
                      "limitations": "Local exact feasibility check. Global optimality and independent review are open."},
            "certificate": {"kind": "exact-rational-sat", "replay": replay},
        },
    }


def submission_zip(record, sources, checkpoint=None):
    """Export only the public geometry. Checkpoints may include private handoff notes."""
    result = _local_result(record)
    view = _view(result, sources)
    filename = view["witness_file"]
    witness = _witness(result, filename)
    # JSON flow syntax is YAML 1.2, and preserves every rational as a quoted string.
    raw = (json.dumps(witness, indent=2) + "\n").encode("utf-8")
    claim = ("Candidate improved upper bound for s(26), pending independent review."
             if view["eligible"] else "Local construction for review. No new public record claimed.")
    snapshot = sources.get("submission_reference") or {}
    when = snapshot.get("checked_at") or "unavailable"
    upper = (snapshot.get("upper_bound") or {}).get("value_text") or "unavailable"
    reported = (snapshot.get("reported_upper_bound") or {}).get("value_text") or "unavailable"
    case_url = CASE_URL.format(case=26)
    replay = witness["witness"]["certificate"]["replay"]
    draft = f"""# s(26) packing — result draft

**Credit name.** [Enter the name or handle you wish the project to credit.]

**Claim.** {claim} The attached construction packs 26 unit squares in a square of exact side `{result['exact_side']}`.

**Public comparison.** Last complete case snapshot: {when}. Verified upper bound as displayed: {upper}. Best reported packing as displayed: {reported}. See the [case record]({case_url}).

**Certificate.** Attach the ZIP containing `{filename}`. SHA-256 of that file: `{sha256(raw).hexdigest()}`. [Add a permanent source link and revision if available.]

**Checking.** SquarePack.evaluate constructed rational rotations, checked all 325 pairs for disjoint interiors using integer separating-axis tests, and measured the container with rational arithmetic. Coordinates in the witness are translated to a lower-left origin. To replay with the Squares Project checker, place the YAML beside `packing/witnesses/witness.schema.yaml`, enter the repository's `packing` directory, and run:

```sh
{replay}
```

**Independent checks.** The local score and this export share SquarePack geometry. Project checker replay, independent review, and a proof of optimality remain open.

**Lineage and credit.** The game's public starting construction credits Erich Friedman (1997). [Describe changes and any additional sources or collaborators.] [Public witness](https://github.com/jlevy/squares/blob/main/packing/witnesses/known-best/n-026.yaml)

**AI assistance.** [Describe the roles of the player, AI and any search software.]

[Project registration instructions]({REPORT_URL})
"""
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr(filename, raw)
        archive.writestr("result-draft.md", draft.encode("utf-8"))
    return output.getvalue()
