# Certificado HTTPS de la API (sin bash/openssl). Uso:  python gen_certs.py 10.0.2.2 192.168.1.50
# 10.0.2.2 = el PC visto desde el emulador; añade la IP WiFi del PC si usas celular real.
import sys, os, ipaddress, datetime
from cryptography import x509
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

if len(sys.argv) < 2:
    sys.exit("Uso: python gen_certs.py <IP> [<IP> ...]")
ips = [x509.IPAddress(ipaddress.ip_address(a)) for a in sys.argv[1:]]
os.makedirs("certs", exist_ok=True)
ahora = datetime.datetime.now(datetime.timezone.utc)
dias = datetime.timedelta(days=825)

def nombre(cn): return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
def pem_key(k): return k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())

ca_key = rsa.generate_private_key(65537, 4096)
ca = (x509.CertificateBuilder().subject_name(nombre("Atrapanieblas-CA")).issuer_name(nombre("Atrapanieblas-CA"))
      .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
      .not_valid_before(ahora - datetime.timedelta(minutes=5)).not_valid_after(ahora + dias)
      .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
      .sign(ca_key, hashes.SHA256()))
srv_key = rsa.generate_private_key(65537, 2048)
srv = (x509.CertificateBuilder().subject_name(nombre("atrapanieblas-api")).issuer_name(ca.subject)
       .public_key(srv_key.public_key()).serial_number(x509.random_serial_number())
       .not_valid_before(ahora - datetime.timedelta(minutes=5)).not_valid_after(ahora + dias)
       .add_extension(x509.SubjectAlternativeName(ips), critical=False)
       .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
       .sign(ca_key, hashes.SHA256()))
w = lambda f, b: open(os.path.join("certs", f), "wb").write(b)
w("ca.crt", ca.public_bytes(serialization.Encoding.PEM)); w("ca.key", pem_key(ca_key))
w("server.crt", srv.public_bytes(serialization.Encoding.PEM)); w("server.key", pem_key(srv_key))
print("Listo. Copia certs/ca.crt a la app: app/src/main/res/raw/ca.crt")
