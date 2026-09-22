#!/usr/bin/env python3

# =============================================================================
# Uptime Kuma – Synology DSM 7
# =============================================================================
#
# Zweck:
# Dieses Script steuert die Uptime-Kuma-Monitore, die mit dem Tag
# "HOMEDOMENAS05" versehen sind.
#
#   pause  -> pausiert alle Monitore mit diesem Tag
#   resume -> aktiviert alle Monitore mit diesem Tag wieder
#
# Dadurch können bei einem geplanten Herunterfahren der Synology die
# erwarteten "DOWN"-Meldungen von Uptime Kuma verhindert werden. Nach dem
# Neustart werden die betreffenden Monitore automatisch wieder aktiviert.
#
# Die Auswahl erfolgt ausschließlich über den Tag "HOMEDOMENAS05".
# Neue Monitore müssen deshalb nicht im Script eingetragen werden: Es reicht,
# ihnen in Uptime Kuma diesen Tag zuzuweisen.
#
# -----------------------------------------------------------------------------
# Einrichtung auf Synology DSM 7
# -----------------------------------------------------------------------------
#
# 1. Script auf der Synology speichern, z. B. als:
#      /volume1/scripts/kuma-control.py
#
# 2. Ausführbar machen:
#      chmod +x /volume1/scripts/kuma-control.py
#
# 3. Manuell testen:
#      python3 /volume1/scripts/kuma-control.py pause
#      python3 /volume1/scripts/kuma-control.py resume
#
#    Nach dem Test sollten in Uptime Kuma alle Monitore mit dem Tag
#    "HOMEDOMENAS05" entsprechend pausiert bzw. wieder aktiviert sein.
#
# 4. In DSM unter:
#      Systemsteuerung -> Aufgabenplaner
#    eine Aufgabe als "Benutzerdefiniertes Skript" mit Benutzer "root"
#    erstellen.
#
#    Aufgabe für das Herunterfahren:
#      python3 /volume1/scripts/kuma-control.py pause
#    Ausführung: Beim Herunterfahren
#
#    Aufgabe für den Start:
#      python3 /volume1/scripts/kuma-control.py resume
#    Ausführung: Beim Hochfahren
#
# 5. Die beiden Aufgaben können zunächst manuell über den Aufgabenplaner
#    ausgeführt werden. Erst danach sollte man einen echten Neustart bzw.
#    ein Herunterfahren zum Test durchführen.
#
# Hinweis:
# Das Script verwendet die Zugangsdaten von Uptime Kuma aus den Einstellungen
# weiter unten. Das Repository sollte deshalb privat bleiben.
#
# =============================================================================

import sys
import json
import time
import socket
import base64
import os
import struct

KUMA_HOST = "10.0.10.20"
KUMA_PORT = 3001
KUMA_USER = "admin"
KUMA_PASS = "MeMy$e1f"
TAG_NAME = "HOMEDOMENAS05"

_ws_buffer = b""


def recv_exact(sock, length):
    global _ws_buffer
    data = b""

    if _ws_buffer:
        take = min(length, len(_ws_buffer))
        data = _ws_buffer[:take]
        _ws_buffer = _ws_buffer[take:]

    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            return None
        data += chunk

    return data


