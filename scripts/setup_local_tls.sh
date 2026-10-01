#!/bin/sh
set -eu

repo_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
tls_dir="$repo_root/data/tls"
firmware_ca="$repo_root/firmware/main/friday_backend_ca.pem"
lan_ip="${1:-}"

if [ -z "$lan_ip" ]; then
  lan_ip=$(ifconfig 2>/dev/null | awk '/inet 192\.168\./ { print $2; exit }')
fi
if [ -z "$lan_ip" ]; then
  printf 'Usage: %s LAN_IP\n' "$0" >&2
  exit 2
fi

mkdir -p "$tls_dir"
chmod 700 "$tls_dir"

if [ ! -f "$tls_dir/friday-ca.key" ] || [ ! -f "$tls_dir/friday-ca.crt" ]; then
  openssl req -x509 -newkey rsa:3072 -sha256 -nodes -days 3650 \
    -subj '/CN=Friday Local Device CA' \
    -addext 'basicConstraints=critical,CA:TRUE' \
    -addext 'keyUsage=critical,keyCertSign,cRLSign' \
    -addext 'subjectKeyIdentifier=hash' \
    -keyout "$tls_dir/friday-ca.key" \
    -out "$tls_dir/friday-ca.crt"
  chmod 600 "$tls_dir/friday-ca.key"
fi

openssl req -newkey rsa:2048 -sha256 -nodes \
  -subj '/CN=Friday Backend' \
  -keyout "$tls_dir/friday-backend.key" \
  -out "$tls_dir/friday-backend.csr"

extension_file=$(mktemp)
trap 'rm -f "$extension_file" "$tls_dir/friday-backend.csr"' EXIT
{
  printf 'basicConstraints=CA:FALSE\n'
  printf 'keyUsage=digitalSignature,keyEncipherment\n'
  printf 'extendedKeyUsage=serverAuth\n'
  printf 'subjectAltName=DNS:localhost,IP:127.0.0.1,IP:%s\n' "$lan_ip"
} > "$extension_file"

openssl x509 -req -sha256 -days 825 \
  -in "$tls_dir/friday-backend.csr" \
  -CA "$tls_dir/friday-ca.crt" \
  -CAkey "$tls_dir/friday-ca.key" \
  -CAcreateserial \
  -extfile "$extension_file" \
  -out "$tls_dir/friday-backend.crt"
chmod 600 "$tls_dir/friday-backend.key"
cp "$tls_dir/friday-ca.crt" "$firmware_ca"

printf 'Friday TLS ready for localhost and %s\n' "$lan_ip"
printf 'Rebuild and flash firmware if the CA changed.\n'
