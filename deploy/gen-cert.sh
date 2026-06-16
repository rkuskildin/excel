#!/usr/bin/env bash
# Самоподписанный TLS-сертификат для веб-морды. Использование:
#   ./gen-cert.sh                 # CN=localhost
#   ./gen-cert.sh 89.22.228.49    # CN/IP сервера
#   ./gen-cert.sh example.com     # CN/DNS домена
set -euo pipefail
cd "$(dirname "$0")"
HOST="${1:-localhost}"
mkdir -p tls

# SAN: IP — если аргумент похож на IPv4, иначе DNS
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  SAN="IP:$HOST"
else
  SAN="DNS:$HOST"
fi

openssl req -x509 -newkey rsa:2048 -nodes -days 825 \
  -keyout tls/key.pem -out tls/cert.pem \
  -subj "/CN=$HOST" -addext "subjectAltName=$SAN" 2>/dev/null

chmod 600 tls/key.pem
echo "TLS-сертификат создан: deploy/tls/{cert,key}.pem  (CN=$HOST, SAN=$SAN)"
echo "Самоподписанный — браузер предупредит один раз. Для боевого HTTPS используй Let's Encrypt."
