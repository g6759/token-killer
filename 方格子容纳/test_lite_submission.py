from copy import deepcopy
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
from io import BytesIO
from itertools import combinations
import json
from urllib.parse import parse_qs, urlsplit
import unittest
from zipfile import ZipFile

from geometry import get_square_pack
from lite_submission import _verdict, _view, submission_view, submission_zip
from reference_sync import CASE_URL


def current_source(age=timedelta()):
    stamp = (datetime.now(timezone.utc) - age).isoformat()
    base = {"source_url": CASE_URL.format(case=26), "checked_at": stamp,
            "evidence_url": "https://github.com/jlevy/squares/blob/main/packing/frontier/evidence.yaml"}
    snapshot = {"checked_at": stamp, "open": True,
                "upper_bound": {**base, "value_text": "5.62132034355964257320253308631455", "proven": True},
                "reported_upper_bound": {**base, "value_text": "5.62132034355964", "proven": False}}
    return {"case": 26, "submission_reference": snapshot, "lower_bound": {**base, "value_text": "5.555", "proven": True}}


def separated(first, second):
    # Independent corner projection, including contact at either interval endpoint.
    for polygon in (first, second):
        for a, b in zip(polygon[:2], polygon[1:3]):
            dx, dy = b[0] - a[0], b[1] - a[1]
            first_proj = [dy * x - dx * y for x, y in first]
            second_proj = [dy * x - dx * y for x, y in second]
            if max(first_proj) <= min(second_proj) or max(second_proj) <= min(first_proj):
                return True
    return False


class LiteSubmissionTest(unittest.TestCase):
    def setUp(self):
        self.pack = get_square_pack("square-26")
        self.record = {"state": {"pack_id": "square-26", "author": "PRIVATE_ALIAS"},
                       "handoff": "PRIVATE_NOTE", "solution": self.pack.seed()}

    def test_export_replays_every_corner_and_pair_in_exact_arithmetic(self):
        source = current_source()
        view = submission_view(self.record, source)
        self.assertFalse(view["eligible"])
        self.assertIsNone(view["issue_url"])
        output = submission_zip(self.record, source, {"handoff": "PRIVATE_CHAIN"})
        with ZipFile(BytesIO(output)) as archive:
            self.assertEqual(set(archive.namelist()), {view["witness_file"], "result-draft.md"})
            raw = archive.read(view["witness_file"])
            draft = archive.read("result-draft.md").decode()
        self.assertIn(sha256(raw).hexdigest(), draft)
        self.assertIn("No new public record claimed", draft)
        self.assertNotIn("PRIVATE", raw.decode() + draft)
        document = json.loads(raw)
        self.assertEqual(document["softschema"]["contract"], "packing.squares:Witness/v2")
        witness = document["witness"]
        self.assertEqual(witness["coordinates"], {"origin": "lower-left", "axes": "x-right-y-up", "angle_unit": "not-applicable"})
        self.assertEqual((witness["representation"], witness["scalar"]["kind"]), ("corners", "rational"))
        self.assertEqual(witness["square_size"], "1")
        self.assertIn(view["witness_file"], witness["certificate"]["replay"])
        self.assertEqual([square["id"] for square in witness["squares"]], list(range(1, 27)))
        self.assertEqual(witness["side"], self.pack.evaluate(self.record["solution"])["exact_side"])
        polygons = []
        for square in witness["squares"]:
            corners = square["corners"]
            self.assertTrue(all(isinstance(v, str) for point in corners for v in point))
            points = [tuple(map(Fraction, point)) for point in corners]
            polygons.append(points)
            edges = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:] + points[:1])]
            self.assertTrue(all(x*x + y*y == 1 for x, y in edges))
            self.assertEqual(edges[0][0]*edges[1][1] - edges[0][1]*edges[1][0], 1)
        xs, ys = zip(*(point for polygon in polygons for point in polygon))
        self.assertEqual((min(xs), min(ys)), (0, 0))
        self.assertEqual(max(max(xs), max(ys)), Fraction(witness["side"]))
        self.assertTrue(all(separated(a, b) for a, b in combinations(polygons, 2)))
        moved = deepcopy(self.record)
        for pose in moved["solution"]["poses"]:
            pose[0] += 100
            pose[1] += 701
        with ZipFile(BytesIO(submission_zip(moved, source))) as archive:
            self.assertEqual(archive.read(view["witness_file"]), raw)

    def test_unverified_input_cannot_produce_a_witness(self):
        self.record["solution"]["poses"][1] = self.record["solution"]["poses"][0]
        for fn in (submission_view, submission_zip):
            with self.assertRaises(ValueError):
                fn(self.record, current_source())

    def test_reference_and_known_bound_are_independent_gates(self):
        hypothetical = Fraction("5.6")  # comparison unit test only, no geometry claim
        for age in (timedelta(days=2), timedelta(minutes=-5)):
            self.assertEqual(_verdict(current_source(age), hypothetical), "reference")
        for key, value in (("open", False), ("open", None), ("reported_upper_bound", None)):
            source = current_source()
            source["submission_reference"][key] = value
            self.assertNotEqual(_verdict(source, hypothetical), "candidate")
        source = current_source()
        source["submission_reference"]["upper_bound"]["proven"] = False
        self.assertEqual(_verdict(source, hypothetical), "reference")
        source = current_source()
        source["submission_reference"]["upper_bound"]["checked_at"] = "unverified"
        self.assertEqual(_verdict(source, hypothetical), "reference")
        self.assertEqual(_verdict({"case": 26, "open": True, "status": "cached"}, hypothetical), "reference")
        source = current_source()
        for key in ("upper_bound", "reported_upper_bound"):
            source["submission_reference"][key]["value_text"] = "5.6220000"
        self.assertEqual(_verdict(source, Fraction("5.62132034355964257320253308631455")), "known")
        self.assertEqual(_verdict(source, Fraction("5.50")), "lower")

    def test_reported_value_last_digit_and_ties_do_not_create_records(self):
        source = current_source()
        source["submission_reference"]["reported_upper_bound"]["value_text"] = "5.600001"
        for value in ("5.61", "5.600001", "5.6000005", "5.600000"):
            self.assertEqual(_verdict(source, Fraction(value)), "precision")
        result = {"solution_hash": "a" * 64, "exact_side": "5599/1000"}
        view = _view(result, source)
        self.assertTrue(view["eligible"])
        url = urlsplit(view["issue_url"])
        self.assertEqual((url.scheme, url.netloc, url.path), ("https", "github.com", "/jlevy/squares/issues/new"))
        self.assertEqual(parse_qs(url.query)["template"], ["result-registration.md"])


if __name__ == "__main__":
    unittest.main()
