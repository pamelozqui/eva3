"""
Atrapanieblas · API de la Unidad 3 (HTTPS) sobre AWS.

  ESP32 (Wokwi) --MQTT/TLS--> AWS IoT Core --Regla--> DynamoDB (tabla de telemetría)
  App Android --HTTPS--> este servidor --boto3--> DynamoDB (lee)  /  IoT Core (publica órdenes)

Qué hace:
  - Registro / login (hash de contraseña, bloqueo tras 5 intentos, tokens de sesión con expiración).
  - Entrega a la app las lecturas y el estado del riego guardados en DynamoDB.
  - Publica en IoT Core las órdenes ON/OFF firmadas con HMAC-SHA256 (el ESP32 verifica la firma).
  - Verifica la integridad (HMAC) de cada lectura guardada y avisa a la app.
"""
import hashlib, hmac, os, secrets, threading, time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash

load_dotenv()

REGION = os.environ.get("AWS_REGION", "us-east-1")
BASE = "atrapanieblas/meneses-quintoman"
T_DATOS, T_CMD, T_ESTADO = f"{BASE}/captador/datos", f"{BASE}/riego/cmd", f"{BASE}/riego/estado"
TBL_USUARIOS = os.environ.get("TBL_USUARIOS", "atrapa_usuarios")
TBL_SESIONES = os.environ.get("TBL_SESIONES", "atrapa_sesiones")
TBL_TELEMETRIA = os.environ.get("TBL_TELEMETRIA", "atrapa_telemetria")
SESION_HORAS = 8
EN_LINEA_SEG = 60          # si la última lectura es más antigua, el nodo se considera fuera de línea

app = Flask(__name__)
fallos = {}                # usuario -> (intentos, bloqueado_hasta)
_contador = 0
_lock = threading.Lock()


# ---------- utilidades ----------
def secreto() -> bytes:
    return os.environ["HMAC_SECRET"].encode()


def firmar(mensaje: str) -> str:
    return hmac.new(secreto(), mensaje.encode(), hashlib.sha256).hexdigest()


def firma_lectura(seq, t, humedad, nivel, luz) -> str:
    # Mismo formato que firmar_lectura() del firmware: humedad en décimas (entero) para evitar problemas de decimales.
    return firmar(f"{int(seq)}|{int(t)}|{round(float(humedad) * 10)}|{int(nivel)}|{1 if luz else 0}")


def siguiente_n() -> int:
    global _contador
    with _lock:
        _contador += 1
        return _contador


def tabla(nombre):
    return boto3.resource("dynamodb", region_name=REGION).Table(nombre)


def iot_data():
    return boto3.client("iot-data", region_name=REGION, endpoint_url="https://" + os.environ["IOT_ENDPOINT"])


def publicar(topic: str, payload: str):
    iot_data().publish(topic=topic, qos=1, payload=payload.encode())


def num(x):
    return int(x) if isinstance(x, Decimal) and x == x.to_integral_value() else float(x)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()   # en la BD solo se guarda el hash del token


def usuario_de_token():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    r = tabla(TBL_SESIONES).get_item(Key={"token": hash_token(auth[7:])}).get("Item")
    if not r or int(r["expira"]) < time.time():
        return None
    return r["usuario"]


def no_autorizado():
    return jsonify(error="No autorizado"), 401


@app.errorhandler(ClientError)
@app.errorhandler(BotoCoreError)
def error_aws(e):
    app.logger.error("AWS: %s", e)
    return jsonify(error="Servicio AWS no disponible (¿credenciales del Learner Lab vencidas?)"), 502


# ---------- autenticación ----------
@app.get("/api/salud")
def salud():
    return jsonify(ok=True, ts=int(time.time() * 1000))


@app.post("/api/registro")
def registro():
    d = request.get_json(silent=True) or {}
    u, p = str(d.get("usuario", "")).strip().lower(), str(d.get("password", ""))
    if len(u) < 3 or len(u) > 50 or len(p) < 8 or not any(x.isalpha() for x in p) or not any(x.isdigit() for x in p):
        return jsonify(error="Datos inválidos (contraseña: 8+ con letras y números)"), 400
    try:
        tabla(TBL_USUARIOS).put_item(
            Item={"usuario": u, "hash": generate_password_hash(p), "creado": int(time.time())},
            ConditionExpression="attribute_not_exists(usuario)")
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return jsonify(error="Ese usuario ya existe"), 409
        raise
    return jsonify(ok=True), 201


