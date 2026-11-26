"""Read the published bounds and replay public coordinate witnesses locally."""

import base64
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
import hashlib
from html import unescape
from html.parser import HTMLParser
import json
from math import ceil, isqrt, radians, tan
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from threading import Lock
from time import monotonic
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree

if __package__:
    from .geometry import MAX_DENOMINATOR, SCALE, SquarePack
    from .packing_catalog import CASE_COUNTS
else:
    from geometry import MAX_DENOMINATOR, SCALE, SquarePack
    from packing_catalog import CASE_COUNTS


CASE_URL = "https://squaremath.org/cases/{case}.html"
GITHUB = "https://github.com/jlevy/squares/blob"
GITHUB_API = "https://api.github.com/repos/jlevy/squares/contents"
_LOCKS = {case: Lock() for case in CASE_COUNTS}
_FIELDS = ("lower_bound", "upper_bound", "reported_upper_bound", "witness")
_TIMEOUT = 5
_MAX_BYTES = 1024 * 1024
_NUMBER = r"[+-]?[0-9]{1,4}(?:\.[0-9]{1,150})?"
_CARD = re.compile(r"<div\b[^>]*\bclass=[\"']site-case-bound[\"'][^>]*>", re.I)
_MATH = re.compile(r"<math(?:\s[^>]*)?>.*?</math>", re.I | re.S)
_CASE_STATUS = re.compile(r"\bdata-case-status=[\"'](open|proved)[\"']", re.I)
_WITNESS_LINK = re.compile(r"/jlevy/squares/blob/(main|[0-9a-f]{40})/"
                           r"(packing/witnesses/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.ya?ml)")
_SQUARE = re.compile(rf"  - id: ([0-9]+)\n    center:\n    - '?({_NUMBER})'?\n"
                     rf"    - '?({_NUMBER})'?\n    angle: '?({_NUMBER})'?")
_GRID_CORNER = r"\n    - - '?([0-9]{1,2})'?\n      - '?([0-9]{1,2})'?"
_GRID_SQUARE = re.compile(r"  - id: ([0-9]+)\n    corners:" + _GRID_CORNER * 4)


class _FixedSourceRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise HTTPError(request.full_url, code, "reference source redirected", headers, response)


def _fetch_text(url):
    request = Request(url, headers={"User-Agent": "SquarePackingReference/1.0", "Cache-Control": "no-cache"})
    deadline = monotonic() + _TIMEOUT
    with build_opener(_FixedSourceRedirects()).open(request, timeout=_TIMEOUT) as response:
        body = bytearray()
        while chunk := response.read1(32768):
            body.extend(chunk)
            if len(body) > _MAX_BYTES:
                raise ValueError("来源文件超出大小限制")
            if monotonic() > deadline:
                raise TimeoutError("来源响应超时")
        return body.decode("utf-8")