def ws_connect():
    global _ws_buffer

    key = base64.b64encode(os.urandom(16)).decode()
    sock = socket.create_connection((KUMA_HOST, KUMA_PORT), timeout=10)

    request = (
        f"GET /socket.io/?EIO=4&transport=websocket HTTP/1.1\r\n"
        f"Host: {KUMA_HOST}:{KUMA_PORT}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )

    sock.sendall(request.encode())

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError("WebSocket-Handshake fehlgeschlagen")
        response += chunk

    if b"101 Switching Protocols" not in response:
        raise RuntimeError("WebSocket-Handshake fehlgeschlagen")

    _, body = response.split(b"\r\n\r\n", 1)
    _ws_buffer = body

    return sock


def ws_send(sock, payload):
    data = payload.encode()
    length = len(data)
    mask = os.urandom(4)

    if length < 126:
        header = bytes([0x81, 0x80 | length])
    elif length < 65536:
        header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", length)
    else:
        header = bytes([0x81, 0x80 | 127]) + struct.pack("!Q", length)

    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(header + mask + masked)


def ws_recv(sock):
    first = recv_exact(sock, 2)

    if first is None:
        return None

    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F

    if length == 126:
        raw = recv_exact(sock, 2)
        if raw is None:
            return None
        length = struct.unpack("!H", raw)[0]
    elif length == 127:
        raw = recv_exact(sock, 8)
        if raw is None:
            return None
        length = struct.unpack("!Q", raw)[0]

    mask = recv_exact(sock, 4) if masked else None
    data = recv_exact(sock, length)

    if data is None:
        return None

    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    if opcode == 0x9:
        ws_send(sock, data.decode(errors="replace"))
        return ws_recv(sock)

    if opcode == 0x8:
        return None

    return data.decode(errors="replace")


def wait_for(sock, predicate, timeout=10):
    deadline = time.time() + timeout

    while time.time() < deadline:
        remaining = max(0.1, deadline - time.time())
        sock.settimeout(remaining)

        try:
            message = ws_recv(sock)
        except socket.timeout:
            continue

        if message is None:
            raise RuntimeError("Kuma hat die WebSocket-Verbindung geschlossen")

        if message == "2":
            ws_send(sock, "3")
            continue

        if predicate(message):
            return message

    raise RuntimeError("Timeout beim Warten auf Kuma")


def login_and_get_monitors():
    sock = ws_connect()

    try:
        packet = ws_recv(sock)

        if not packet or not packet.startswith("0"):
            raise RuntimeError("Engine.IO-Verbindung fehlgeschlagen")

        ws_send(sock, "40")
        wait_for(sock, lambda p: p.startswith("40"))

        login = [
            "login",
            {
                "username": KUMA_USER,
                "password": KUMA_PASS,
                "token": None
            }
        ]

        ws_send(sock, "421" + json.dumps(login, separators=(",", ":")))

        monitor_list = None
        login_ok = False
        deadline = time.time() + 10

        while time.time() < deadline:
            sock.settimeout(max(0.1, deadline - time.time()))

            try:
                message = ws_recv(sock)
            except socket.timeout:
                continue

            if message is None:
                raise RuntimeError("Kuma hat die Verbindung geschlossen")

            if message == "2":
                ws_send(sock, "3")
                continue

            if message.startswith("43"):
                try:
                    ack = json.loads(message[3:])
                    if ack and isinstance(ack[0], dict) and ack[0].get("ok"):
                        login_ok = True
                except Exception:
                    pass

            elif message.startswith("42"):
                try:
                    event = json.loads(message[2:])
                    if event and event[0] == "monitorList":
                        monitor_list = event[1]
                except Exception:
                    pass

            if login_ok and monitor_list is not None:
                return sock, monitor_list

        raise RuntimeError("Login oder monitorList von Kuma nicht erhalten")

    except Exception:
        sock.close()
        raise


def tagged_monitors(monitor_list):
    monitors = []

    for monitor_id, monitor in monitor_list.items():
        for tag in monitor.get("tags", []):
            if tag.get("name") == TAG_NAME:
                monitors.append({
                    "id": int(monitor_id),
                    "name": monitor.get("name", "")
                })
                break

    return monitors


def change_monitor(sock, monitor_id, action, ack_id):
    command = [action, monitor_id]

    ws_send(
        sock,
        f"42{ack_id}" + json.dumps(command, separators=(",", ":"))
    )

    def matching_ack(message):
        return message.startswith("43" + str(ack_id))

    response = wait_for(sock, matching_ack)

    try:
        payload = json.loads(response[2 + len(str(ack_id)):])
        return bool(
            payload
            and isinstance(payload[0], dict)
            and payload[0].get("ok")
        )
    except Exception:
        return False


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("pause", "resume"):
        print("Verwendung: kuma-control.py pause|resume")
        sys.exit(1)

    action = sys.argv[1]

    print(f"Verbinde mit Uptime Kuma {KUMA_HOST}:{KUMA_PORT} ...")

    sock, monitor_list = login_and_get_monitors()

    try:
        monitors = tagged_monitors(monitor_list)

        print(f"Tag: {TAG_NAME}")
        print(f"Gefundene Monitore: {len(monitors)}")

        if not monitors:
            print("Keine passenden Monitore gefunden.")
            return

        kuma_action = "pauseMonitor" if action == "pause" else "resumeMonitor"
        ack_id = 2

        for monitor in monitors:
            print(f"{action}: #{monitor['id']} - {monitor['name']}")

            if change_monitor(sock, monitor["id"], kuma_action, ack_id):
                print("  OK")
            else:
                print("  FEHLER")
                sys.exit(1)

            ack_id += 1

        print("Fertig.")

    finally:
        sock.close()


if __name__ == "__main__":
    main()
