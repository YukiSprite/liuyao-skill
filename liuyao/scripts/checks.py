#!/usr/bin/env python3
"""离线行为检查：独立卦例、全部动静组合、爻位不变量和历法边界。"""
import copy
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
from paipan import build_chart, make_calendar, void_branches, render_markdown, render_text_diagram, InputError
from tables import (HEXAGRAMS, TRIGRAMS, TRIGRAM_BY_BITS, BRANCHES, STEMS, KIN,
                    najia, relation, kin_for)


def request(tosses=None, **changes):
    data = {"question": "测试问题", "tosses": tosses if tosses is not None else [1, 1, 0, 2, 1, 3],
            "input_mode": "back_count", "time": {"kind": "unknown"}}
    data.update(changes)
    return data


class ChartTests(unittest.TestCase):
    def test_all_64_names_trigrams_palaces_and_shiying(self):
        self.assertEqual(set(HEXAGRAMS), set(range(64)))
        self.assertEqual(len({r['name'] for r in HEXAGRAMS.values()}), 64)
        # 从独立的内外爻比较口诀验证宫序表，避免用同一掩码生成期望答案。
        self_by_difference = {0: 6, 1: 1, 2: 3, 3: 2, 4: 5, 5: 4, 6: 4, 7: 3}
        for bits, meta in HEXAGRAMS.items():
            lower, upper = bits & 7, bits >> 3
            difference = lower ^ upper
            expected_self = self_by_difference[difference]
            self.assertEqual(meta['self_position'], expected_self)
            self.assertEqual(abs(meta['self_position'] - meta['other_position']), 3)
            palace_bits = lower if difference in (0, 2) else upper if expected_self <= 3 else lower ^ 7
            self.assertEqual(meta['palace'], TRIGRAM_BY_BITS[palace_bits])
            if lower == upper:
                self.assertTrue(meta['name'].startswith(TRIGRAM_BY_BITS[lower] + '为'))
            else:
                self.assertTrue(meta['name'].startswith(TRIGRAMS[meta['upper']]['image'] + TRIGRAMS[meta['lower']]['image']))

    def test_all_4096_toss_combinations(self):
        for tosses in itertools.product(range(4), repeat=6):
            chart = build_chart(request(list(tosses)))
            self.assertEqual([r['position'] for r in chart['lines_bottom_up']], list(range(1, 7)))
            expected_yang = [n in (1, 3) for n in tosses]
            expected_moving = [i + 1 for i, n in enumerate(tosses) if n in (0, 3)]
            self.assertEqual([r['yang'] for r in chart['lines_bottom_up']], expected_yang)
            self.assertEqual(chart['moving_positions'], expected_moving)
            if not expected_moving:
                self.assertIsNone(chart['changed'])
            else:
                expected_changed = [not yang if n in (0, 3) else yang for yang, n in zip(expected_yang, tosses)]
                self.assertEqual([r['yang'] for r in chart['changed']['layout_bottom_up']], expected_changed)
            missing = set(KIN) - {r['kin'] for r in chart['lines_bottom_up']}
            hidden_kin = {h['kin'] for r in chart['lines_bottom_up'] for h in r['hidden']}
            self.assertEqual(missing, hidden_kin)
            for row in chart['lines_bottom_up']:
                self.assertEqual(row['change'] is not None, row['position'] in expected_moving)
                if row['change']:
                    self.assertEqual(row['change']['kin'], kin_for(chart['original']['palace_element'], row['change']['element']))
            for edge in chart['nominal_edges']:
                if '化出' in edge['source']:
                    self.assertEqual(edge['source'][0], edge['target'][2])

    def test_tutorial_golden(self):
        data = request(focus='妻财', time={'kind': 'civil', 'at': '2021-10-13T09:14:00', 'timezone': 'Asia/Shanghai'})
        original_input = copy.deepcopy(data)
        c = build_chart(data)
        self.assertEqual(data, original_input)
        self.assertEqual(c['original']['name'], '风泽中孚')
        self.assertEqual(c['changed']['name'], '水天需')
        self.assertEqual((c['original']['palace'], c['original']['stage'], c['original']['self_position'], c['original']['other_position']), ('艮', '游魂', 4, 1))
        self.assertEqual([r['ganzhi'] for r in c['lines_bottom_up']], ['丁巳', '丁卯', '丁丑', '辛未', '辛巳', '辛卯'])
        self.assertEqual([r['spirit'] for r in c['lines_bottom_up']], ['青龙', '朱雀', '勾陈', '螣蛇', '白虎', '玄武'])
        self.assertEqual(c['lines_bottom_up'][2]['change']['ganzhi'], '甲辰')
        self.assertEqual(c['lines_bottom_up'][5]['change']['ganzhi'], '戊子')
        self.assertEqual(c['lines_bottom_up'][4]['hidden'][0]['ganzhi'], '丙子')
        self.assertEqual(c['focus_candidates'][0]['location'], '伏神')
        cal = c['calendar']
        self.assertEqual([cal[k] for k in ('year_ganzhi', 'month_ganzhi', 'day_ganzhi', 'hour_ganzhi')], ['辛丑', '戊戌', '甲午', '己巳'])
        self.assertEqual(cal['void'], ['辰', '巳'])

    def test_independent_book_guai_to_gou(self):
        # E2 明示：泽天夬（坤宫）初、上动，变天风姤（乾宫）。
        c = build_chart(request([3, 1, 1, 1, 1, 0]))
        self.assertEqual(c['original']['name'], '泽天夬')
        self.assertEqual(c['changed']['name'], '天风姤')
        self.assertEqual(c['original']['self_position'], 5)
        self.assertEqual(c['original']['palace_element'], '土')
        self.assertEqual(c['changed']['palace_element'], '金')
        self.assertEqual(c['lines_bottom_up'][0]['change']['kin'], '兄弟')
        self.assertEqual(c['lines_bottom_up'][5]['change']['kin'], '兄弟')
        self.assertEqual([c['lines_bottom_up'][i]['change']['ganzhi'] for i in (0, 5)], ['辛丑', '壬戌'])
        self.assertTrue(all(c['lines_bottom_up'][i]['change'] is None for i in (1, 2, 3, 4)))

    def test_najia_fixed_sequences(self):
        expected = {'乾': '子寅辰午申戌', '兑': '巳卯丑亥酉未', '离': '卯丑亥酉未巳', '震': '子寅辰午申戌',
                    '巽': '丑亥酉未巳卯', '坎': '寅辰午申戌子', '艮': '辰午申戌子寅', '坤': '未巳卯丑亥酉'}
        for trigram, sequence in expected.items():
            self.assertEqual(''.join(x[1] for x in najia(TRIGRAMS[trigram]['bits'] * 9)), sequence)

    def test_input_modes_and_order(self):
        expected = build_chart(request())
        variants = [request([2, 2, 3, 1, 2, 0], input_mode='front_count'),
                    request([7, 7, 6, 8, 7, 9], input_mode='line_values'),
                    request([9, 7, 8, 6, 7, 7], input_mode='line_values', order='top_to_bottom')]
        for data in variants:
            c = build_chart(data)
            self.assertEqual(c['normalized_values_bottom_up'], expected['normalized_values_bottom_up'])
            self.assertEqual(c['original'], expected['original'])
            self.assertEqual(c['changed'], expected['changed'])

    def test_void_all_60_days(self):
        for start in range(0, 60, 10):
            occupied = {BRANCHES[i % 12] for i in range(start, start + 10)}
            for i in range(start, start + 10):
                self.assertEqual(set(void_branches(STEMS[i % 10] + BRANCHES[i % 12])), set(BRANCHES) - occupied)

    def test_relation_direction_and_non_inference(self):
        self.assertEqual(relation('申', '戌')['element_relation'], '被生')
        self.assertEqual(relation('戌', '申')['element_relation'], '生')
        self.assertFalse(relation('寅', '午')['clashes'])
        self.assertTrue(relation('子', '午')['clashes'])
        self.assertTrue(relation('卯', '戌')['combines'])
        self.assertEqual(relation('亥', '子')['element_relation'], '比和')
        c = build_chart(request([1]*6, time={'kind':'manual', 'day_ganzhi':'甲子', 'month_branch':'午', 'source':'测试'}))
        self.assertIsNone(c['changed'])
        self.assertEqual(c['nominal_edges'], [])
        self.assertIn('日冲（暗动或日破须另判）', c['lines_bottom_up'][3]['flags'])

    def test_missing_time_and_multiple_focus(self):
        c = build_chart(request([1]*6, focus='父母'))
        self.assertEqual(len(c['focus_candidates']), 2)
        self.assertEqual(c['calendar']['status'], 'missing')
        self.assertTrue(all(r['spirit'] is None and not r['flags'] for r in c['lines_bottom_up']))

    def test_all_day_stem_spirit_heads(self):
        heads = ('青龙','青龙','朱雀','朱雀','勾陈','螣蛇','白虎','白虎','玄武','玄武')
        for i, head in enumerate(heads):
            c = build_chart(request(time={'kind':'manual','month_branch':'寅','day_ganzhi':STEMS[i]+BRANCHES[i],'source':'测试'}))
            self.assertEqual(c['lines_bottom_up'][0]['spirit'], head)
            self.assertEqual(len({r['spirit'] for r in c['lines_bottom_up']}),6)

    def test_bad_inputs(self):
        bad = [request([1]*5), request([1]*7), request([1,1,1,1,1,4]), request([1,1,1,1,1,True]),
               request([1,1,1,1,1,1.0]), request([1,1,1,1,1,6]), request(question=' '), request(input_mode=None),
               request(order='unknown'), request(focus='官星'), request(time={'kind':'manual','day_ganzhi':'甲丑','month_branch':'寅','source':'测试'}),
               request(time={'kind':'civil','at':'2026-02-30T12:00'}), request(time={'kind':'civil','at':'2026-09-13'}),
               request(time={'kind':'civil','at':'2026-09-13T12:00','timezone':'Not/AZone'}),
               request(time={'kind':'manual','day_ganzhi':'甲子','month_branch':'寅'}),
               request(time={'kind':'unknown','at':'2026-09-13T12:00'}), request(extra='字段')]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(InputError):
                build_chart(data)


