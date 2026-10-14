import json
import tempfile
import unittest
from pathlib import Path
from bot import APIError, BACK, Bot, CANCEL, DONE, FIELDS, NEW, SEND, SKIP, Store, summary, valid_map_link


class FakeAPI:
    def __init__(self):
        self.calls = []
        self.fail = None

    def call(self, method, **params):
        if self.fail == method:
            self.fail = None
            raise APIError(429, 1)
        self.calls.append((method, params))
        return {'message_id': len(self.calls)}


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'db.sqlite'
        self.store = Store(self.path)
        self.api = FakeAPI()
        self.bot = Bot(self.api, self.store, -123)
        self.counter = 0

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def send(self, text='', user=42, chat_type='private', **extra):
        self.counter += 1
        self.bot.handle({'update_id': self.counter, 'message': {
            'from': {'id': user, 'first_name': 'Тадбиркор'},
            'chat': {'id': user, 'type': chat_type}, 'text': text, **extra}})

    def fill(self, photos=5, user=42):
        self.send(NEW, user=user)
        for text in ['Ресторан', 'Тест <Кафе>', 'Миллий таомлар.', 'Наманган шаҳри', 'Гулзор кўчаси, 7-уй']:
            self.send(text, user=user)
        self.send(user=user, location={'latitude': 40.99, 'longitude': 71.67})
        self.send('+998 90 123 45 67', user=user)
        for text in ['09:00–23:00', 'Али Вали', SKIP]:
            self.send(text, user=user)
        for i in range(photos):
            self.send(user=user, photo=[{'file_id': f'f{i}', 'file_unique_id': f'u{i}'}])

    def test_end_to_end_five_photos_and_submit_once(self):
        self.fill()
        self.send(DONE)
        draft = self.store.draft(42)
        self.assertEqual(draft['step'], len(FIELDS))
        self.assertIn('&lt;Кафе&gt;', summary(draft))
        self.assertEqual(draft['data']['phone'], '+998901234567')
        self.send(SEND)
        self.send(SEND)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        self.bot.flush()
        admin = [(m, p) for m, p in self.api.calls if p.get('chat_id') == -123]
        self.assertEqual([m for m, _ in admin], ['sendMessage', 'sendMediaGroup', 'sendLocation'])
        self.assertEqual(len(admin[1][1]['media']), 5)
        self.assertTrue(self.store.db.execute('SELECT complete FROM jobs').fetchone()[0])
        self.assertIn('етказилди', self.api.calls[-1][1]['text'])

    def test_minimum_maximum_and_deduplication(self):
        self.fill(4)
        self.send(DONE)
        self.assertEqual(self.store.draft(42)['step'], 10)
        self.send(photo=[{'file_id': 'again', 'file_unique_id': 'u0'}])
        self.assertEqual(len(self.store.draft(42)['data']['photos']), 4)
        for i in range(4, 7):
            self.send(photo=[{'file_id': f'f{i}', 'file_unique_id': f'u{i}'}])
        self.assertEqual(len(self.store.draft(42)['data']['photos']), 6)
        self.send(DONE)
        self.assertEqual(self.store.draft(42)['step'], 11)

    def test_partial_delivery_retries_only_missing_parts(self):
        self.fill()
        self.send(DONE)
        self.send(SEND)
        self.api.fail = 'sendMediaGroup'
        self.bot.flush()
        job = self.store.db.execute('SELECT * FROM jobs').fetchone()
        self.assertEqual(job['phase'], 1)
        self.assertEqual(job['complete'], 0)
        self.store.db.execute('UPDATE jobs SET retry_at=0')
        self.store.db.commit()
        self.bot.flush()
        admin_texts = [p for m, p in self.api.calls if m == 'sendMessage' and p['chat_id'] == -123]
        self.assertEqual(len(admin_texts), 1)
        self.assertEqual(self.store.db.execute('SELECT complete FROM jobs').fetchone()[0], 1)

    def test_persistence_and_user_isolation(self):
        self.send(NEW)
        self.send('Ресторан')
        self.send(NEW, user=99)
        self.store.set_offset(12345)
        self.store.db.close()
        self.store = Store(self.path)
        self.bot.store = self.store
        self.assertEqual(self.store.draft(42)['step'], 1)
        self.assertEqual(self.store.draft(99)['step'], 0)
        self.assertEqual(self.store.offset(), 12345)

    def test_back_and_cancel(self):
        self.fill()
        self.send(DONE)
        self.send(BACK)
        self.assertEqual(self.store.draft(42)['data']['photos'], [])
        self.send(CANCEL)
        self.assertIsNone(self.store.draft(42))

    def test_invalid_phone_and_location_do_not_advance(self):
        self.send(NEW)
        for text in ['Ресторан', 'Кафе', 'Таомлар', 'Наманган', 'Манзил']:
            self.send(text)
        self.send('https://example.org')
        self.assertEqual(self.store.draft(42)['step'], 5)
        self.send('https://maps.app.goo.gl/abc')
        self.send('900')
        self.assertEqual(self.store.draft(42)['step'], 6)
        self.send(contact={'phone_number': '901234567'})
        self.assertEqual(self.store.draft(42)['data']['phone'], '+998901234567')

    def test_group_does_not_collect_personal_data(self):
        self.send(NEW, chat_type='supergroup')
        self.assertIsNone(self.store.draft(42))
        self.send('/chatid@TestBot', chat_type='supergroup')
        self.assertIn('42', self.api.calls[-1][1]['text'])

    def test_not_configured_bot_blocks_form(self):
        self.bot.admin = None
        self.send(NEW)
        self.assertIsNone(self.store.draft(42))

    def test_map_domains(self):
        self.assertTrue(valid_map_link('https://www.google.com/maps?q=40,71'))
        self.assertTrue(valid_map_link('https://yandex.uz/maps/'))
        self.assertFalse(valid_map_link('https://google.com.evil.test/maps'))
        self.assertFalse(valid_map_link('https://user@maps.app.goo.gl/id'))
        self.assertFalse(valid_map_link('http://maps.app.goo.gl/id'))

    def test_map_url_delivery_without_coordinate_message(self):
        self.fill()
        draft = self.store.draft(42)
        draft['data']['location'] = {'url': 'https://maps.app.goo.gl/test'}
        self.store.save(42, draft)
        self.send(DONE)
        self.send(SEND)
        self.bot.flush()
        self.assertFalse(any(method == 'sendLocation' for method, _ in self.api.calls))
        self.assertEqual(self.store.db.execute('SELECT complete FROM jobs').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
