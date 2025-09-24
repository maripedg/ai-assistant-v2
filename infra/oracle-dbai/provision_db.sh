set -a; source "${ENV_FILE}"; set +a

# Defaults for auto-port behavior
AUTO_PORT="${AUTO_PORT:-false}"
PORT_RANGE_START="${PORT_RANGE_START:-1521}"
PORT_RANGE_END="${PORT_RANGE_END:-1599}"

# Validate required vars (except PORT which can be auto-assigned)
[[ -z "${CONTAINER_NAME:-}" ]] && die "Missing CONTAINER_NAME in ${ENV_FILE}"
[[ -z "${VOLUME_PATH:-}"   ]] && die "Missing VOLUME_PATH in ${ENV_FILE}"
[[ -z "${ORACLE_PWD:-}"    ]] && die "Missing ORACLE_PWD in ${ENV_FILE}"

# Check docker and compose plugin
command -v docker >/dev/null 2>&1 || die "Docker is not installed"
docker compose version >/dev/null 2>&1 || die "Docker compose plugin is not available"

# Port selection logic
if [[ "${AUTO_PORT}" == "true" || -z "${PORT:-}" ]]; then
  CANDIDATE_PORT="$(find_free_port)" || die "No free port found in range ${PORT_RANGE_START}-${PORT_RANGE_END}"
  PORT="${CANDIDATE_PORT}"
  log "Selected free port: ${PORT}"
else
  if ! is_port_free "${PORT}"; then
    die "Configured port ${PORT} is already in use. Set AUTO_PORT=true or choose another PORT."
  fi
fi

# Prepare persistent volume directory
log "Preparing Oracle volume at ${VOLUME_PATH}..."
sudo mkdir -p "${VOLUME_PATH}"

# WARNING: uncomment the next line only for fresh deployments (it wipes previous data)
# sudo rm -rf "${VOLUME_PATH:?}/"*

# Set permissions so the 'oracle' user in the container (uid/gid 54321) can write
sudo chmod 777 "${VOLUME_PATH}"
sudo chown 54321:54321 "${VOLUME_PATH}"

# Start container
log "Starting container ${CONTAINER_NAME} on port ${PORT}..."
# Export env vars for docker compose; override PORT with the selected value
export $(grep -v '^\s*#' "${ENV_FILE}" | xargs -d '\n' || true)
export PORT="${PORT}"

docker compose up -d

# Wait for database readiness
log "Waiting until the database reports readiness..."
READY_MSG="DATABASE IS READY TO USE"
end=$((SECONDS+1200))  # 20-minute timeout
while true; do
  if docker logs "${CONTAINER_NAME}" 2>&1 | grep -q "${READY_MSG}"; then
    log "Database is ready."
    break
  fi
  if (( SECONDS > end )); then
    die "Timeout while waiting for database readiness. See: docker logs ${CONTAINER_NAME}"
  fi
  sleep 5
done

# Print connection info
VM_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
log "Connection DSN (thin driver): ${VM_IP:-<VM_IP>}:${PORT}/FREEPDB1"
log "Admin access is via SYS as SYSDBA using ORACLE_PWD. Create a dedicated application user for your app."
log "Example sqlplus inside container: docker exec -it ${CONTAINER_NAME} bash -lc 'sqlplus / as sysdba'"

log "Done."