class CalendarTests(unittest.TestCase):
    def civil(self, at, **kwargs):
        return make_calendar({'kind':'civil','at':at,'timezone':'Asia/Shanghai',**kwargs})

    def test_midnight_and_zi_boundaries(self):
        self.assertEqual(self.civil('2021-10-13T22:59')['day_ganzhi'],'甲午')
        self.assertEqual(self.civil('2021-10-13T23:00')['day_ganzhi'],'甲午')
        c = self.civil('2021-10-13T23:00',day_boundary='zi23')
        self.assertEqual(c['day_ganzhi'],'乙未')
        self.assertEqual(c['hour_ganzhi'],'丙子')
        self.assertEqual(self.civil('2021-10-13T23:00')['hour_ganzhi'],'甲子')
        self.assertEqual(self.civil('2021-10-14T00:00')['day_ganzhi'],'乙未')

    def test_solar_term_exact_transition_and_timezone(self):
        # 同一真实时刻换时区不应改变节气年、月；跨当天立春节令才换。
        before = self.civil('2024-02-04T12:00')
        after = self.civil('2024-02-04T20:00')
        self.assertEqual((before['year_ganzhi'],before['month_ganzhi']),('癸卯','乙丑'))
        self.assertEqual((after['year_ganzhi'],after['month_ganzhi']),('甲辰','丙寅'))
        overseas = self.civil('2024-02-04T20:00:00+08:00',timezone='America/New_York')
        self.assertEqual(overseas['year_ganzhi'],after['year_ganzhi'])
        self.assertEqual(overseas['month_ganzhi'],after['month_ganzhi'])

    def test_dst_ambiguity_and_gap(self):
        for at in ('2024-11-03T01:30','2024-03-10T02:30'):
            with self.assertRaises(InputError):
                self.civil(at,timezone='America/New_York')
        self.assertEqual(self.civil('2024-11-03T01:30:00-04:00',timezone='America/New_York')['status'],'calculated')


