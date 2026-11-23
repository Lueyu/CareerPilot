import json
import unittest

from app.services.study_blocks import extract_blocks, teaching_diagram_fallback, wants_teaching_diagram


def reply(block):
    return '原理说明。\n```study-blocks\n' + json.dumps([block], ensure_ascii=False) + '\n```'


class StudyBlockTests(unittest.TestCase):
    def test_short_module_relationship_and_text_only(self):
        question = 'RAG 的索引、检索和生成模块如何协作？'
        self.assertTrue(wants_teaching_diagram('explain', question))
        self.assertFalse(wants_teaching_diagram('explain', question + '只要文字。'))
        self.assertIsNone(teaching_diagram_fallback('队列和 Worker 如何提升吞吐量？'))
        self.assertIsNone(teaching_diagram_fallback('队列和 Worker 故障后如何恢复？只用文字。'))

    def test_ordinary_queue_recovery_question_gets_a_scoped_teaching_diagram(self):
        question = '解释 Agent、队列、Worker 和外部存储之间怎么配合，失败以后怎么恢复？'
        self.assertTrue(wants_teaching_diagram('explain', question))
        diagram = teaching_diagram_fallback(question)
        self.assertEqual(diagram['basis'], 'teaching')
        self.assertEqual(diagram['sources'], [])
        self.assertIn([1, 2, '未确认时可重新投递'], diagram['edges'])
        self.assertFalse(wants_teaching_diagram('interview', question))
        self.assertFalse(wants_teaching_diagram('explain', question + '不要画图。'))
        self.assertFalse(wants_teaching_diagram('explain', '队列是什么意思？'))
        self.assertIsNone(teaching_diagram_fallback('这个项目的数据库状态如何转换？'))

    def test_demo_has_no_executable_or_remote_fields(self):
        value=reply(dict(type='demo',preset='retry',title='退避',summary='教学模拟',src='https://example.invalid',script='ignored'))
        prose, blocks=extract_blocks(value,[])
        self.assertEqual(prose,'原理说明。'); self.assertEqual(set(blocks[0]),{'id','type','title','summary','preset'})

    def test_chart_metric_must_match_exact_message_evidence(self):
        block=dict(type='chart',basis='observed',unit='秒',title='耗时',summary='来源记录',points=[['耗时',2,0,['seconds']]])
        sources=[{'text':'{"seconds":2}'}]; self.assertEqual(len(extract_blocks(reply(block),sources)[1]),1)
        for invalid in [[],[{'text':'{"other":2}'}],[{'text':'{"seconds":3}'}]]:
            self.assertEqual(extract_blocks(reply(block),invalid),(reply(block),[]))

    def test_project_diagram_requires_read_code_and_valid_edges(self):
        block=dict(type='diagram',kind='sequence',basis='project',title='入队',summary='流程',nodes=['入口','队列'],edges=[[0,1,'提交']],sources=[0])
        self.assertFalse(extract_blocks(reply(block),[{'kind':'project_report'}])[1]);self.assertTrue(extract_blocks(reply(block),[{'kind':'project_code'}])[1]);block['edges'][0][1]=5
        self.assertFalse(extract_blocks(reply(block),[{'kind':'project_code'}])[1])

    def test_incomplete_payload_and_trailing_prose_are_retained(self):
        for value in ['正文\n```study-blocks\n{','```study-blocks\n[]\n```\n继续讲','```py\npass\n```']:
            self.assertEqual(extract_blocks(value,[]),(value,[]))
