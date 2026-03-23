#!/usr/bin/with-contenv bashio
echo "Tado-local server starting..."
CONFIG_PSTH=/data/options.json
ARGS=""
PRIVATE_DB_PATH=/data/tado-local.db
PUBLIC_DB_PATH=/homeassistant_config/.storage/tado-local.db

# Get the variables from HA
BRIDGE_IP="$(bashio::config 'bridge_ip')"
BRIDGE_PIN="$(bashio::config 'bridge_pin')"
echo "Bridge:"
echo "  IP is $BRIDGE_IP"
echo "  pin is $BRIDGE_PIN"
ARGS="${ARGS} --bridge-ip ${BRIDGE_IP} --pin ${BRIDGE_PIN}"

# Check if log_level is set to debug or trace
LOG_LEVEL="$(bashio::config 'log_level')"
if [ "$LOG_LEVEL" = "debug" ]; then
    echo "INFO: Enabling verbose logging for log_level=${LOG_LEVEL}"
    ARGS="${ARGS} --verbose"
fi

# Determine where to store the database based on the keep_db_private option
LOCAL_DB="$(bashio::config 'keep_db_private')"
if [ "$LOCAL_DB" = true ]; then
    echo "INFO: keep_db_private is true, using ${PRIVATE_DB_PATH} (not accessible outside the container)"
    ARGS="${ARGS} --state ${PRIVATE_DB_PATH}"
else
    echo "INFO: keep_db_private is false, using ${PUBLIC_DB_PATH} (accessible outside the container)"
    ARGS="${ARGS} --state ${PUBLIC_DB_PATH}"
    if [ -f "${PRIVATE_DB_PATH}" ] && [ ! -f "${PUBLIC_DB_PATH}" ]; then
        # Forward compatibility: if the old database location exists, move it to the new location and use it.
        echo "*** WARNING: DB found at ${PRIVATE_DB_PATH}. move to new location and use it. ***"
        echo "--- mv ${PRIVATE_DB_PATH} ${PUBLIC_DB_PATH}"
        mv $PRIVATE_DB_PATH $PUBLIC_DB_PATH
    fi
fi

# Check if purge_history is set and add it to the arguments
PURGE_DAYS="$(bashio::config 'purge_history')"
if [ -n "$PURGE_DAYS" ] && [[ "$PURGE_DAYS" =~ ^[0-9]+$ ]] && [ "$PURGE_DAYS" -ne 0 ]; then
    echo "INFO: purge history after ${PURGE_DAYS} days"
    ARGS="${ARGS} --purgehistory ${PURGE_DAYS}"
elif [ -n "$PURGE_DAYS" ] && ! [[ "$PURGE_DAYS" =~ ^[0-9]+$ ]]; then
    echo "WARNING: Invalid purge_history value '${PURGE_DAYS}'. It must be a non-negative integer. Ignoring purge_history setting."
fi

# Get the number of accessories configured in HA
ACCESSORY_COUNT="$(bashio::config 'accessories|length' || echo 0)"
echo "Accessories found: ${ACCESSORY_COUNT}"

# Loop through the accessories and build the arguments for tado-local server
for ((i=0; i<ACCESSORY_COUNT; i++)); do
    ACCESSORY_IP="$(bashio::config "accessories[${i}].ip")"
    ACCESSORY_PIN="$(bashio::config "accessories[${i}].pin")"

    echo "Accessory ${i}:"
    echo "  IP: ${ACCESSORY_IP}"
    echo "  PIN: ${ACCESSORY_PIN}"
    ARGS="${ARGS} --accessory-ip ${ACCESSORY_IP} --accessory-pin ${ACCESSORY_PIN}"
done

# Start the server
echo "Exec: ../tado-local $ARGS"
/usr/src/app/TadoLocal/my-venv/bin/tado-local $ARGS

