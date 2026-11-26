from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from evidence import build_report, rehearsal_candidate
from 本地裁判 import LocalJudge, get_pack, get_square_pack, symmetry_key


class EvidenceTests(unittest.TestCase):
    def test_five_distinct_candidates_pass_the_original_geometry_or_coverage_checks(self):
        with TemporaryDirectory() as folder:
            for pack in ('square-18', 'square-19', 'relay-49-frontier'):
                with self.subTest(pack=pack):
                    judge = LocalJudge(Path(folder), pack)
                    square = pack.startswith('square-')
                    rules = get_square_pack(pack) if square else get_pack(pack)
                    seen = set()
                    for turn in range(1, 6):
                        candidate = rehearsal_candidate(pack, turn, judge.task())
                        self.assertEqual(candidate['parent_hash'], judge.metric()['hash'])
                        self.assertIn('离线演练', candidate['handoff'])
                        solution = rules.evaluate(candidate['solution'])['solution']
                        signature = symmetry_key(solution) if square else tuple(map(tuple, solution['blocks']))
                        self.assertNotIn(signature, seen)
                        seen.add(signature)
                        graded = judge.grade(candidate, '离线演练')
                        if square:
                            self.assertTrue(graded['accepted'], graded['message'])
                            self.assertLess(graded['after']['side_ticks'], graded['before']['side_ticks'])
                        else:
                            self.assertFalse(graded['accepted'])
                            self.assertIn('未高于父档', graded['message'])

    def test_live_report_shows_meter_change_rollover_and_token_rates(self):
        def snapshot(at, short, short_reset, week):
            return {'at': at, 'weekly_used': week, 'credits': '0.000', 'windows': [
                {'used': short, 'minutes': 300, 'reset': short_reset},
                {'used': week, 'minutes': 10080, 'reset': 20000},
            ]}

        record = {
            'pack': 'relay-49-frontier', 'turn': 1, 'status': 'done',
            'before': snapshot('10:00:00', 98, 10000, 70),
            'after': snapshot('10:01:00', 1, 15000, 71),
            'model_seconds': 30,
            'model': {'text': '{}', 'usage': {'input_tokens': 20, 'cached_input_tokens': 10,
                                             'output_tokens': 60, 'reasoning_output_tokens': 50}},
            'grade': {'accepted': True, 'message': '已保存', 'before': {'score': 1160, 'total': 1176},
                      'after': {'score': 1161, 'total': 1176}, 'checkpoint': {'tip': 'fixture'}},
        }
        report = build_report(Path('验收'), [record], {
            'mode': 'run', 'packs': ['relay-49-frontier', 'square-18'], 'rounds': 5,
        }, '测试备注')
        for value in ('订阅实跑', '1/5', '0/5', '1,160/1,176', '1,161/1,176', '120.00',
                      '+1 个百分点', '窗口重置', '账户共享', '测试备注'):
            self.assertIn(value, report)
        self.assertNotIn('-97 个百分点', report)

    def test_rehearsal_reports_no_credit_or_token_measurement(self):
        report = build_report(Path('验收'), [{
            'pack': 'square-18', 'turn': 1, 'status': 'done', 'model_seconds': 0.01,
            'before': None, 'after': None, 'model': {'text': '{}', 'usage': None},
            'grade': {'accepted': False, 'message': '已复算', 'before': {'side_ticks': 4825529,
                      'side': '4.825529', 'area': '23.285730129841'}, 'after': {'side_ticks': 4825529,
                      'side': '4.825529', 'area': '23.285730129841'}, 'checkpoint': None},
        }], {'mode': 'replay', 'packs': ['square-18'], 'rounds': 5}, '')
        for value in ('离线演练', '未查询', '未调用模型', '4.825529', '23.285730129841'):
            self.assertIn(value, report)
        self.assertNotIn('订阅实跑', report)

    def test_stopped_run_preserves_observed_baseline_and_unclassified_windows(self):
        report = build_report(Path('验收'), [{
            'pack': 'relay-49-frontier', 'turn': 1, 'status': 'ready',
            'metric_before': {'score': 1160, 'total': 1176},
            'before': {'weekly_used': 87, 'credits': '2.000', 'windows': [
                {'used': 22, 'minutes': None, 'reset': None},
                {'used': 87, 'minutes': 10080, 'reset': 20000},
            ]},
        }], {'mode': 'run', 'packs': ['relay-49-frontier'], 'rounds': 5}, '暂停')
        self.assertIn('0/5', report)
        self.assertIn('1,160/1,176', report)
        self.assertIn('窗口时长未知', report)
        self.assertIn('周套餐已用 87% → —', report)


if __name__ == '__main__':
    unittest.main()
