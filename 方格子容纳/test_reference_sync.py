import base64
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from geometry import SquarePack, get_square_pack
import reference_sync


SOURCE = "https://squaremath.org/cases/18.html"
WITNESS_PATH = "packing/witnesses/known-best/n-018.yaml"
WITNESS_API = f"https://api.github.com/repos/jlevy/squares/contents/{WITNESS_PATH}?ref=main"
LINKED_PATH = "packing/witnesses/corrections/n-018-tighter.yaml"
LINKED_API = f"https://api.github.com/repos/jlevy/squares/contents/{LINKED_PATH}?ref=main"
LOWER_EVIDENCE = "https://github.com/jlevy/squares/blob/main/packing/frontier/evidence.yaml#L52354"


def case_page(*, case=18, upper="4.99999999999999999999999999999", linked="", upper_math="", upper_linked=""):
    return f'''<link rel="canonical" href="https://squaremath.org/cases/{case}.html">
    <h3 class="site-case-heading">Bounds</h3><div class="site-case-bounds">
      <div class="site-case-bound"><span class="site-card-label">Best known packing</span>
        <span class="site-case-decimal">5.00</span>{linked}</div>
      <div class="site-case-bound"><span class="site-card-label">Verified upper bound</span>
        {upper_math}<span class="site-case-decimal">{upper}</span>{upper_linked}</div>
      <div class="site-case-bound"><span class="site-card-label">Reported lower bound</span>
        <span class="site-case-decimal">4.79</span></div>
      <div class="site-case-bound"><span class="site-card-label">Verified lower bound</span>
        <p class="site-case-value"><span><math xmlns="http://www.w3.org/1998/Math/MathML">
          <mrow><mstyle><mfrac><mrow><mn>941</mn></mrow><mrow><mn>200</mn></mrow></mfrac></mstyle></mrow>
        </math></span></p><a href="{LOWER_EVIDENCE}">Evidence</a></div>
    </div><h3>Results in the register</h3><p>Historical side 4.59</p>'''


def github_response(path=WITNESS_PATH, *, case=18, overlap=False):
    rows = []
    for index in range(case):
        x, y = (0, 0) if overlap else divmod(index, 4)
        rows.append(f"  - id: {index + 1}\n    center:\n    - '{x}.5'\n    - '{y}.5'\n    angle: '0'")
    serialized = ("softschema:\n  contract: packing.squares:Witness/v2\nwitness:\n"
                  f"  id: W-known-best-n{case:03}\n  n: {case}\n  side: '5'\n"
                  "  square_size: '1'\n  representation: center-angle\n  scalar:\n    kind: decimal\n"
                  "  coordinates:\n    origin: lower-left\n    axes: x-right-y-up\n    angle_unit: degrees\n"
                  "  squares:\n" + "\n".join(rows) + "\n  claim:\n    coordinate_provenance: numerically-checked\n").encode()
    prefix = f"blob {len(serialized)}\0".encode()
    return json.dumps({"type": "file", "encoding": "base64", "path": path,
                       "sha": hashlib.sha1(prefix + serialized).hexdigest(),
                       "content": base64.b64encode(serialized).decode()})


class ReferenceSyncTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def refresh(self, pages):
        def fetch(url):
            if url not in pages:
                raise TimeoutError("source did not respond")
            return pages[url]

        with patch.object(reference_sync, "_fetch_text", side_effect=fetch):
            return reference_sync.refresh_reference(18, self.directory)

    def test_verified_cards_override_reported_and_historical_values(self):
        current = self.refresh({SOURCE: case_page(), WITNESS_API: github_response()})
        self.assertEqual(current["status"], "online")
        self.assertEqual(current["lower_bound"]["value_text"], "4.705")
        self.assertEqual(current["lower_bound"]["source_url"], SOURCE)
        self.assertEqual(current["lower_bound"]["evidence_url"], LOWER_EVIDENCE)
        self.assertEqual(current["upper_bound"]["value_text"], "4.99999999999999999999999999999")
        self.assertTrue(current["upper_bound"]["proven"])
        self.assertTrue(current["lower_bound"]["proven"])
        self.assertIsNotNone(current["upper_bound"]["checked_at"])

    def test_explicit_link_selects_new_verified_witness_and_records_its_own_source(self):
        url = f"https://github.com/jlevy/squares/blob/main/{LINKED_PATH}"
        link = f'<a href="{url}">Coordinates witness</a>'
        upper_link = f'<a href="https://github.com/jlevy/squares/blob/main/{WITNESS_PATH}">Older witness</a>'
        current = self.refresh({SOURCE: case_page(linked=link, upper_linked=upper_link),
                                LINKED_API: github_response(LINKED_PATH), WITNESS_API: github_response()})
        witness = current["witness"]
        self.assertTrue(current["witness_refreshed"])
        self.assertEqual(witness["source_url"], url)
        self.assertEqual(witness["value_text"], "5")
        self.assertEqual(witness["side_ticks"], 5_000_000)
        self.assertEqual(SquarePack(18).evaluate(witness["solution"])["exact_side"], "5")

    def test_comparison_uses_exact_math_when_display_is_rounded_up(self):
        upper_math = '<math><mfrac><mn>49</mn><mn>10</mn></mfrac></math>'
        current = self.refresh({SOURCE: case_page(upper="5", upper_math=upper_math),
                                WITNESS_API: github_response()})
        self.assertEqual(current["upper_bound"]["comparison_ticks"], 4_900_000)
        self.assertEqual(current["upper_bound"]["value_text"], "4.9")

    def test_coarse_decimal_alone_cannot_set_a_comparison_threshold(self):
        current = self.refresh({SOURCE: case_page(upper="4.89"), WITNESS_API: github_response()})
        self.assertEqual(current["upper_bound"]["value_text"], "4.89")
        self.assertIsNone(current["upper_bound"]["comparison_ticks"])

    def test_a_rounded_display_crossing_a_microtick_cannot_set_a_threshold(self):
        current = self.refresh({SOURCE: case_page(upper="4.890000000000"), WITNESS_API: github_response()})
        self.assertIsNone(current["upper_bound"]["comparison_ticks"])

    def test_only_proved_status_with_matching_verified_bounds_closes_a_case(self):
        badge = '<span class="site-chip" data-case-status="proved">proved</span>'
        upper_evidence = '<a href="https://github.com/jlevy/squares/blob/main/packing/frontier/evidence.yaml#L1">Proof</a>'
        claimed = badge + case_page(upper="4.80", upper_linked=upper_evidence)
        current = self.refresh({SOURCE: claimed, WITNESS_API: github_response()})
        self.assertIs(current["open"], True)
        self.assertTrue(reference_sync.case_is_open(18, self.directory))

        exact = '<math><mfrac><mn>941</mn><mn>200</mn></mfrac></math>'
        proved = badge + case_page(upper="4.705", upper_math=exact, upper_linked=upper_evidence)
        current = self.refresh({SOURCE: proved, WITNESS_API: github_response()})
        self.assertIs(current["open"], False)
        self.assertFalse(reference_sync.case_is_open(18, self.directory))
        self.assertIs(self.refresh({})["open"], False)

    def test_unrecognizable_sources_keep_previous_successful_cache_bytes(self):
        current = self.refresh({SOURCE: case_page(), WITNESS_API: github_response()})
        [cache_file] = self.directory.iterdir()
        cache_bytes = cache_file.read_bytes()
        fallback = self.refresh({SOURCE: "<p>case migrated</p>", WITNESS_API: "{}"})
        self.assertEqual(fallback["status"], "cached")
        self.assertTrue(fallback["warnings"])
        self.assertEqual(current["lower_bound"], fallback["lower_bound"])
        self.assertEqual(current["witness"], fallback["witness"])
        self.assertEqual(cache_file.read_bytes(), cache_bytes)

    def test_partial_refresh_keeps_witness_after_geometry_rejects_overlaps(self):
        current = self.refresh({SOURCE: case_page(), WITNESS_API: github_response()})
        updated = self.refresh({SOURCE: case_page(upper="4.99"), WITNESS_API: github_response(overlap=True)})
        self.assertEqual(updated["status"], "partial")
        self.assertEqual(updated["upper_bound"]["value_text"], "4.99")
        self.assertEqual(updated["witness"], current["witness"])
        self.assertFalse(updated["witness_refreshed"])
        self.assertEqual(reference_sync.load_reference(18, self.directory)["witness"], current["witness"])
        self.assertFalse(reference_sync.load_reference(18, self.directory)["witness_refreshed"])

    def test_damaged_cache_falls_back_to_the_bundled_geometry(self):
        self.refresh({SOURCE: case_page(), WITNESS_API: github_response()})
        [cache] = self.directory.iterdir()
        damaged = json.loads(cache.read_text(encoding="utf-8"))
        damaged["upper_bound"] = {"value_text": "nan"}
        cache.write_text(json.dumps(damaged), encoding="utf-8")
        fallback = reference_sync.load_reference(18, self.directory)
        self.assertEqual(fallback["status"], "bundled")
        self.assertEqual(fallback["upper_bound"]["comparison_ticks"], 4_822_876)

    def test_wrong_case_and_timeouts_do_not_mark_bundled_values_checked(self):
        fallback = self.refresh({SOURCE: case_page(case=19)})
        self.assertEqual(fallback["status"], "bundled")
        self.assertTrue(fallback["warnings"])
        self.assertIsNone(fallback["lower_bound"]["checked_at"])
        self.assertIsNone(fallback["witness"]["checked_at"])
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_expanded_bundled_references_have_replayable_layouts_and_bounds(self):
        expected = {20: ("4.9125", 5_000_000), 26: ("5.555", 5_621_321),
                    27: ("5.655", 5_707_107), 37: ("6.4975", 6_598_620),
                    41: ("6.775", 6_926_694), 50: ("7.4", 7_571_429)}
        for case, (lower, upper_ticks) in expected.items():
            with self.subTest(case=case):
                pack = get_square_pack(f"square-{case}")
                sources = reference_sync.load_reference(case, self.directory)
                self.assertIs(sources["open"], True)
                self.assertEqual(sources["lower_bound"]["value_text"], lower)
                self.assertEqual(sources["upper_bound"]["comparison_ticks"], upper_ticks)
                self.assertTrue(sources["upper_bound"]["evidence_url"])
                witness = sources["witness"]
                self.assertEqual(pack.evaluate(witness["solution"])["side_ticks"], witness["side_ticks"])
                self.assertLess(witness["side_ticks"], pack.evaluate(pack.seed())["side_ticks"])


if __name__ == "__main__":
    unittest.main()
