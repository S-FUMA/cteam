import copy
import json
import os
import tempfile
import unittest

import app


class InstructionBoardTests(unittest.TestCase):
    def setUp(self):
        self.original_path = app.INSTRUCTIONS_FILE
        self.original_instructions = app.instructions
        with open(self.original_path, encoding='utf-8') as source:
            self.initial_records = json.load(source)
        self.temp_directory = tempfile.TemporaryDirectory()
        app.INSTRUCTIONS_FILE = os.path.join(self.temp_directory.name, 'instructions.json')
        app.instructions = copy.deepcopy(self.initial_records)
        self.write_records(self.initial_records)
        self.client = app.app.test_client()
        with self.client.session_transaction() as session:
            session['logged_in'] = True

    def tearDown(self):
        app.INSTRUCTIONS_FILE = self.original_path
        app.instructions = self.original_instructions
        self.temp_directory.cleanup()

    def write_records(self, records):
        with open(app.INSTRUCTIONS_FILE, 'w', encoding='utf-8') as output:
            json.dump(records, output, ensure_ascii=False, indent=2)

    def read_records(self):
        with open(app.INSTRUCTIONS_FILE, encoding='utf-8') as source:
            return json.load(source)

    def test_board_requires_login(self):
        response = app.app.test_client().get('/board')
        self.assertEqual(response.status_code, 302)

    def test_resident_notice_requires_confirmation_and_persists(self):
        form = {
            'target': '住民',
            'district': '市内全域',
            'facility': '',
            'shelter': '',
            'urgency': '高',
            'content': '川の様子を確認してください',
        }
        response = self.client.post('/board', data={**form, 'action': 'create'})
        self.assertIn('住民向け発信の確認', response.get_data(as_text=True))
        self.assertEqual(self.read_records(), self.initial_records)

        response = self.client.post('/board', data={**form, 'action': 'cancel'})
        self.assertIn('入力内容を登録', response.get_data(as_text=True))
        self.assertEqual(self.read_records(), self.initial_records)

        self.client.post('/board', data={**form, 'action': 'create'})
        response = self.client.post('/board', data={**form, 'action': 'confirm_create'})
        self.assertEqual(response.status_code, 302)
        saved = self.read_records()
        self.assertEqual(len(saved), len(self.initial_records) + 1)
        self.assertEqual(saved[-1]['status'], '未対応')
        self.assertEqual(saved[-1]['content_hiragana'], 'かわのようすをかくにんしてください')

    def test_internal_notice_and_status_update(self):
        response = self.client.post('/board', data={
            'action': 'create',
            'target': '防災課',
            'district': '市内全域',
            'facility': '',
            'shelter': '',
            'urgency': '中',
            'content': '<script>test</script>',
        })
        self.assertEqual(response.status_code, 302)
        saved = self.read_records()
        created = saved[-1]
        self.assertEqual(created['target'], '防災課')
        self.assertEqual(created['status'], '未対応')
        self.assertIn('&lt;script&gt;', self.client.get('/board').get_data(as_text=True))

        response = self.client.post('/board', data={
            'action': 'update_status', 'id': str(created['id']), 'status': '対応中',
        })
        self.assertEqual(response.status_code, 302)
        updated = self.read_records()[-1]
        self.assertEqual(updated['status'], '対応中')
        self.assertTrue(updated['updated_at'])

    def test_home_only_shows_resident_notices_with_reading_controls(self):
        app.instructions = [
            {
                'id': 100, 'target': '住民', 'content': '避難してください',
                'content_hiragana': 'ひなんしてください', 'district': '市内全域',
                'facility': '施設A', 'urgency': '高', 'status': '未対応',
            },
            {'id': 101, 'target': '防災課', 'content': '非公開の内部指示'},
        ]
        html = app.app.test_client().get('/').get_data(as_text=True)
        self.assertIn('避難してください', html)
        self.assertIn('ひなんしてください', html)
        self.assertIn('対象施設: 施設A', html)
        self.assertNotIn('非公開の内部指示', html)
        self.assertIn('toggleEasyJapanese', html)
        self.assertIn('toggleResidentTextSize', html)

    def test_invalid_values_and_save_failure_do_not_modify_records(self):
        before = copy.deepcopy(app.instructions)
        invalid = self.client.post('/board', data={
            'action': 'create',
            'target': '存在しない宛先',
            'district': '市内全域',
            'facility': '',
            'shelter': '',
            'urgency': '緊急',
            'content': '入力検証',
        })
        invalid_html = invalid.get_data(as_text=True)
        self.assertIn('宛先を選択してください。', invalid_html)
        self.assertIn('緊急度を選択してください。', invalid_html)
        self.assertEqual(self.read_records(), self.initial_records)
        self.assertEqual(app.instructions, before)

        app.INSTRUCTIONS_FILE = os.path.join(self.temp_directory.name, 'missing', 'instructions.json')
        with self.assertLogs(level='ERROR'):
            failed = self.client.post('/board', data={
                'action': 'create',
                'target': '防災課',
                'district': '市内全域',
                'facility': '',
                'shelter': '',
                'urgency': '中',
                'content': '保存失敗の確認',
            })
        self.assertIn('保存に失敗しました。', failed.get_data(as_text=True))
        self.assertEqual(app.instructions, before)


if __name__ == '__main__':
    unittest.main()