class CommandTests(unittest.TestCase):
    def test_text_diagram_mixed_lines_and_static_hexagram(self):
        c = build_chart(request([0, 1, 2, 0, 3, 1]))
        before = copy.deepcopy(c)
        diagram = render_text_diagram(c)
        self.assertEqual(c, before)
        self.assertEqual(c['text_diagram'], diagram)
        rows = [line for line in diagram.splitlines() if line.startswith(('上爻', '五爻', '四爻', '三爻', '二爻', '初爻'))]
        self.assertEqual([line[:2] for line in rows], ['上爻', '五爻', '四爻', '三爻', '二爻', '初爻'])
        # 手工卦例：涣的上至初爻为阳阳阴阴阳阴，睽为阳阴阳阴阳阳。
        expected = [(True, True), (True, False), (False, True), (False, False), (True, True), (False, True)]
        for line, (left, right) in zip(rows, expected):
            self.assertTrue(line[6:].startswith('━━━━━━━━━' if left else '━━━   ━━━'))
            self.assertTrue(line.endswith('━━━━━━━━━' if right else '━━━   ━━━'))
        self.assertEqual([line[:2] for line in rows if '→' in line], ['五爻', '四爻', '初爻'])
        self.assertIn('○ 世', rows[1])
        self.assertIn('应', rows[4])
        self.assertIn('×', rows[2])
        self.assertIn('×', rows[5])
        self.assertIn('```text\n' + diagram + '\n```', render_markdown(c))
        for toss, line_shape in [(1, '━━━━━━━━━'), (2, '━━━   ━━━')]:
            static = build_chart(request([toss] * 6))['text_diagram']
            static_rows = [line for line in static.splitlines() if line.startswith(('上爻', '五爻', '四爻', '三爻', '二爻', '初爻'))]
            self.assertEqual(len(static_rows), 6)
            self.assertTrue(all(line.count(line_shape) == 1 for line in static_rows))
            self.assertFalse(any(symbol in static for symbol in ('→', '○', '×', '变卦：')))

    def test_cli_output_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            src = tmp/'input.json'
            src.write_text(json.dumps(request(),ensure_ascii=False),encoding='utf-8')
            target = tmp/'result'
            command = [sys.executable,'-B',str(Path(__file__).with_name('paipan.py')),'--input',str(src),'--output-dir',str(target)]
            first = subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            saved = (target/'chart.json').read_bytes()
            self.assertTrue((target/'chart.md').is_file())
            self.assertEqual((target/'diagram.txt').read_text(encoding='utf-8').rstrip('\n'), json.loads(saved)['text_diagram'])
            second = subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(second.returncode,2)
            self.assertEqual((target/'chart.json').read_bytes(),saved)

    def test_markdown_escaping_and_line_order(self):
        report = render_markdown(build_chart(request(question='问题|换行\n<script>内容</script>')))
        self.assertIn('问题\\|换行<br>&lt;script&gt;',report)
        self.assertLess(report.index('| 上爻 |'),report.index('| 初爻 |'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
