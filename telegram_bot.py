"""Bot Telegram por sondeo periódico desde GitHub Actions.
No usar a la vez con un webhook u otro consumidor de getUpdates.
"""
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

STATE_PATH = Path('bot_state.json')
CONFIG = json.loads(Path('config.json').read_text(encoding='utf-8'))
TOKEN = os.environ['TELEGRAM_BOT_TOKEN'].strip()
CHAT_ID = str(os.environ['TELEGRAM_CHAT_ID']).strip()
TP_TOKEN = os.environ['TP_TOKEN'].strip()
API = f'https://api.telegram.org/bot{TOKEN}/'
FLIGHTS_API = 'https://api.travelpayouts.com/aviasales/v3/prices_for_dates'


def load_state():
    try:
        data = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Invalid state')
        return data
    except FileNotFoundError:
        return {'offset': 0, 'alerts': [], 'session': {}, 'last_check': 0}


def save_state():
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def request_json(url, params=None, post=False):
    encoded = urllib.parse.urlencode(params or {}).encode('utf-8')
    if post:
        req = urllib.request.Request(url, data=encoded, method='POST')
    else:
        req = urllib.request.Request(url + ('?' + encoded.decode() if params else ''))
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        # Do not print the URL: it contains the bot token.
        raise RuntimeError(f'HTTP {exc.code}: {body}') from None


def tg(method, params):
    result = request_json(API + method, params, post=True)
    if not result.get('ok'):
        raise RuntimeError(f'Telegram {method}: {result.get("description", "unknown error")}')
    return result['result']


def send(text, keyboard=None):
    args = {'chat_id': CHAT_ID, 'text': text[:4000], 'disable_web_page_preview': 'true'}
    if keyboard:
        args['reply_markup'] = json.dumps({'inline_keyboard': keyboard})
    tg('sendMessage', args)


def buttons(items):
    return [[{'text': label, 'callback_data': code} for label, code in items]]


def menu():
    send('✈️ ¿Qué quieres hacer?', [
        [{'text': '🔎 Buscar vuelos', 'callback_data': 'start:search'},
         {'text': '🔔 Crear alerta', 'callback_data': 'start:alert'}],
        [{'text': '📋 Mis alertas', 'callback_data': 'list'},
         {'text': '❓ Ayuda', 'callback_data': 'help'}]
    ])


def ask():
    s = state['session']
    step = s.get('step')
    if step == 'origin':
        send('¿Desde dónde sales?', buttons([('Alicante', 'origin:ALC'), ('Murcia', 'origin:RMU')]))
    elif step == 'destination':
        send('¿A qué destino? Escribe su código de 3 letras (por ejemplo ROM, PAR, LON, BCN).')
    elif step == 'departure':
        send('¿Qué día sales? Escribe la fecha AAAA-MM-DD (por ejemplo 2026-12-05).')
    elif step == 'return':
        send('¿Qué día vuelves? Escribe la fecha AAAA-MM-DD.')
    elif step == 'price':
        send('¿Cuál es el precio máximo de ida y vuelta en euros? Escribe un número, por ejemplo 50.')


def start(mode):
    state['session'] = {'mode': mode, 'step': 'origin'}
    ask()
