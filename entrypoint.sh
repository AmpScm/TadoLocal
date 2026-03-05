#!/bin/sh
set -e

STATE_DB="/data/tado-local.db"

if [ -z "$TADO_BRIDGE_IP" ] && [ ! -f "$STATE_DB" ]; then
    echo "No TADO_BRIDGE_IP set and no existing pairing DB found at $STATE_DB."
    echo "Set TADO_BRIDGE_IP (and TADO_BRIDGE_PIN for first pairing) then restart."
    echo "Sleeping to keep container alive for debugging..."
    exec sleep infinity
fi

ARGS="--state $STATE_DB --port ${TADO_PORT:-4407}"

if [ -n "$TADO_BRIDGE_IP" ]; then
    ARGS="$ARGS --bridge-ip $TADO_BRIDGE_IP"
fi

if [ -n "$TADO_BRIDGE_PIN" ]; then
    ARGS="$ARGS --pin $TADO_BRIDGE_PIN"
fi

# Forward-compatible: PR #40 accessory support (comma-separated IPs and PINs)
if [ -n "$TADO_ACCESSORY_IP" ]; then
    IFS=','
    set -- $TADO_ACCESSORY_IP
    idx=0
    for ip in "$@"; do
        pin=$(echo "$TADO_ACCESSORY_PIN" | cut -d',' -f$((idx + 1)))
        ARGS="$ARGS --accessory-ip $ip"
        [ -n "$pin" ] && ARGS="$ARGS --accessory-pin $pin"
        idx=$((idx + 1))
    done
    unset IFS
fi

exec tado-local $ARGS
