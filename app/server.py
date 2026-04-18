"""
Hubitat MCP Server
------------------
Exposes the Hubitat Maker API as MCP tools for Claude.

Transport: Streamable HTTP (remote-compatible)
Auth: Bearer token required on every request
Audit: Structured JSON logs to stdout + append-only audit log file
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier
from starlette.requests import Request
from starlette.responses import JSONResponse

# -------------------------------------------------------------------
# Configuration (all via environment variables)
# -------------------------------------------------------------------
HUBITAT_HUB_IP = os.environ["HUBITAT_HUB_IP"]
HUBITAT_APP_ID = os.environ["HUBITAT_APP_ID"]
HUBITAT_ACCESS_TOKEN = os.environ["HUBITAT_ACCESS_TOKEN"]
MCP_BEARER_TOKEN = os.environ["MCP_BEARER_TOKEN"]

AUDIT_LOG_PATH = os.environ.get("AUDIT_LOG_PATH", "/var/log/hubitat-mcp/audit.log")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "10"))

HUBITAT_BASE_URL = f"http://{HUBITAT_HUB_IP}/apps/api/{HUBITAT_APP_ID}"

# -------------------------------------------------------------------
# Logging — structured JSON to stdout for Dozzle
# -------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if hasattr(record, "extra_fields"):
            payload.update(record.extra_fields)
        return json.dumps(payload)


handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(JsonFormatter())
logging.basicConfig(level=LOG_LEVEL, handlers=[handler])
log = logging.getLogger("hubitat-mcp")


def audit(action: str, detail: dict[str, Any]) -> None:
    """Write to both stdout (for Dozzle) and the audit log file (for retention)."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "action": action,
        **detail,
    }
    log.info(f"AUDIT {action}", extra={"extra_fields": entry})
    try:
        os.makedirs(os.path.dirname(AUDIT_LOG_PATH), exist_ok=True)
        with open(AUDIT_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.warning(f"Audit file write failed: {e}")


# -------------------------------------------------------------------
# Hubitat HTTP client
# -------------------------------------------------------------------
client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)


async def hubitat_get(path: str) -> Any:
    """Call the Maker API. All Maker API operations are GET requests."""
    url = f"{HUBITAT_BASE_URL}{path}"
    params = {"access_token": HUBITAT_ACCESS_TOKEN}
    resp = await client.get(url, params=params)
    resp.raise_for_status()
    # Hubitat returns JSON for data endpoints, sometimes plain text/empty for commands
    try:
        return resp.json()
    except ValueError:
        return {"status": "ok", "raw": resp.text}


# -------------------------------------------------------------------
# MCP server with bearer token auth
# -------------------------------------------------------------------
auth = StaticTokenVerifier(
    tokens={
        MCP_BEARER_TOKEN: {
            "client_id": "hubitat-mcp-client",
            "scopes": ["hubitat:read", "hubitat:write"],
        }
    },
    required_scopes=["hubitat:read"],
)

mcp = FastMCP(
    name="hubitat-mcp",
    instructions=(
        "Control and query a Hubitat Elevation smart home hub via the Maker API. "
        "Tools are grouped by capability: devices (list/query), commands (on/off/setLevel/etc.), "
        "events (history), modes (Home/Away/Night), HSM (security arm state), and hub variables. "
        "Destructive or security-sensitive operations (HSM arm changes, mode changes, lock/unlock) "
        "should be confirmed with the user before execution."
    ),
    auth=auth,
)


# -------------------------------------------------------------------
# Tools — Devices (read)
# -------------------------------------------------------------------
@mcp.tool
async def list_devices() -> list[dict]:
    """List all devices exposed by Maker API (basic info: id, name, label, type)."""
    return await hubitat_get("/devices")


@mcp.tool
async def list_devices_detailed() -> list[dict]:
    """List all devices with full attributes, capabilities, and current state. Larger payload than list_devices."""
    return await hubitat_get("/devices/all")


@mcp.tool
async def get_device(device_id: str) -> dict:
    """Get full details for a specific device including all current attribute values."""
    return await hubitat_get(f"/devices/{device_id}")


@mcp.tool
async def get_device_attribute(device_id: str, attribute_name: str) -> dict:
    """Get a single attribute value for a device (e.g. 'switch', 'level', 'temperature', 'battery')."""
    return await hubitat_get(f"/devices/{device_id}/attribute/{attribute_name}")


@mcp.tool
async def list_device_commands(device_id: str) -> list:
    """List the commands available for a specific device (e.g. on, off, setLevel)."""
    return await hubitat_get(f"/devices/{device_id}/commands")


