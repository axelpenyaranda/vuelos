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


def valid_date(value):
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def consume_text(text):
    s = state.get('session', {})
    step = s.get('step')
    if not step:
        send('Pulsa un botón para empezar o escribe /menu.')
        return
    value = text.strip().upper()
    if step == 'origin':
        if value not in ('ALC', 'RMU'):
            ask()
            return
        s['origin'] = value
        s['step'] = 'destination'
    elif step == 'destination':
        if not re.fullmatch(r'[A-Z]{3}', value):
            send('Escribe un código de 3 letras, como ROM o PAR.')
            return
        s['destination'] = value
        s['step'] = 'departure'
    elif step == 'departure':
        d = valid_date(text.strip())
        if not d or d < date.today():
            send('Introduce una fecha futura válida en formato AAAA-MM-DD.')
            return
        s['departure'] = d.isoformat()
        s['step'] = 'return'
    elif step == 'return':
        d = valid_date(text.strip())
        if not d or d < date.fromisoformat(s['departure']):
            send('La vuelta debe ser igual o posterior a la ida. Usa AAAA-MM-DD.')
            return
        s['return'] = d.isoformat()
        s['step'] = 'price'
    elif step == 'price':
        try:
            price = float(text.strip().replace(',', '.'))
            if not 0 < price <= 100000:
                raise ValueError()
        except ValueError:
            send('Escribe un precio positivo, por ejemplo 50.')
            return
        s['max_price'] = price
        completed = {k: s[k] for k in ('origin', 'destination', 'departure', 'return', 'max_price')}
        mode = s['mode']
        state['session'] = {}
        if mode == 'alert':
            if completed not in state['alerts']:
                state['alerts'].append(completed)
            send('🔔 Alerta guardada. Revisaré las ofertas periódicamente.\n' + format_search(completed))
        else:
            search_and_reply(completed)
        menu()
        return
    ask()


def format_search(a):
    return (f'{a["origin"]} → {a["destination"]}\n'
            f'Ida {a["departure"]} · vuelta {a["return"]}\n'
            f'Máximo {a["max_price"]:g} €')


def query_flights(a):
    # Travelpayouts returns cached offers, not a live exhaustive flight search.
    q = {'origin': a['origin'], 'destination': a['destination'],
         'departure_at': a['departure'][:7], 'one_way': 'false',
         'sorting': 'price', 'unique': 'false', 'limit': 1000,
         'currency': CONFIG.get('currency', 'eur'), 'token': TP_TOKEN}
    response = request_json(FLIGHTS_API, q)
    if not response.get('success', True):
        raise RuntimeError('Travelpayouts returned an unsuccessful response')
    results = []
    for d in response.get('data', []):
        if (str(d.get('departure_at', ''))[:10] != a['departure'] or
                str(d.get('return_at', ''))[:10] != a['return'] or
                d.get('destination') != a['destination'] or
                d.get('price') is None or float(d['price']) > a['max_price']):
            continue
        link = d.get('link', '')
        if link.startswith('/'):
            link = 'https://www.aviasales.com' + link
        elif not link.startswith('https://'):
            link = 'https://www.aviasales.com'
        results.append({'price': d['price'], 'link': link, 'airline': d.get('airline', '')})
    return sorted(results, key=lambda x: x['price'])[:5]


def search_and_reply(a):
    send('🔎 Consultando ofertas almacenadas para:\n' + format_search(a))
    try:
        offers = query_flights(a)
        if not offers:
            send('No hay ofertas registradas que coincidan exactamente. Esto no significa que no existan vuelos.')
        else:
            for d in offers:
                send(f'✈️ {a["origin"]} → {a["destination"]}\n'
                     f'📅 {a["departure"]} / {a["return"]}\n'
                     f'💰 {d["price"]} €\n🔗 {d["link"]}')
    except Exception as exc:
        print('Flight lookup failed:', type(exc).__name__, str(exc)[:300])
        send('No he podido consultar Travelpayouts ahora. Inténtalo más tarde.')


def list_alerts():
    if not state['alerts']:
        send('No tienes alertas específicas. Pulsa 🔔 Crear alerta.', buttons([('🔔 Crear alerta', 'start:alert')]))
        return
    for i, a in enumerate(state['alerts']):
        send(f'🔔 Alerta {i + 1}\n{format_search(a)}',
             buttons([(f'🗑️ Eliminar alerta {i + 1}', f'del:{i}')]))


def handle_callback(query):
    tg('answerCallbackQuery', {'callback_query_id': query['id']})
    action = query.get('data', '')
    if action.startswith('start:'):
        start('alert' if action == 'start:alert' else 'search')
    elif action.startswith('origin:'):
        if state.get('session', {}).get('step') == 'origin':
            consume_text(action.split(':', 1)[1])
        else:
            menu()
    elif action == 'list':
        list_alerts()
    elif action == 'help':
        send('Usa los botones de /menu. Para cancelar una búsqueda escribe /cancelar. '
             'Las fechas son exactas y las ofertas proceden de datos almacenados de Travelpayouts.')
    elif action.startswith('del:'):
        try:
            idx = int(action.split(':', 1)[1])
            if 0 <= idx < len(state['alerts']):
                state['alerts'].pop(idx)
                send('🗑️ Alerta eliminada.')
            else:
                send('La alerta ya no existe.')
        except ValueError:
            pass


def process_updates():
    updates = tg('getUpdates', {'offset': state.get('offset', 0), 'timeout': 0, 'limit': 100})
    for u in updates:
        try:
            msg = u.get('message')
            cb = u.get('callback_query')
            if msg and str(msg.get('chat', {}).get('id')) == CHAT_ID:
                text = msg.get('text', '').strip()
                if text.lower().split('@')[0] in ('/start', '/menu'):
                    state['session'] = {}
                    menu()
                elif text.lower() == '/cancelar':
                    state['session'] = {}
                    send('Operación cancelada.')
                    menu()
                elif text:
                    consume_text(text)
            elif cb and str(cb.get('message', {}).get('chat', {}).get('id')) == CHAT_ID:
                handle_callback(cb)
        except Exception as exc:
            print('Update processing error:', type(exc).__name__, str(exc)[:250])
        finally:
            state['offset'] = u['update_id'] + 1
            save_state()


def check_alerts():
    now = time.time()
    if now - state.get('last_check', 0) < 6 * 3600:
        return
    # Only mark the check complete when every query succeeds.
    found = []
    for a in state['alerts']:
        for offer in query_flights(a):
            key = '|'.join(map(str, [a['origin'], a['destination'], a['departure'],
                                     a['return'], offer['price']]))
            found.append((key, a, offer))
    seen = set(state.get('seen', []))
    for key, a, offer in found:
        if key not in seen:
            send(f'🔔 ¡Oferta para tu alerta!\n{format_search(a)}\n'
                 f'💰 Encontrado: {offer["price"]} €\n🔗 {offer["link"]}')
            seen.add(key)
            state['seen'] = sorted(seen)
            save_state()
    state['last_check'] = now
    save_state()


state = load_state()
state.setdefault('alerts', [])
state.setdefault('session', {})
state.setdefault('offset', 0)
state.setdefault('last_check', 0)
state.setdefault('seen', [])

if __name__ == '__main__':
    process_updates()
    try:
        check_alerts()
    except Exception as exc:
        print('Alert check failed:', type(exc).__name__, str(exc)[:300])
        # Keep previous last_check so the next scheduled run retries.
    save_state()
