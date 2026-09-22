import socket
import base64
import os
import json
import getpass
import struct

HOST = "10.0.10.20"
PORT = 3001
MONITOR_ID = 7


def ws_connect():
    key = base64.b64encode(os.urandom(16)).decode()
    s = socket.create_connection((HOST, PORT), timeout=10)
    req = (
        f"GET /socket.io/?EIO=4&transport=websocket HTTP/1.1\r\n"
        f"Host: {HOST}:{PORT}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(req.encode())
    response = b""
    while b"\r\n\r\n" not in response:
        response += s.recv(4096)
    if b"101 Switching Protocols" not in response:
        raise RuntimeError("WebSocket-Verbindung fehlgeschlagen")
    return s


def ws_send(s, payload):
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
    s.sendall(header + mask + masked)


def ws_recv(s):
    first = s.recv(2)
    if len(first) < 2:
        return None
    opcode = first[0] & 0x0f
    length = first[1] & 0x7f
    if length == 126:
        length = struct.unpack("!H", s.recv(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", s.recv(8))[0]
    data = b""
    while len(data) < length:
        chunk = s.recv(length - len(data))
        if not chunk:
            return None
        data += chunk
    if opcode == 0x8:
        return None
    return data.decode(errors="replace")


print("Verbinde mit Uptime Kuma...")
s = ws_connect()
ws_send(s, "40")

while True:
    msg = ws_recv(s)
    if msg is None:
        raise RuntimeError("Kuma hat die Verbindung geschlossen")
    print("Kuma:", msg)
    if msg == "2":
        ws_send(s, "3")
        continue
    if msg.startswith("42"):
        try:
            packet = json.loads(msg[2:])
        except Exception:
            continue
        if packet[0] == "loginRequired":
            break

print()
username = input("Kuma Benutzer: ")
password = getpass.getpass("Kuma Passwort: ")

login = ["login", {
    "username": username,
    "password": password,
    "token": None
}]
ws_send(s, "42" + json.dumps(login, separators=(",", ":")))

while True:
    msg = ws_recv(s)
    if msg is None:
        raise RuntimeError("Kuma hat die Verbindung geschlossen")
    print("Kuma:", msg)
    if msg == "2":
        ws_send(s, "3")
        continue
    if msg.startswith("42"):
        try:
            packet = json.loads(msg[2:])
        except Exception:
            continue
        if packet[0] == "login":
            print("Login-Antwort:", packet)
            if len(packet) > 1 and isinstance(packet[1], dict) and packet[1].get("ok"):
                print("Login erfolgreich.")
                print(f"Pausiere Monitor #{MONITOR_ID} ...")
                ws_send(s, "42" + json.dumps(
                    ["pauseMonitor", MONITOR_ID],
                    separators=(",", ":")
                ))
                break
            print("Login fehlgeschlagen.")
            break

s.close()
