#!/usr/bin/with-contenv bashio
echo "Tado-local server starting.."
CONFIG_PSTH=/data/options.json

# Get the variables from HA
BRIDGE_IP="$(bashio::config 'bridge_ip')"
echo "Bridge IP is $BRIDGE_IP"
BRIDGE_PIN="$(bashio::config 'bridge_pin')"
echo "Bridge pin is $BRIDGE_PIN"

# Start the server
/usr/src/app/TadoLocal/my-venv/bin/tado-local --bridge-ip "${BRIDGE_IP}" --pin "${BRIDGE_PIN}" --state /data/tado-local.db

