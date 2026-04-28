#!/usr/bin/env bash
# Print SIP credentials in a copy/paste-friendly form for Linphone, Zoiper,
# MicroSIP, etc. Reads from .env in the project root.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "no .env yet — run: cp .env.example .env  and edit it" >&2
  exit 1
fi

# shellcheck disable=SC1091
set -a; source .env; set +a

host_ip="${1:-127.0.0.1}"

cat <<EOF
Softphone account
-----------------
  Username        : 1000
  Auth user       : 1000
  Password        : ${SIP_EXT_1000_PASSWORD}
  Domain / Realm  : ${host_ip}
  Proxy           : ${host_ip}:5060
  Transport       : UDP

Dial to test
------------
  9979  echo test (your own voice mirrored back)
  9664  music on hold
  9196  Gemini Live voice bot

Linphone one-shot URI:
  sip:1000:${SIP_EXT_1000_PASSWORD}@${host_ip}
EOF
