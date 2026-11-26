"""Explore packing deformations. Geometry estimates guide search; the native judge scores it."""
from fractions import Fraction
from hashlib import sha256
import json
from math import hypot
from random import Random

from 本地裁判 import get_square_pack


PACK = "square-26"
MAX_BRANCHES = 1728


def schema(count):
    fields = {
        "pack_id": {"type": "string", "enum": [PACK]},
        "parent_hash": {"type": "string"},
        "branch": {"type": "integer", "minimum": 0, "maximum": count - 1},
        "square": {"type": "integer", "minimum": 0, "maximum": 25},
        "dx": {"type": "integer", "minimum": -50000, "maximum": 50000},
        "dy": {"type": "integer", "minimum": -50000, "maximum": 50000},
        "handoff": {"type": "string", "description": "Give a verification note in 1 to 300 characters."},
    }
    return {"type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}


def _seed(pack, parent_hash, lane, turn, count):
    return sha256(json.dumps([pack, parent_hash, lane, turn, count]).encode()).hexdigest()


def _tilts(poses):
    angles = {}
    for p, q in {(row[2], row[3]) for row in poses}:
        for tilt in (-2, -1, 1, 2):
            angle = Fraction(max(0, min(1000*q-1, 1000*p+tilt*q)), 1000*q).limit_denominator(1000)
            angles[p,q,tilt] = angle.as_integer_ratio() if angle < 1 else (p,q)
    return angles


def _shape(poses, seed, index, angles):
    rng = Random(f"{seed}/{index}")
    cut, shift_axis = rng.randrange(2), rng.randrange(2)
    pivot = rng.choice(poses)[cut]
    offset = rng.randint(-40000, 40000)
    strain_x, strain_y = rng.randint(-8000, 4000), rng.randint(-8000, 4000)
    tilt = rng.choice((-2, -1, 1, 2))
    # Move one band and rotate its squares slightly, allowing contact changes at its boundary.
    result = []
    for pose in poses:
        x, y, p, q = pose
        x += x * strain_x // 1000000
        y += y * strain_y // 1000000
        if pose[cut] >= pivot:
            if shift_axis == 0:
                x += offset
            else:
                y += offset
            p, q = angles[p,q,tilt]
        result.append([x, y, p, q])
    return _normalize(result)


def _normalize(poses):
    left = min(row[0] for row in poses)
    bottom = min(row[1] for row in poses)
    return [[x - left, y - bottom, p, q] for x, y, p, q in poses]


def _estimate(poses, pairs):
    # Half-extents and the signed separating-axis gap, in integer micro-units.
    axes = [((q*q-p*p)/(q*q+p*p), 2*p*q/(q*q+p*p)) for _, _, p, q in poses]
    radii = [500000*(c+s) for c,s in axes]
    span = max(max(row[k]+r for row,r in zip(poses,radii))-min(row[k]-r for row,r in zip(poses,radii))
               for k in (0,1))
    gaps = []
    for i,j in pairs:
        c,s = axes[i]
        d,t = axes[j]
        dx,dy = poses[i][0]-poses[j][0], poses[i][1]-poses[j][1]
        reach = 500000*(1+abs(c*d+s*t)+abs(c*t-s*d))
        gap = max(abs(dx*c+dy*s),abs(dy*c-dx*s),abs(dx*d+dy*t),abs(dy*d-dx*t))-reach
        gaps.append((round(gap),i,j))
    return round(span), [[i,j,g] for g,i,j in sorted(gaps)[:5]]


def build(pack, parent, lane, turn, count):
    if pack != PACK or type(count) is not int or not 1 <= count <= MAX_BRANCHES:
        raise ValueError("Invalid square frontier pack or branch count")
    poses = get_square_pack(pack).evaluate(parent["solution"])["solution"]["poses"]
    seed = _seed(pack, parent["hash"], lane, turn, count)
    pairs = set()
    for i,(x,y,_,_) in enumerate(poses):
        nearby = sorted((hypot(x-a,y-b),j) for j,(a,b,_,_) in enumerate(poses) if j != i)
        pairs.update(tuple(sorted((i,j))) for _,j in nearby[:4])
    branches = []
    angles = _tilts(poses)
    for index in range(count):
        candidate = _shape(poses, seed, index, angles)
        span, contacts = _estimate(candidate, pairs)
        branches.append([index, candidate, span, contacts])
    prompt = json.dumps({"pack_id":pack, "parent_hash":parent["hash"], "parent":poses,
        "task":"Choose a deformation and improve one square by a translation dx,dy in micro-units. Minimize the final bounding square side without overlap. Each branch contains [index,poses,approximate side,five tight nearby pairs [i,j,signed gap]]. A negative gap suggests overlap. Square indices are zero-based. Each pose is [cx,cy,p,q] with tan(angle/2)=p/q; one unit is 1000000. Exact geometry is checked locally; small remaining overlaps may require a uniform expansion. Return a verifiable reason in handoff.",
        "branches":branches}, separators=(",",":"))
    if len(prompt.encode()) > 1600*1024:
        raise ValueError("Square frontier exceeds the game prompt size limit")
    return prompt, {"seed":seed, "count":count, "parent_hash":parent["hash"]}


def resolve(pack, parent, context, response):
    move = json.loads(response)
    if (pack != PACK or not isinstance(move, dict) or move.keys() != schema(context["count"])["properties"].keys()
            or move["pack_id"] != pack or move["parent_hash"] != parent["hash"]
            or context["parent_hash"] != parent["hash"]):
        raise ValueError("Square reply does not match the current pack and parent")
    index, square, dx, dy = (move[k] for k in ("branch","square","dx","dy"))
    if (any(type(x) is not int for x in (index,square,dx,dy)) or not 0 <= index < context["count"]
            or not 0 <= square < 26 or max(abs(dx),abs(dy)) > 50000
            or not isinstance(move["handoff"],str) or not 1 <= len(move["handoff"].strip()) <= 300):
        raise ValueError("Invalid square frontier move")
    rules = get_square_pack(pack)
    poses = rules.evaluate(parent["solution"])["solution"]["poses"]
    branch = _shape(poses, context["seed"], index, _tilts(poses))
    branch[square][0] += dx
    branch[square][1] += dy
    branch = _normalize(branch)
    checked = 0

    def try_scale(scale):
        nonlocal checked
        checked += 1
        solution = {"poses":[[x*scale//1000000,y*scale//1000000,p,q] for x,y,p,q in branch]}
        try:
            return rules.evaluate(solution)
        except ValueError:
            return None

    best = try_scale(1000000)
    if best is None:
        low,high = 1000000,1250000
        best = try_scale(high)
        if best is None:
            raise ValueError("Selected square layout remains invalid after expansion")
        while high-low > 1:
            middle = (low+high)//2
            result = try_scale(middle)
            if result is None:
                low = middle
            else:
                high,best = middle,result
    note = move["handoff"]
    candidate = {"pack_id":pack, "parent_hash":parent["hash"], "solution":best["solution"], "handoff":note}
    return {"strategy":"square_frontier", "checked":checked, "candidate":candidate, "hypothesis":note,
            "outcome":f"branch={index}, side={best['exact_side']}, exact checks={checked}"}
