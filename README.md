# hubitat-mcp

Self-hosted [Model Context Protocol](https://modelcontextprotocol.io) server that exposes a [Hubitat Elevation](https://hubitat.com) hub's [Maker API](https://docs2.hubitat.com/en/apps/maker-api) to any MCP-compatible AI assistant (Claude, ChatGPT, Cursor, etc.).

Runs entirely on your own infrastructure — no cloud relay, no third-party platform, no data leaves your network.

## Why this exists

Other Hubitat MCP implementations require hosting on a SaaS platform (Workato, etc.), which means exposing your hub's cloud endpoint, handing a vendor your access token, and paying for a tier once you exceed free limits. This project is a single Python container you run on your own hardware. It talks to Hubitat over your LAN and to the AI assistant over an authenticated HTTPS endpoint you control.

## Features

- **All Maker API operations** — devices, commands, events, modes, HSM arm state, hub variables
- **Streamable HTTP transport** — works with Claude Desktop, Claude.ai remote connectors, and any MCP client that supports HTTP
- **Bearer token auth** — required on every request, independent of any reverse proxy in front
- **Structured audit logging** — JSON to stdout (for log aggregators like Dozzle) and an append-only audit file for write operations
- **Health endpoint** — unauthenticated `/health` for Docker healthchecks and uptime monitors
- **Non-root container** — runs as UID 1000, minimal attack surface

## Tools exposed

| Tool | Purpose |
|---|---|
| `list_devices` | Basic device list |
| `list_devices_detailed` | Full device list with attributes and capabilities |
| `get_device` | Single device detail |
| `get_device_attribute` | Single attribute value |
| `list_device_commands` | Available commands for a device |
| `get_device_events` | Device event history |
| `send_device_command` | Send any command (on/off/setLevel/setColor/etc.) |
| `list_modes` / `set_mode` | Hub mode control (Home/Away/Night/etc.) |
| `get_hsm_status` / `set_hsm_state` | Hubitat Safety Monitor |
| `list_hub_variables` / `get_hub_variable` / `set_hub_variable` | Hub variable read/write |

Write tools (`send_device_command`, `set_mode`, `set_hsm_state`, `set_hub_variable`) are tagged as security-sensitive in their descriptions so the AI assistant prompts for confirmation before executing them.

## Quick start (Docker Compose)

### 1. Prerequisites

- A Hubitat Elevation hub on your LAN
- Docker + Docker Compose on the host that will run this container
- The host must have LAN access to your Hubitat hub

### 2. Configure Hubitat Maker API

1. Hubitat admin → **Apps** → **Add Built-In App** → **Maker API**
2. Select the devices to expose
3. **Allow access via cloud endpoints**: leave **unchecked** (local only)
4. Under **Allowed IPs / Local LAN Access**, add the IP of your Docker host
5. Note the **App ID** (number in the generated URLs) and **Access Token**

### 3. Run the container

```bash
git clone https://github.com/pghart/hubitat-mcp.git
cd hubitat-mcp

# Generate a bearer token for clients to authenticate with
openssl rand -hex 32

# Create a .env file with your values
cat > .env <<EOF
HUBITAT_HUB_IP=192.168.1.10
HUBITAT_APP_ID=123
HUBITAT_ACCESS_TOKEN=your-maker-api-token
MCP_BEARER_TOKEN=the-openssl-output-from-above
EOF

# Create the audit log directory
mkdir -p ./logs

docker compose up -d
```

Check it's up:

```bash
curl http://localhost:8765/health
# {"status":"ok","service":"hubitat-mcp"}
```

### 4. Connect an AI assistant

**Claude Desktop** — edit `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hubitat": {
      "type": "http",
      "url": "http://your-host:8765/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_BEARER_TOKEN"
      }
    }
  }
}
```

**Claude.ai remote connector** — put an HTTPS-terminating reverse proxy in front (Nginx Proxy Manager, Caddy, Traefik), then add a custom connector pointing at `https://your-domain/mcp` with the bearer token in the header.

## Production deployment

### Reverse proxy

MCP's Streamable HTTP transport uses SSE-style long-lived responses. Proxies need:

```nginx
proxy_buffering off;
proxy_read_timeout 3600s;
proxy_send_timeout 3600s;
```

### Remote access

Two good options depending on your risk tolerance:

**Cloudflare Tunnel + Cloudflare Access** — Expose via tunnel with OAuth/email OTP in front. Adds identity-level gating on top of the bearer token. Test OAuth flow with Claude Desktop first to confirm the AI's connector can complete Access's challenge.

**Tailscale / WireGuard** — Keep the service LAN-only and bring the AI-running device onto your private network instead. Simpler, no public exposure at all.

### Pre-built image

The included GitHub Actions workflow publishes `ghcr.io/pghart/hubitat-mcp:latest` on every push to main and a semver-tagged image on every `v*` tag. Swap the `build:` block in `docker-compose.yml` for `image: ghcr.io/pghart/hubitat-mcp:latest` once the image is published.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `HUBITAT_HUB_IP` | yes | — | LAN IP of your Hubitat hub |
| `HUBITAT_APP_ID` | yes | — | Maker API App ID |
| `HUBITAT_ACCESS_TOKEN` | yes | — | Maker API access token |
| `MCP_BEARER_TOKEN` | yes | — | Token clients must send as `Authorization: Bearer ...` |
| `AUDIT_LOG_PATH` | no | `/var/log/hubitat-mcp/audit.log` | Where to append audit entries for write ops |
| `LOG_LEVEL` | no | `INFO` | Python logging level |
| `HTTP_TIMEOUT` | no | `10` | Hubitat HTTP request timeout in seconds |

## Audit logging

Every write operation (`send_device_command`, `set_mode`, `set_hsm_state`, `set_hub_variable`) writes a JSON line to:

- **stdout** — captured by Docker logs, viewable in tools like Dozzle
- **`AUDIT_LOG_PATH`** — append-only file, persisted across container rebuilds via volume mount

Example:

```json
{"ts":"2026-04-18T19:30:00Z","action":"device_command","device_id":"42","command":"on","value1":null,"value2":null}
```

Reads aren't audited — add instrumentation in `hubitat_get()` if you want that.

## Security notes

- The bearer token is the authoritative gate. A reverse proxy in front is defense in depth, not a substitute.
- Hubitat Maker API access tokens have full control over every device you exposed to the app. Scope the Maker API to only the devices you want the AI to control.
- HSM arm/disarm and lock/unlock commands work by default. If you want to block them entirely, delete the relevant tools from `server.py` before deploying.
- Running as non-root (UID 1000) inside the container. If you bind-mount the audit log directory, make sure it's writable by UID 1000 on the host.

## Built with

- [FastMCP](https://github.com/jlowin/fastmcp) — Python MCP framework
- [httpx](https://www.python-httpx.org/) — async HTTP client
- The [OpenAPI spec](https://github.com/craigde/hubitat-mcp-server) by @craigde, which was the reference for tool coverage

## License

MIT — see [LICENSE](./LICENSE).

## Not affiliated with Hubitat

Hubitat® and Hubitat Elevation® are trademarks of Hubitat, Inc. This project is an independent community implementation.
