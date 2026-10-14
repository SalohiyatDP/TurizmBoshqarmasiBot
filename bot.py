#!/usr/bin/env python3
"""Namangan tourism intake bot. Python 3.10+, standard library only."""
import argparse
import getpass
import html
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
NEW = '📝 Янги сўровнома'
RESUME = '▶️ Давом эттириш'
BACK = '⬅️ Орқага'
CANCEL = '❌ Бекор қилиш'
DONE = '✅ Расмлар тайёр'
SEND = '📨 Бошқармага юбориш'
SKIP = '⏭ Ўтказиб юбориш'
FIELDS = ['kind', 'name', 'description', 'district', 'address', 'location',
          'phone', 'hours', 'contact', 'social', 'photos']
LABELS = {'kind': 'Фаолият тури', 'name': 'Объект номи',
          'description': 'Қисқача маълумот', 'district': 'Туман / шаҳар',
          'address': 'Манзил ва мўлжал', 'phone': 'Телефон',
          'hours': 'Иш вақти', 'contact': 'Масъул шахс', 'social': 'Ижтимоий тармоқ'}
PROMPTS = {
    'kind': '1/11. Фаолият турини танланг.',
    'name': '2/11. Ресторан ёки дўконнинг номини ёзинг.',
    'description': '3/11. Таомлар, маҳсулотлар ёки хизматлар ҳақида 2–3 жумла ёзинг (1000 белгидан ошмасин).',
    'district': '4/11. Қайси туман ёки шаҳарда жойлашган?',
    'address': '5/11. Маҳалла, кўча, уй рақами ва мўлжални ёзинг.',
    'location': '6/11. 📍 Локацияни юборинг: 📎 → Жойлашув → харитада объект жойини танланг. Ёки Google Maps / Yandex Maps ҳаволасини юборинг. Тугма сизнинг ҳозирги жойлашувингизни юборади — объектда бўлсангиз фойдаланинг.',
    'phone': '7/11. Алоқа телефон рақамини ёзинг: +998 90 123 45 67. Ёки контакт тугмасини босинг.',
    'hours': '8/11. Иш кунлари ва вақтини ёзинг. Масалан: ҳар куни 09:00–23:00.',
    'contact': '9/11. Масъул шахснинг исм-фамилиясини ёзинг.',
    'social': '10/11. Telegram / Instagram саҳифаси ёки сайт ҳаволасини юборинг. Мавжуд бўлмаса «Ўтказиб юбориш»ни босинг.',
    'photos': '11/11. 📸 5–6 та турли сифатли расм юборинг: ташқи кўриниш, пешлавҳа, ички муҳит, таомлар / маҳсулотлар. Альбом ёки алоҳида расм қилиб юбориш мумкин. Сўнг «Расмлар тайёр»ни босинг.'}


def load_env(path=ROOT / '.env'):
    if path.exists():
        for raw in path.read_text(encoding='utf-8-sig').splitlines():
            raw = raw.strip()
            if raw and not raw.startswith('#') and '=' in raw:
                key, value = raw.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"\''))


class APIError(Exception):
    def __init__(self, code=0, retry_after=0):
        super().__init__(f'Telegram API error ({code})')
        self.code, self.retry_after = code, retry_after


