
"""Consulta vuelos baratos, guarda deals.json y avisa por Telegram.

Uso:
  python fetch_deals.py
  python fetch_deals.py --demo
"""

import json
import os
import sys
import random
import urllib.request
import urllib.parse
import urllib.error

from datetime import date, datetime, timezone

CFG = json.load(open("config.json", encoding="utf-8"))

API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
BASE = "https://www.aviasales.com"


def months(n):
    y, m = date.today().year, date.today().month
    for i in range(n):
        yield f"{y + (m - 1 + i) // 12}-{(m - 1 + i) % 12 + 1:02d}"


def parse(s):
    return datetime.fromisoformat(
        s.replace("Z", "+00:00")
    ).date()


def fetch_real(origin):
    token = os.environ["TP_TOKEN"]
    out = []

    for month in months(CFG["months_ahead"]):
        q = urllib.parse.urlencode({
            "origin": origin,
            "departure_at": month,
            "one_way": "false",
            "unique": "false",
            "sorting": "price",
            "limit": 200,
            "currency": CFG["currency"],
            "token": token,
        })

        with urllib.request.urlopen(
            f"{API}?{q}", timeout=30
        ) as r:
            data = json.load(r).get("data", [])

        for d in data:
            if not d.get("return_at"):
                continue

            days = (
                parse(d["return_at"]) -
                parse(d["departure_at"])
            ).days

            out.append({
                "origin": origin,
                "destination": d["destination"],
                "price": d["price"],
                "airline": d.get("airline", ""),
                "departure": d["departure_at"][:10],
                "return": d["return_at"][:10],
                "days": days,
                "transfers": d.get("transfers", 0),
                "link": BASE + d.get("link", ""),
            })

    return out


def fetch_demo(origin):
    out = []

    for dest in [
        "LON", "BCN", "ROM", "PAR",
        "DUB", "BER", "LIS", "AMS"
    ]:
        for _ in range(3):
            days = random.randint(2, 12)

            dep = date.fromordinal(
                date.today().toordinal() +
                random.randint(10, 80)
            )

            out.append({
                "origin": origin,
                "destination": dest,
                "price": random.randint(19, 140),
                "airline": random.choice(
                    ["FR", "VY", "U2", "W6"]
                ),
                "departure": dep.isoformat(),
                "return": date.fromordinal(
                    dep.toordinal() + days
                ).isoformat(),
                "days": days,
                "transfers": 0,
                "link": BASE,
            })

    return out


def telegram(msg):
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not tok:
        raise RuntimeError(
            "Falta TELEGRAM_BOT_TOKEN en GitHub Secrets"
        )

    if not chat_id:
        raise RuntimeError(
            "Falta TELEGRAM_CHAT_ID en GitHub Secrets"
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{tok}/sendMessage"
    )

    # Telegram permite hasta 4096 caracteres.
    # Usamos un margen de seguridad.
    for i in range(0, len(msg), 3500):
        parte = msg[i:i + 3500]

        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": parte,
            "disable_web_page_preview": "true",
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            method="POST"
        )

        try:
            with urllib.request.urlopen(
                req, timeout=30
            ) as response:
                resultado = json.load(response)

            if not resultado.get("ok"):
                raise RuntimeError(
                    "Telegram no confirmó el envío"
                )

            print(
                "Mensaje enviado correctamente a Telegram"
            )

        except urllib.error.HTTPError as e:
            detalle = e.read().decode(
                "utf-8", errors="replace"
            )

            print(
                f"Error de Telegram: HTTP {e.code}"
            )
            print(f"Detalle: {detalle}")

            raise RuntimeError(
                "Telegram rechazó el mensaje. "
                "Revisa el detalle anterior."
            ) from None


def main():
    demo = "--demo" in sys.argv
    fetch = fetch_demo if demo else fetch_real

    deals = []

    for ap in CFG["airports"]:
        try:
            ofertas = fetch(ap)
            deals.extend(ofertas)
            print(
                f"Aeropuerto {ap}: "
                f"{len(ofertas)} ofertas encontradas"
            )

        except Exception as e:
            print(f"Error con {ap}: {e}")

    deals.sort(key=lambda d: d["price"])

    # Guardar ofertas con fecha UTC actual
    updated = datetime.now(
        timezone.utc
    ).isoformat().replace("+00:00", "Z")

    with open(
        "deals.json", "w", encoding="utf-8"
    ) as f:
        json.dump({
            "updated": updated,
            "currency": CFG["currency"],
            "deals": deals,
        }, f, ensure_ascii=False, indent=2)

    # Leer ofertas notificadas anteriormente
    try:
        with open(
            "seen.json", encoding="utf-8"
        ) as f:
            seen = set(json.load(f))
    except (FileNotFoundError, ValueError):
        seen = set()

    new = []

    for d in deals:
        key = (
            f'{d["origin"]}-'
            f'{d["destination"]}-'
            f'{d["departure"]}-'
            f'{d["return"]}-'
            f'{d["price"]}'
        )

        ok = (
            d["origin"] in CFG["alert_airports"]
            and d["price"] <= CFG["max_price"]
            and CFG["min_days"] <= d["days"]
            <= CFG["max_days"]
        )

        if ok and key not in seen:
            new.append((key, d))

    # Enviar solo ofertas nuevas
    if new and not demo:
        seleccionadas = new[:10]

        lines = [
            (
                f'✈️ {d["origin"]} → '
                f'{d["destination"]}\n'
                f'💰 {d["price"]} '
                f'{CFG["currency"]}\n'
                f'📅 {d["departure"]} / '
                f'{d["return"]}\n'
                f'🌙 {d["days"]} días\n'
                f'🔗 {d["link"]}'
            )
            for key, d in seleccionadas
        ]

        mensaje = (
            "✈️ VUELOS BARATOS NUEVOS\n\n"
            + "\n\n".join(lines)
        )

        telegram(mensaje)

        # Marcar solo las ofertas enviadas.
        for key, d in seleccionadas:
            seen.add(key)

        with open("seen.json", "w", encoding="utf-8") as f:
            json.dump(
                sorted(seen),
                f,
                ensure_ascii=False,
                indent=2
            )

    print(
        f"{len(deals)} ofertas encontradas, "
        f"{len(new)} nuevas para alerta"
    )


if __name__ == "__main__":
    main()