class _Links(HTMLParser):
    def __init__(self, html, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.canonical = []
        self.links = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if href := attrs.get("href"):
            if tag == "link" and attrs.get("rel", "").lower() == "canonical":
                self.canonical.append(urljoin(self.base, href))
            elif tag == "a":
                self.links.append(urljoin(self.base, href))


def _class_text(html, selector):
    match = re.search(rf"<(\w+)\b[^>]*\bclass=[\"']{selector}[\"'][^>]*>([^<]*)</\1>", html, re.I)
    return unescape(match[2]).strip() if match else None


def _math_value(element):
    tag = element.tag.rsplit("}", 1)[-1]
    if tag == "mn" and re.fullmatch(_NUMBER, (element.text or "").strip()):
        return Decimal(element.text.strip())
    if tag == "mfrac" and len(element) == 2:
        return _math_value(element[0]) / _math_value(element[1])
    if tag == "msqrt" and len(element) == 1:
        return _math_value(element[0]).sqrt()
    if tag in {"math", "mrow", "mstyle"} and len(element):
        terms = [Decimal(1)]
        for item in element:
            if item.tag.rsplit("}", 1)[-1] == "mo":
                operator = (item.text or "").strip()
                if operator not in {"+", "−", "-", "×", "·", "\u2062"}:
                    raise ValueError("未识别的数学运算符")
                if operator == "+":
                    terms.append(Decimal(1))
                elif operator in {"−", "-"}:
                    terms.append(Decimal(-1))
            else:
                terms[-1] *= _math_value(item)
        return sum(terms)
    raise ValueError("未识别的数学表达式")


def _claim(value_text, source_url, checked_at, proven):
    return {"value": float(Decimal(value_text)), "value_text": value_text, "source_url": source_url,
            "checked_at": checked_at, "proven": proven}


def _checked_side(value_text, case):
    if not re.fullmatch(_NUMBER, value_text or ""):
        raise ValueError("未识别的边长数值")
    side = Decimal(value_text)
    grid_side = isqrt(case - 1) + 1
    if not 0 < side <= grid_side or Fraction(side) ** 2 < case:
        raise ValueError("边长超出该题已知可行范围")
    return value_text


def _read_bound(block, case):
    display = _class_text(block, "site-case-decimal")
    if display:
        display = _checked_side(display, case)
    mathml = _MATH.search(block)
    if mathml:
        with localcontext() as context:
            context.prec = 55
            try:
                exact = _math_value(ElementTree.fromstring(mathml[0]))
            except (ValueError, ArithmeticError, ElementTree.ParseError):
                exact = None
            if exact is not None:
                exact_text = _checked_side(format(exact, "f"), case)
                if display:
                    shown = Decimal(display)
                    precision = shown.as_tuple().exponent
                    if abs(exact - shown) > Decimal(1).scaleb(precision):
                        raise ValueError("数学表达式与页面小数不一致")
                    if precision <= -6:
                        exact_text = display
                # Keep the threshold conservative if a radical's evaluation is near a tick boundary.
                return exact_text, ceil(Fraction(exact - Decimal("1e-48")) * SCALE)
    value_text = display or _class_text(block, "site-case-value")
    if not value_text:
        return None, None
    value_text = _checked_side(value_text, case)
    value = Decimal(value_text)
    ticks = None
    if value.as_tuple().exponent <= -6:
        # A displayed decimal may have been rounded. Only use a threshold when
        # moving a full last-place unit in either direction leaves its tick unchanged.
        uncertainty = Fraction(10) ** value.as_tuple().exponent
        minimum = ceil((Fraction(value) - uncertainty) * SCALE)
        maximum = ceil((Fraction(value) + uncertainty) * SCALE)
        if minimum == maximum:
            ticks = minimum
    return value_text, ticks


def _case_cards(html, case, checked_at):
    url = CASE_URL.format(case=case)
    if _Links(html, url).canonical != [url]:
        raise ValueError("网页题号无法核对")
    names = {"Verified lower bound": "lower_bound", "Verified upper bound": "upper_bound",
             "Best known packing": "reported_upper_bound"}
    cards = {}
    linked = None
    for block in _CARD.split(html)[1:]:
        block = re.split(r"<h[1-6]\b", block, maxsplit=1, flags=re.I)[0]
        label = _class_text(block, "site-card-label")
        if label not in names:
            continue
        field = names[label]
        if field in cards:
            raise ValueError("网页存在重复的参考项目")
        links = _Links(block, url).links
        if field == "reported_upper_bound" or (field == "upper_bound" and linked is None):
            for link in links:
                target = urlsplit(link)
                match = _WITNESS_LINK.fullmatch(target.path)
                if target.scheme == "https" and target.netloc == "github.com" and match:
                    linked = match.groups()
                    break
        value_text, comparison_ticks = _read_bound(block, case)
        if value_text:
            item = _claim(value_text, url, checked_at, field != "reported_upper_bound")
            if field == "upper_bound":
                item["comparison_ticks"] = comparison_ticks
            evidence = next((link for link in links if urlsplit(link).scheme == "https"
                             and urlsplit(link).netloc == "github.com"
                             and urlsplit(link).path.startswith("/jlevy/squares/blob/")), None)
            cards[field] = {**item, "evidence_url": evidence}
    statuses = {match.lower() for match in _CASE_STATUS.findall(html)}
    verified_bounds = {"lower_bound", "upper_bound"} <= cards.keys()
    proved = statuses == {"proved"} and verified_bounds and _equal_bounds(cards)
    if verified_bounds and not proved:
        if Decimal(cards["lower_bound"]["value_text"]) > Decimal(cards["upper_bound"]["value_text"]):
            raise ValueError("网页上下界相互矛盾")
    if statuses == {"open"}:
        cards["open"] = True
    elif proved:
        cards["open"] = False
    return cards, linked


def _equal_bounds(reference):
    lower, upper = (reference[key] for key in ("lower_bound", "upper_bound"))
    if not all(bound.get("proven") is True and bound.get("evidence_url") for bound in (lower, upper)):
        return False
    # Equality can print differently between a rounded decimal and MathML.
    values = [Decimal(bound["value_text"]) for bound in (lower, upper)]
    return abs(values[1] - values[0]) <= Decimal("1e-12")


def _rounded_layout(rows, margin, rounding_ticks=1, max_denominator=MAX_DENOMINATOR):
    centers = [(Decimal(x), Decimal(y)) for _, x, y, _ in rows]
    min_x = min(x for x, _ in centers)
    min_y = min(y for _, y in centers)
    tangents = [Fraction(tan(radians(float((Decimal(angle) % 90 + 90) % 90)) / 2)).limit_denominator(
        max_denominator) for _, _, _, angle in rows]
    return {"poses": [[int(((x - min_x) * (SCALE + margin) / rounding_ticks).to_integral_value(
                            rounding=ROUND_HALF_UP)) * rounding_ticks,
                       int(((y - min_y) * (SCALE + margin) / rounding_ticks).to_integral_value(
                            rounding=ROUND_HALF_UP)) * rounding_ticks,
                       rotation.numerator, rotation.denominator]
                      for (x, y), rotation in zip(centers, tangents)]}


def _convert_witness(case, rows, reported_side):
    for margin in (0, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1_000):
        try:
            result = SquarePack(case).evaluate(_rounded_layout(rows, margin))
        except ValueError:
            continue
        if abs(Fraction(result["exact_side"]) - Fraction(reported_side)) <= Fraction(1, 100):
            return result
        break
    raise ValueError("坐标转换后未得到与公开边长相符的精确布局")


def _grid_layout(case, rows, side):
    poses = []
    for row in rows:
        numbers = [int(value) for value in row[1:]]
        corners = set(zip(numbers[::2], numbers[1::2]))
        x = min(x for x, _ in corners)
        y = min(y for _, y in corners)
        if corners != {(x, y), (x + 1, y), (x + 1, y + 1), (x, y + 1)} or max(x, y) + 1 > Decimal(side):
            raise ValueError("网格见证的方格边长或坐标范围有误")
        poses.append([x * SCALE + SCALE // 2, y * SCALE + SCALE // 2, 0, 1])
    return SquarePack(case).evaluate({"poses": poses})


def _read_witness(case, checked_at, linked):
    ref, path = linked or ("main", f"packing/witnesses/known-best/n-{case:03}.yaml")
    metadata = json.loads(_fetch_text(f"{GITHUB_API}/{path}?ref={ref}"))
    if not isinstance(metadata, dict) or (metadata.get("type"), metadata.get("encoding"), metadata.get("path")) != (
            "file", "base64", path) or not isinstance(metadata.get("content"), str):
        raise ValueError("GitHub 未返回可核对的见证文件")
    blob = base64.b64decode("".join(metadata["content"].split()), validate=True)
    sha = hashlib.sha1(f"blob {len(blob)}\0".encode() + blob).hexdigest()
    if sha != metadata.get("sha"):
        raise ValueError("见证文件的 Git 对象校验不一致")
    value_text, rows, grid = _witness_rows(case, blob.decode("utf-8"))
    result = _grid_layout(case, rows, value_text) if grid else _convert_witness(case, rows, value_text)
    item = _claim(value_text, f"{GITHUB}/{ref}/{path}", checked_at, False)
    return {**item, "solution": result["solution"], "side_ticks": result["side_ticks"],
            "exact_side": result["exact_side"], "blob_sha": sha,
            "commit_sha": ref if ref != "main" else None}


def _witness_rows(case, raw):
    raw = raw.replace("\r\n", "\n")
    sections = raw.split("\n  squares:\n")
    header = set(sections[0].splitlines())
    required = {"  contract: packing.squares:Witness/v2", "witness:", f"  n: {case}",
                "    origin: lower-left", "    axes: x-right-y-up"}
    center_angle = {"  representation: center-angle", "    kind: decimal", "    angle_unit: degrees"} <= header
    grid = {"  representation: corners", "    kind: rational", "    angle_unit: not-applicable"} <= header
    if len(sections) != 2 or not required <= header or center_angle == grid:
        raise ValueError("坐标见证格式或题号无法核对")
    unit = re.search(rf"^  square_size: '?({_NUMBER})'?$", sections[0], re.M)
    side = re.search(rf"^  side: '?({_NUMBER})'?$", sections[0], re.M)
    if not unit or Decimal(unit[1]) != 1 or not side:
        raise ValueError("见证须给出单位方格边长和容器边长")
    value_text = _checked_side(side[1], case)
    rows_text = re.split(r"\n  [A-Za-z_]+:", sections[1], maxsplit=1)[0].strip("\n")
    chunks = re.split(r"\n(?=  - id: )", rows_text)
    layout_pattern = _GRID_SQUARE if grid else _SQUARE
    matches = [layout_pattern.fullmatch(chunk) for chunk in chunks]
    if len(matches) != case or not all(matches) or len({int(row[1]) for row in matches}) != case:
        raise ValueError("见证方格列表不完整")
    return value_text, [match.groups() for match in matches], grid


def _bundled(case):
    bundled_path = Path(__file__).with_name("reference_layouts.json")
    bundled = json.loads(bundled_path.read_text(encoding="utf-8"))[f"square-{case}"]
    source = bundled["source"]
    bounds = bundled["bounds"]
    url = CASE_URL.format(case=case)
    value_text = source["reported_side"]
    upper_text = bounds.get("upper", value_text)
    lower_text = bounds["lower"]
    result = SquarePack(case).evaluate(bundled["finer"])
    witness = _claim(value_text, source["url"], None, False)
    return {"case": case, "status": "bundled", "open": True, "checked_at": None, "warnings": [],
            "witness_refreshed": False,
            "lower_bound": {**_claim(lower_text, url, None, True), "evidence_url": bounds.get("lower_evidence_url")},
            "upper_bound": {**_claim(upper_text, url, None, True), "evidence_url": bounds.get("upper_evidence_url"),
                            "comparison_ticks": ceil(Fraction(upper_text) * SCALE)}, "reported_upper_bound": None,
            "witness": {**witness, "blob_sha": source["blob_sha"], "commit_sha": None,
                        "solution": result["solution"], "side_ticks": result["side_ticks"],
                        "exact_side": result["exact_side"]}}


def _cache_path(case, data_dir):
    if type(case) is not int or case not in CASE_COUNTS:
        raise ValueError("找不到这一题的公开参考")
    return Path(data_dir) / f"public-reference-{case}.json"


def _verified_cache(cached, case):
    if cached["case"] != case or not isinstance(cached["warnings"], list):
        raise ValueError("缓存题号或状态有误")
    for field in _FIELDS:
        fact = cached[field]
        if fact is None and field == "reported_upper_bound":
            continue
        side = _checked_side(fact["value_text"], case)
        if fact["value"] != float(Decimal(side)) or not isinstance(fact["source_url"], str):
            raise ValueError("缓存来源或数值有误")
    witness = cached["witness"]
    checked = SquarePack(case).evaluate(witness["solution"])
    if (checked["side_ticks"], checked["exact_side"]) != (witness["side_ticks"], witness["exact_side"]):
        raise ValueError("缓存见证与几何复核不符")
    open_case = cached.get("open")
    if open_case is False and not _equal_bounds(cached):
        raise ValueError("已证明状态与缓存中的上下界不符")
    return {**cached, "open": open_case is not False, "status": "cached", "witness_refreshed": False}


def load_reference(case: int, data_dir: Path) -> dict:
    """Read this case's most recent saved reference, or the bundled snapshot."""
    path = _cache_path(case, data_dir)
    try:
        return _verified_cache(json.loads(path.read_text(encoding="utf-8")), case)
    except (OSError, ValueError, KeyError, TypeError):
        return _bundled(case)


def case_is_open(case: int, data_dir: Path) -> bool:
    """Only a valid proof recorded by the case page can close a case."""
    path = _cache_path(case, data_dir)
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached.get("open") is False:
            return _verified_cache(cached, case)["open"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return True


def _write_cache(path, reference):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temp_path = Path(output.name)
            json.dump(reference, output, ensure_ascii=False, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def refresh_reference(case: int, data_dir: Path) -> dict:
    """Refresh the selected case and preserve each source's prior data on failure."""
    cache_path = _cache_path(case, data_dir)
    with _LOCKS[case]:
        previous = load_reference(case, data_dir)
        attempt_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        updates, warnings, linked = {}, [], None
        try:
            updates, linked = _case_cards(_fetch_text(CASE_URL.format(case=case)), case, attempt_at)
            if not {"lower_bound", "upper_bound"} <= updates.keys():
                warnings.append("Squares Project 的部分已验证上下界无法识别，保留原数据。")
        except (OSError, ValueError, ArithmeticError, ElementTree.ParseError):
            warnings.append("Squares Project 上下界暂时无法核对，保留原数据。")
        try:
            updates["witness"] = _read_witness(case, attempt_at, linked)
        except (OSError, ValueError, ArithmeticError):
            warnings.append("GitHub 坐标见证暂时无法获取或未通过几何验证，保留原见证。")

        if case == 26:
            # Submission requires the verified and reported cards from the same official read.
            keys = {"open", "lower_bound", "upper_bound", "reported_upper_bound"}
            ready = keys <= updates.keys() and all(updates[k].get("evidence_url")
                                                    for k in ("upper_bound", "reported_upper_bound"))
            updates["submission_reference"] = (
                {"checked_at": attempt_at, **{k: updates[k] for k in keys}} if ready else None)

        if not updates:
            return {**previous, "checked_at": attempt_at, "warnings": warnings}
        result = {**previous, **updates, "status": "partial" if warnings else "online",
                  "checked_at": attempt_at, "warnings": warnings, "witness_refreshed": "witness" in updates}
        lower, upper, reported = (result[key] for key in _FIELDS[:3])
        if (result["open"] is False and not _equal_bounds(result)
                or result["open"] is True and Decimal(lower["value_text"]) > Decimal(upper["value_text"])):
            result.update(lower_bound=previous["lower_bound"], upper_bound=previous["upper_bound"], status="partial")
            if case == 26:
                result["submission_reference"] = None
            warnings.append("新旧来源的上下界相互矛盾，保留上一组上下界。")
        if reported:
            reported_side = Decimal(reported["value_text"])
            if Decimal(result["upper_bound"]["value_text"]) - reported_side <= Decimal(1).scaleb(
                    reported_side.as_tuple().exponent):
                result["reported_upper_bound"] = None
        try:
            _write_cache(cache_path, result)
        except OSError:
            result["status"] = "partial"
            warnings.append("本次参考数据已读取，缓存写入失败。")
        return result
