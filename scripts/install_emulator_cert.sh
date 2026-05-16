#!/usr/bin/env bash
# Optional helper: install the Cosmos emulator's self-signed cert into the
# system trust store. Most contributors are fine with `COSMOS_VERIFY_TLS=false`
# in `.env.local` and never need to run this.
#
# Usage:
#   ./scripts/install_emulator_cert.sh
#
# macOS / Linux only. Requires docker compose to be running.

set -euo pipefail

CERT_URL="https://localhost:8081/_explorer/emulator.pem"
TMP=$(mktemp -t cosmos-emulator.XXXXXX.pem)

curl -k -fsS "$CERT_URL" -o "$TMP"

case "$(uname -s)" in
  Darwin)
    sudo security add-trusted-cert -d -r trustRoot \
      -k /Library/Keychains/System.keychain "$TMP"
    ;;
  Linux)
    sudo cp "$TMP" /usr/local/share/ca-certificates/cosmos-emulator.crt
    sudo update-ca-certificates
    ;;
  *)
    echo "Unsupported OS: $(uname -s)" >&2
    exit 1
    ;;
esac

echo "Installed Cosmos emulator cert from $CERT_URL"