class Telegram:
    def __init__(self, token):
        self.url = 'https://api.telegram.org/bot' + token + '/'

    def call(self, method, **params):
        request = urllib.request.Request(self.url + method,
            data=json.dumps(params).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            try:
                result = json.load(exc)
            except (ValueError, OSError):
                raise APIError(exc.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            # Never log a request URL; it contains the token.
            raise APIError() from None
        if not result.get('ok'):
            raise APIError(result.get('error_code', 0), result.get('parameters', {}).get('retry_after', 0))
        return result['result']


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS drafts (user INTEGER PRIMARY KEY, payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user INTEGER NOT NULL,
            payload TEXT NOT NULL, phase INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
            retry_at REAL DEFAULT 0, complete INTEGER DEFAULT 0);
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        ''')

    def draft(self, user):
        row = self.db.execute('SELECT payload FROM drafts WHERE user=?', (user,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, user, draft):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?)', (user, json.dumps(draft)))

    def delete(self, user):
        with self.db:
            self.db.execute('DELETE FROM drafts WHERE user=?', (user,))

    def enqueue(self, user, draft):
        ident = draft['id']
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO jobs (id,user,payload) VALUES (?,?,?)',
                            (ident, user, json.dumps(draft)))
            draft['submitted'] = True
            self.db.execute('INSERT OR REPLACE INTO drafts VALUES (?,?)', (user, json.dumps(draft)))
        return ident

    def offset(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='offset'").fetchone()
        return int(row[0]) if row else 0

    def set_offset(self, value):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('offset',?)", (str(value),))


def keyboard(rows):
    return {'keyboard': [[{'text': item} if isinstance(item, str) else item for item in row]
                         for row in rows], 'resize_keyboard': True}


def valid_map_link(text):
    from urllib.parse import urlparse
    parsed = urlparse(text)
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password:
        return False
    return (host in {'maps.app.goo.gl', 'goo.gl', 'maps.google.com', 'maps.yandex.ru',
                     'maps.yandex.com', 'yandex.uz', 'yandex.ru', 'yandex.com', '2gis.uz'}
            or (host in {'google.com', 'www.google.com'} and parsed.path.startswith('/maps')))


def summary(draft, admin=False):
    data = draft['data']
    lines = ['📋 ' + ('ЯНГИ ОБЪЕКТ СЎРОВНОМАСИ' if admin else 'МАЪЛУМОТЛАРНИ ТЕКШИРИНГ'),
             'Рақам: ' + draft['id']]
    for key in LABELS:
        lines.append(f'<b>{LABELS[key]}:</b> {html.escape(data.get(key, "—"))}')
    loc = data['location']
    link = (f'https://www.google.com/maps?q={loc["latitude"]},{loc["longitude"]}'
            if 'latitude' in loc else loc['url'])
    lines.append('📍 Локация: ' + html.escape(link))
    lines.append(f'📸 Расмлар: {len(data.get("photos", []))} та')
    if admin:
        sender = draft['sender']
        lines.append(f'Юборувчи: <a href="tg://user?id={sender["id"]}">{html.escape(sender["name"])}</a>')
        if sender.get('username'):
            lines.append('@' + html.escape(sender['username']))
    return '\n\n'.join(lines)


class Bot:
    def __init__(self, api, store, admin_chat=None):
        self.api, self.store, self.admin = api, store, admin_chat

    def say(self, chat, text, rows=None):
        params = {'chat_id': chat, 'text': text, 'parse_mode': 'HTML'}
        if rows is not None:
            params['reply_markup'] = keyboard(rows)
        return self.api.call('sendMessage', **params)

    def prompt(self, user, draft):
        step = draft['step']
        if step == len(FIELDS):
            return self.say(user, summary(draft) + '\n\n«Бошқармага юбориш» тугмасини босганда ушбу маълумотлар ва расмлар Туризм бошқармасига юборилади.', [[SEND], [BACK, CANCEL]])
        key = FIELDS[step]
        rows = []
        if key == 'kind':
            rows = [['Ресторан', 'Кафе'], ['Савдо дўкони', 'Бошқа']]
        elif key == 'phone':
            rows = [[{'text': '📞 Контактни юбориш', 'request_contact': True}]]
        elif key == 'location':
            rows = [[{'text': '📍 Ҳозирги жойлашувим', 'request_location': True}]]
        elif key == 'social':
            rows = [[SKIP]]
        elif key == 'photos':
            rows = [[DONE]]
        rows.append([BACK, CANCEL] if step else [CANCEL])
        self.say(user, PROMPTS[key], rows)

    def handle(self, update):
        msg = update.get('message')
        if not msg or not msg.get('from') or msg['from'].get('is_bot'):
            return
        user, chat = msg['from']['id'], msg['chat']['id']
        text = msg.get('text', '').strip()
        command = text.split()[0].split('@')[0] if text.startswith('/') else ''
        if command == '/chatid':
            self.say(chat, f'Ушбу чат ID рақами: <code>{chat}</code>')
            return
        if msg['chat']['type'] != 'private':
            return
        draft = self.store.draft(user)
        if command == '/status':
            row = self.store.db.execute('SELECT id,complete FROM jobs WHERE user=? ORDER BY rowid DESC LIMIT 1', (user,)).fetchone()
            self.say(user, ('Сўровнома ' + row['id'] + (': бошқармага етказилди ✅' if row['complete'] else ': юбориш навбатида ⏳')) if row else 'Ҳали сўровнома юборилмаган.', [[NEW]])
            return
        if command == '/start':
            rows = [[NEW]]
            if draft and not draft.get('submitted'):
                rows.insert(0, [RESUME])
            self.say(user, 'Хуш келибсиз!\n\nНаманган вилоятидаги ресторан, кафе ва савдо дўконлари ҳақидаги маълумотларни Туризм бошқармасига юбориш учун сўровномани тўлдиринг.\n\nОбъект номи, қисқача маълумот, манзил, локация, телефон ва 5–6 та расм талаб этилади.', rows)
            return
        if text == CANCEL or command == '/cancel':
            self.store.delete(user)
            self.say(user, 'Тўлдирилаётган сўровнома бекор қилинди. Олдин юборилган сўровномалар бекор қилинмайди.', [[NEW]])
            return
        if text == NEW or command == '/new':
            if not self.admin:
                self.say(user, 'Ботни созлаш давом этмоқда. Қабул қилувчи гуруҳ улангач сўровнома очилади.')
                return
            if draft and not draft.get('submitted'):
                self.say(user, 'Тугалланмаган сўровномангиз бор. Давом эттиринг ёки бекор қилиб янги сўровнома очинг.', [[RESUME], [CANCEL]])
                return
            sender = msg['from']
            draft = {'id': uuid.uuid4().hex[:12], 'step': 0, 'data': {},
                     'created_at': time.time(), 'sender': {'id': user,
                     'name': ' '.join(filter(None, [sender.get('first_name'), sender.get('last_name')])),
                     'username': sender.get('username')}}
            self.store.save(user, draft)
            self.prompt(user, draft)
            return
        if not draft:
            self.say(user, 'Сўровномани бошлаш учун тугмани босинг.', [[NEW]])
            return
        if draft.get('submitted'):
            self.say(user, 'Сўровнома қабул қилинган. Ҳолатини /status орқали текширинг.', [[NEW]])
            return
        if text == RESUME:
            self.prompt(user, draft)
            return
        if text == BACK:
            draft['step'] = max(0, draft['step'] - 1)
            if FIELDS[draft['step']] == 'photos':
                draft['data']['photos'] = []
            self.store.save(user, draft)
            self.prompt(user, draft)
            return
        if draft['step'] == len(FIELDS):
            if text != SEND:
                self.say(user, 'Юбориш ёки орқага қайтиш тугмасини босинг.', [[SEND], [BACK, CANCEL]])
                return
            ident = self.store.enqueue(user, draft)
            self.say(user, f'⏳ Сўровнома қабул қилинди. Рақам: {ident}. Бошқармага етказилгач хабар берамиз.', [[NEW]])
            return
        key = FIELDS[draft['step']]
        data = draft['data']
        value = None
        if key == 'photos':
            photos = data.setdefault('photos', [])
            if text == DONE:
                if len(photos) < 5:
                    self.say(user, f'Камида 5 та расм керак. Ҳозир {len(photos)} та. Яна {5-len(photos)} та расм юборинг.')
                    return
            elif msg.get('photo'):
                photo = msg['photo'][-1]
                if any(p['unique'] == photo['file_unique_id'] for p in photos):
                    self.say(user, 'Бу расм аллақачон қўшилган. Бошқа расм юборинг.')
                    return
                if len(photos) >= 6:
                    self.say(user, '6 та расм қабул қилинди. «Расмлар тайёр» тугмасини босинг.')
                    return
                photos.append({'file': photo['file_id'], 'unique': photo['file_unique_id']})
                self.store.save(user, draft)
                self.say(user, f'📸 {len(photos)}/6 та расм қабул қилинди.' + (' «Расмлар тайёр»ни босишингиз мумкин.' if len(photos) >= 5 else ' Давом этинг.'))
                return
            else:
                self.say(user, 'Расмни «Фото» сифатида юборинг. 5–6 та расмдан сўнг «Расмлар тайёр»ни босинг.')
                return
        elif key == 'location':
            loc = msg.get('location') or msg.get('venue', {}).get('location')
            if loc and -90 <= loc['latitude'] <= 90 and -180 <= loc['longitude'] <= 180:
                value = {'latitude': loc['latitude'], 'longitude': loc['longitude']}
            elif len(text) <= 500 and valid_map_link(text):
                value = {'url': text}
            else:
                self.say(user, 'Telegram локацияси ёки Google Maps / Yandex Maps HTTPS ҳаволасини юборинг.')
                return
        elif key == 'phone':
            raw = msg.get('contact', {}).get('phone_number', text)
            digits = re.sub(r'[\s()+-]', '', raw)
            if re.fullmatch(r'\d{9}', digits):
                digits = '998' + digits
            if not re.fullmatch(r'\d{10,15}', digits):
                self.say(user, 'Телефонни текширинг. Масалан: +998 90 123 45 67.')
                return
            value = '+' + digits
        else:
            if key == 'social' and text == SKIP:
                value = 'Мавжуд эмас'
            elif not text or text.startswith('/'):
                self.say(user, 'Маълумотни матн шаклида ёзинг.')
                return
            elif len(text) > (1000 if key == 'description' else 250):
                self.say(user, 'Матн жуда узун. Қисқароқ ёзинг.')
                return
            elif key == 'kind' and text not in {'Ресторан', 'Кафе', 'Савдо дўкони', 'Бошқа'}:
                self.say(user, 'Фаолият турини тугмалардан танланг.')
                return
            else:
                value = text
        if value is not None:
            data[key] = value
        draft['step'] += 1
        self.store.save(user, draft)
        self.prompt(user, draft)

    def flush(self):
        if not self.admin:
            return
        jobs = self.store.db.execute('SELECT * FROM jobs WHERE complete=0 AND retry_at<=? ORDER BY rowid LIMIT 10', (time.time(),)).fetchall()
        for job in jobs:
            draft = json.loads(job['payload'])
            phase = job['phase']
            try:
                if phase == 0:
                    self.say(self.admin, summary(draft, admin=True))
                    phase = 1
                    self.progress(job['id'], phase)
                if phase == 1:
                    media = [{'type': 'photo', 'media': p['file']} for p in draft['data']['photos']]
                    media[0]['caption'] = '📸 ' + draft['data']['name'] + ' | ' + job['id']
                    self.api.call('sendMediaGroup', chat_id=self.admin, media=media)
                    phase = 2
                    self.progress(job['id'], phase)
                if phase == 2:
                    loc = draft['data']['location']
                    if 'latitude' in loc:
                        self.api.call('sendLocation', chat_id=self.admin, **loc)
                    phase = 3
                    self.progress(job['id'], phase)
                self.say(job['user'], f'✅ Сўровнома ва расмлар Туризм бошқармасига етказилди.\nРақам: {job["id"]}', [[NEW]])
                with self.store.db:
                    self.store.db.execute('UPDATE jobs SET complete=1 WHERE id=?', (job['id'],))
            except APIError as exc:
                if phase == 3 and exc.code == 403:
                    # User blocked the bot, but all materials reached the administration.
                    with self.store.db:
                        self.store.db.execute('UPDATE jobs SET complete=1 WHERE id=?', (job['id'],))
                    continue
                delay = max(exc.retry_after, min(300, 5 * 2 ** min(job['attempts'], 6)))
                with self.store.db:
                    self.store.db.execute('UPDATE jobs SET attempts=attempts+1,retry_at=? WHERE id=?', (time.time()+delay, job['id']))
                logging.warning('Delivery pending: application %s, phase %s, code %s', job['id'], phase, exc.code)

    def progress(self, ident, phase):
        with self.store.db:
            self.store.db.execute('UPDATE jobs SET phase=? WHERE id=?', (phase, ident))


def setup():
    print('BotFather токенини киритинг (экранда кўринмайди):')
    token = getpass.getpass('BOT_TOKEN: ').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise SystemExit('Токен шакли нотўғри.')
    chat = input('Бошқарма гуруҳи ID рақами (ҳозирча билмасангиз Enter): ').strip()
    if chat and not re.fullmatch(r'-?\d+', chat):
        raise SystemExit('Гуруҳ ID рақами нотўғри.')
    path = ROOT / '.env'
    if path.exists():
        answer = input('Мавжуд .env созламаларини алмаштирасизми? yes / no: ')
        if answer.lower() != 'yes':
            return
    path.write_text(f'BOT_TOKEN={token}\nADMIN_CHAT_ID={chat}\n', encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass
    print('Созламалар сақланди. start.bat ёки python bot.py орқали ишга туширинг.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--setup', action='store_true')
    args = parser.parse_args()
    if args.setup:
        return setup()
    load_env()
    token = os.environ.get('BOT_TOKEN', '')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise SystemExit('Аввал setup.bat ёки python bot.py --setup орқали токенни киритинг.')
    admin = os.environ.get('ADMIN_CHAT_ID', '').strip()
    if admin and not re.fullmatch(r'-?\d+', admin):
        raise SystemExit('ADMIN_CHAT_ID рақам бўлиши керак.')
    data_dir = Path(os.environ.get('DATA_DIR', str(ROOT / 'data')))
    data_dir.mkdir(parents=True, exist_ok=True)
    api = Telegram(token)
    store = Store(data_dir / 'bot.sqlite3')
    bot = Bot(api, store, int(admin) if admin else None)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    try:
        me = api.call('getMe')
        if admin:
            api.call('getChat', chat_id=int(admin))
        # Long polling cannot coexist with a webhook. Keep pending messages.
        api.call('deleteWebhook', drop_pending_updates=False)
        api.call('setMyCommands', commands=[
            {'command': 'start', 'description': 'Сўровномани очиш'},
            {'command': 'new', 'description': 'Янги сўровнома'},
            {'command': 'cancel', 'description': 'Сўровномани бекор қилиш'},
            {'command': 'status', 'description': 'Юбориш ҳолати'},
            {'command': 'chatid', 'description': 'Чат ID рақами'}])
        logging.info('Bot running: @%s. Recipient %s.', me['username'], 'configured' if admin else 'not configured')
        while True:
            bot.flush()
            try:
                updates = api.call('getUpdates', offset=store.offset(), timeout=20, allowed_updates=['message'])
                for update in updates:
                    try:
                        bot.handle(update)
                    except APIError as exc:
                        # Saved state is preserved; /start can resume a draft if a prompt failed.
                        logging.warning('Reply failed, code %s. Saved state retained.', exc.code)
                    store.set_offset(update['update_id'] + 1)
            except APIError as exc:
                if exc.code in (401, 409):
                    raise SystemExit('Токенни текширинг ёки бошқа ишлаётган бот нусхасини тўхтатинг.') from None
                logging.warning('Connection error, code %s; retrying.', exc.code)
                time.sleep(max(5, min(30, exc.retry_after)))
    except APIError as exc:
        raise SystemExit(f'Telegram билан уланиш амалга ошмади (код {exc.code}). Токен, гуруҳ ID ва бот рухсатларини текширинг.') from None
    except KeyboardInterrupt:
        print('\nБот тўхтатилди. Маълумотлар сақланди.')
    finally:
        store.db.close()


if __name__ == '__main__':
    main()