@app.post("/api/login")
def login():
    d = request.get_json(silent=True) or {}
    u, p = str(d.get("usuario", "")).strip().lower(), str(d.get("password", ""))
    intentos, hasta = fallos.get(u, (0, 0))
    if time.time() < hasta:
        return jsonify(error="Cuenta bloqueada temporalmente"), 429
    r = tabla(TBL_USUARIOS).get_item(Key={"usuario": u}).get("Item")
    if not r or not check_password_hash(r["hash"], p):
        intentos += 1
        fallos[u] = (intentos, time.time() + 300 if intentos >= 5 else 0)
        return jsonify(error="Credenciales incorrectas"), 401
    fallos.pop(u, None)
    token = secrets.token_hex(32)
    tabla(TBL_SESIONES).put_item(Item={
        "token": hash_token(token), "usuario": u,
        "expira": int(time.time()) + SESION_HORAS * 3600})     # atributo TTL de la tabla
    return jsonify(token=token)


# ---------- datos (DynamoDB) ----------
def _lectura(it):
    try:
        integra = hmac.compare_digest(
            str(it.get("firma", "")), firma_lectura(it["seq"], it["t"], it["humedad"], it["nivel"], it.get("luz", False)))
    except Exception:
        integra = False
    return {"ts": int(it["ts"]), "humedad": float(it["humedad"]), "nivel": int(it["nivel"]),
            "luz": bool(it.get("luz", False)), "integra": integra}


def _ultimo(device):
    r = tabla(TBL_TELEMETRIA).query(KeyConditionExpression=Key("device").eq(device),
                                    ScanIndexForward=False, Limit=1)["Items"]
    return r[0] if r else None


@app.get("/api/lecturas")
def lecturas():
    if not usuario_de_token():
        return no_autorizado()
    limit = max(1, min(int(request.args.get("limit", 100)), 500))
    items = tabla(TBL_TELEMETRIA).query(KeyConditionExpression=Key("device").eq("nodo-a"),
                                        ScanIndexForward=False, Limit=limit)["Items"]
    lista = [_lectura(i) for i in items]
    prom = sum(l["humedad"] for l in lista) / len(lista) if lista else 0
    return jsonify(lecturas=lista, promedio_humedad=prom)


@app.get("/api/estado")
def estado():
    """Una sola consulta para la pantalla principal: última lectura + estado del riego."""
    if not usuario_de_token():
        return no_autorizado()
    lec, est = _ultimo("nodo-a"), _ultimo("nodo-b")
    ahora = int(time.time() * 1000)
    lectura = _lectura(lec) if lec else None
    return jsonify(
        servidor_ts=ahora,
        lectura=lectura,
        nodo_en_linea=bool(lectura and ahora - lectura["ts"] <= EN_LINEA_SEG * 1000),
        riego=est["riego"] if est else None,
        modo=est.get("modo") if est else None,
        riego_ts=int(est["ts"]) if est else None)


# ---------- órdenes hacia el ESP32 (vía AWS IoT Core) ----------
@app.post("/api/riego")
def riego():
    if not usuario_de_token():
        return no_autorizado()
    accion = str((request.get_json(silent=True) or {}).get("accion", "")).upper()
    if accion not in ("ON", "OFF"):
        return jsonify(error="Acción inválida"), 400
    t, n = int(time.time()), siguiente_n()
    publicar(T_CMD, '{"accion":"%s","t":%d,"n":%d,"firma":"%s"}' % (accion, t, n, firmar(f"{accion}|{t}|{n}")))
    return jsonify(ok=True)


@app.post("/api/simular")
def simular():
    """Publica una lectura simulada (útil para la demo cuando Wokwi no está corriendo)."""
    if not usuario_de_token():
        return no_autorizado()
    d = request.get_json(silent=True) or {}
    try:
        hum, nivel = round(float(d["humedad"]), 1), int(d["nivel"])
        luz = bool(d.get("luz", True))
        assert 0 <= hum <= 100 and 0 <= nivel <= 100
    except Exception:
        return jsonify(error="Datos inválidos"), 400
    t, seq = int(time.time()), 1_000_000 + siguiente_n()      # seq alto: no choca con el del ESP32
    publicar(T_DATOS, '{"t":%d,"seq":%d,"humedad":%.1f,"nivel":%d,"luz":%s,"firma":"%s"}' % (
        t, seq, hum, nivel, "true" if luz else "false", firma_lectura(seq, t, hum, nivel, luz)))
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, ssl_context=(os.environ["TLS_CERT"], os.environ["TLS_KEY"]))