@mcp.tool
async def get_device_events(device_id: str) -> list[dict]:
    """Get event history for a device. Useful for auditing what a device has done recently."""
    return await hubitat_get(f"/devices/{device_id}/events")


# -------------------------------------------------------------------
# Tools — Device commands (write)
# -------------------------------------------------------------------
@mcp.tool
async def send_device_command(
    device_id: str,
    command: str,
    value1: str | None = None,
    value2: str | None = None,
) -> dict:
    """
    Send a command to a device. Examples:
      - Turn on:         command='on'
      - Turn off:        command='off'
      - Dimmer 50%:      command='setLevel', value1='50'
      - Color temp 3000K: command='setColorTemperature', value1='3000'
      - Thermostat 72F:  command='setHeatingSetpoint', value1='72'
      - Lock:            command='lock'   (SECURITY-SENSITIVE - confirm with user)
      - Unlock:          command='unlock' (SECURITY-SENSITIVE - confirm with user)
      - Open/close:      command='open' or 'close'

    Use list_device_commands first to see what a device supports.
    """
    path = f"/devices/{device_id}/{command}"
    if value1 is not None:
        path += f"/{value1}"
    if value2 is not None:
        path += f"/{value2}"

    audit(
        "device_command",
        {"device_id": device_id, "command": command, "value1": value1, "value2": value2},
    )
    return await hubitat_get(path)


# -------------------------------------------------------------------
# Tools — Modes (Home/Away/Night/etc.)
# -------------------------------------------------------------------
@mcp.tool
async def list_modes() -> list[dict]:
    """List all hub modes and which one is currently active."""
    return await hubitat_get("/modes")


@mcp.tool
async def set_mode(mode_id: str) -> dict:
    """
    Change the hub mode (e.g. Home, Away, Night). SECURITY-SENSITIVE: mode changes often trigger
    automation rules (lights, locks, HSM arm state). Confirm intent with the user first.
    Use list_modes to see available mode IDs.
    """
    audit("set_mode", {"mode_id": mode_id})
    return await hubitat_get(f"/modes/{mode_id}")


# -------------------------------------------------------------------
# Tools — HSM (Hubitat Safety Monitor)
# -------------------------------------------------------------------
@mcp.tool
async def get_hsm_status() -> dict:
    """Get the current Hubitat Safety Monitor arm state."""
    return await hubitat_get("/hsm")


@mcp.tool
async def set_hsm_state(arm_state: str) -> dict:
    """
    Set HSM arm state. SECURITY-CRITICAL: this changes the home security posture.
    Always confirm with the user before calling.

    Valid values: armAway, armHome, armNight, disarm, disarmAll, cancelAlerts
    """
    valid = {"armAway", "armHome", "armNight", "disarm", "disarmAll", "cancelAlerts"}
    if arm_state not in valid:
        return {"error": f"Invalid arm_state. Must be one of: {sorted(valid)}"}

    audit("set_hsm_state", {"arm_state": arm_state})
    return await hubitat_get(f"/hsm/{arm_state}")


# -------------------------------------------------------------------
# Tools — Hub variables
# -------------------------------------------------------------------
@mcp.tool
async def list_hub_variables() -> dict:
    """List all hub variables and their current values."""
    return await hubitat_get("/hubvariables")


@mcp.tool
async def get_hub_variable(name: str) -> dict:
    """Get the value of a specific hub variable."""
    return await hubitat_get(f"/hubvariables/{name}")


@mcp.tool
async def set_hub_variable(name: str, value: str) -> dict:
    """Set the value of a hub variable. Confirm with user if the variable drives automations."""
    audit("set_hub_variable", {"name": name, "value": value})
    return await hubitat_get(f"/hubvariables/{name}/{value}")


# -------------------------------------------------------------------
# Health endpoint (via custom route) so NPM/Uptime Kuma can ping
# -------------------------------------------------------------------
@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request):
    return JSONResponse({"status": "ok", "service": "hubitat-mcp"})


# -------------------------------------------------------------------
# Entry point
# -------------------------------------------------------------------
if __name__ == "__main__":
    log.info(
        "Starting Hubitat MCP server",
        extra={
            "extra_fields": {
                "hub_ip": HUBITAT_HUB_IP,
                "app_id": HUBITAT_APP_ID,
                "audit_log": AUDIT_LOG_PATH,
            }
        },
    )
    mcp.run(transport="http", host="0.0.0.0", port=8765, path="/mcp")
