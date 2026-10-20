"""Consulta vuelos baratos desde tus aeropuertos, guarda deals.json y avisa por Telegram.

Uso:
  python fetch_deals.py          # usa la API real (necesita TP_TOKEN)
  python fetch_deals.py --demo   # datos falsos para probar la web sin token
"""
import json, os, sys, random
from datetime import date, datetime
import urllib.request, urllib.parse

CFG = json.load(open("config.json", encoding="utf-8"))
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BASE = "https://www.aviasales.com"


def months(n):
    y, m = date.today().year, date.today().month
    for i in range(n):
        yield f"{y + (m - 1 + i) // 12}-{(m - 1 + i) % 12 + 1:02d}"


def parse(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).date()


def fetch_real(origin):
    token = os.environ["TP_TOKEN"]
    out = []
    for month in months(CFG["months_ahead"]):
        q = urllib.parse.urlencode({
            "origin": origin, "departure_at": month, "one_way": "false",
            "unique": "false", "sorting": "price", "limit": 200,
            "currency": CFG["currency"], "token": token,
        })
        with urllib.request.urlopen(f"{API}?{q}", timeout=30) as r:
            data = json.load(r).get("data", [])
        for d in data:
            if not d.get("return_at"):
                continue
            days = (parse(d["return_at"]) - parse(d["departure_at"])).days
            out.append({
                "origin": origin, "destination": d["destination"],
                "price": d["price"], "airline": d.get("airline", ""),
                "departure": d["departure_at"][:10], "return": d["return_at"][:10],
                "days": days, "transfers": d.get("transfers", 0),
                "link": BASE + d.get("link", ""),
            })
    return out


def fetch_demo(origin):
    out = []
    for dest in ["LON", "BCN", "ROM", "PAR", "DUB", "BER", "LIS", "AMS"]:
        for _ in range(3):
            days = random.randint(2, 12)
            dep = date.fromordinal(date.today().toordinal() + random.randint(10, 80))
            out.append({
                "origin": origin, "destination": dest,
                "price": random.randint(19, 140), "airline": random.choice(["FR", "VY", "U2", "W6"]),
                "departure": dep.isoformat(),
                "return": date.fromordinal(dep.toordinal() + days).isoformat(),
                "days": days, "transfers": 0, "link": BASE,
            })
    return out


def telegram(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("(sin Telegram configurado)\n" + text)
        return
    data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
    urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30)


def main():
    demo = "--demo" in sys.argv
    fetch = fetch_demo if demo else fetch_real
    deals = []
    for ap in CFG["airports"]:
        try:
            deals += fetch(ap)
        except Exception as e:
            print(f"Error con {ap}: {e}")
    deals.sort(key=lambda d: d["price"])
    json.dump({"updated": datetime.utcnow().isoformat() + "Z", "currency": CFG["currency"], "deals": deals},
              open("deals.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # Alertas: solo ofertas nuevas que cumplan tus condiciones
    try:
        seen = set(json.load(open("seen.json")))
    except Exception:
        seen = set()
    new = []
    for d in deals:
        key = f'{d["origin"]}-{d["destination"]}-{d["departure"]}-{d["return"]}-{d["price"]}'
        ok = (d["origin"] in CFG["alert_airports"] and d["price"] <= CFG["max_price"]
              and CFG["min_days"] <= d["days"] <= CFG["max_days"])
        if ok and key not in seen:
            new.append(d)
            seen.add(key)
    if new and not demo:
        lines = [f'✈️ {d["origin"]}→{d["destination"]} {d["price"]}€ · {d["days"]} días · '
                 f'{d["departure"]} / {d["return"]}\n{d["link"]}' for d in new[:10]]
        telegram("Vuelos baratos nuevos:\n\n" + "\n\n".join(lines))
        json.dump(sorted(seen), open("seen.json", "w"))
    print(f"{len(deals)} ofertas, {len(new)} nuevas para alerta")


if __name__ == "__main__":
    main()
