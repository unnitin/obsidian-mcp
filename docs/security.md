# Security Spec

## Threat model

The backend exposes a search and ingestion API over HTTP. The index contains
the full text of every note, PDF, and web page in your vault — this is
sensitive personal data that must not be accessible to anyone outside your
trusted devices.

The primary threats are:

| Threat | Severity |
|--------|----------|
| Backend reachable from the public internet | Critical |
| Unauthenticated requests on the local network | High |
| Note content leaked via unencrypted HTTP | Medium |
| Vault path or DB file accessible to other users | Low |

---

## Network security — Tailscale (required for Mac Mini hosting)

When the backend runs on a Mac Mini as a home server, it **must not** bind to
`0.0.0.0` on the physical network interface. Exposing port `51234` on your
home LAN means any device on your Wi-Fi can query or modify your vault index.

The correct approach is to bind exclusively to the **Tailscale interface**, so
only devices on your tailnet can reach the backend.

### How it works

Tailscale assigns each device a stable `100.x.x.x` IP address (the tailnet
IP) and a `*.ts.net` MagicDNS hostname (e.g. `mac-mini.tail1234.ts.net`).
Traffic between tailnet devices is encrypted end-to-end with WireGuard —
even if it crosses the public internet.

```
iPhone (Obsidian)          ──► Tailscale WireGuard ──► Mac Mini :51234
MacBook (Obsidian plugin)  ──► Tailscale WireGuard ──► Mac Mini :51234
MacBook (Claude Desktop)   ──► spawns MCP locally  (no network)
```

No ports need to be opened on your router. No VPN server to manage.
The backend is completely invisible to the public internet.

### Configuration

On the Mac Mini, bind to the Tailscale IP only:

```dotenv
# .env on Mac Mini
HOST=100.x.x.x        # replace with Mac Mini's tailnet IP
PORT=51234
VAULT_PATH=/Users/yourname/Library/Mobile Documents/iCloud~md~obsidian/Documents/YourVaultName
```

Find your Mac Mini's tailnet IP:

```bash
tailscale ip -4
# or use the MagicDNS hostname:
tailscale status | grep mac-mini
```

On each client device, set the plugin backend URL to:

```
http://mac-mini.tail1234.ts.net:51234
```

or using the IP directly:

```
http://100.x.x.x:51234
```

### iPhone access

Obsidian on iOS can reach the Mac Mini backend via Tailscale if:

1. Tailscale app is installed on the iPhone
2. The iPhone is connected to the tailnet (Tailscale is active)
3. The plugin Backend URL is set to the Mac Mini's MagicDNS hostname

When Tailscale is off on the iPhone, the plugin will simply fail to reach the
backend and fall back to no results — the vault itself is unaffected.

### Tailscale ACLs (access control)

In your tailnet admin panel (`login.tailscale.com`), restrict port `51234` to
only your own devices:

```json
{
  "acls": [
    {
      "action": "accept",
      "src": ["autogroup:personal"],
      "dst": ["tag:obsidian-backend:51234"]
    }
  ],
  "tagOwners": {
    "tag:obsidian-backend": ["autogroup:owner"]
  }
}
```

Tag the Mac Mini as `tag:obsidian-backend` in the admin panel. This ensures
no other tailnet members (if you ever share your tailnet) can reach the backend.

---

## Authentication

The backend currently has **no authentication layer** — any device that can
reach port `51234` can query and modify the index. This is acceptable when
combined with Tailscale (access is gated at the network layer), but if you
ever expose the backend beyond your tailnet, API key authentication must be
added first.

### Planned: API key middleware (not yet implemented)

When implemented, every request will require:

```
Authorization: Bearer <api-key>
```

The key will be set via environment variable:

```dotenv
OBSIDIAN_SEARCH_API_KEY=<random-256-bit-hex>
```

Requests without a valid key return `401 Unauthorized`.

The Obsidian plugin and MCP server config will need the key added to their
respective settings.

---

## Transport encryption

Tailscale encrypts all traffic between devices at the WireGuard layer —
traffic between the iPhone/MacBook and the Mac Mini is encrypted in transit
even though the backend serves plain HTTP. Adding TLS on top of Tailscale
is redundant and not required.

If the backend is ever exposed outside Tailscale (e.g. over a raw LAN or
reverse proxy), HTTPS must be added — either via a reverse proxy (Caddy,
nginx) or by enabling TLS in uvicorn directly.

---

## Data at rest

| Asset | Location | Protection |
|-------|----------|------------|
| Note content | `{vault}/*.md` | iCloud encryption at rest |
| Vector index | `{vault}/.obsidian-search/semantic-search.db` | iCloud encryption at rest |
| Embedding model weights | `~/.cache/huggingface/` | Local disk only, not synced |
| Backend logs | `/tmp/obsidian-search.log` | Local disk, not synced |

The DB file syncs via iCloud and benefits from Apple's encryption at rest.
No plaintext secrets are stored in the DB — it contains only chunk text,
embeddings, and metadata already present in the vault.

---

## CORS

The FastAPI server restricts cross-origin requests to:

```python
allow_origins=["app://obsidian.md", "http://localhost:51234"]
```

`app://obsidian.md` is the Electron origin used by the Obsidian desktop app.
This prevents arbitrary browser-based cross-origin requests to the backend.

When the backend moves to the Mac Mini and clients access it via a tailnet
hostname, the Obsidian plugin communicates via a background fetch (not a
browser same-origin request), so CORS does not apply — CORS is only enforced
on browser-initiated requests.

---

## Security checklist — Mac Mini deployment

- [ ] Tailscale installed and active on Mac Mini, MacBook(s), and iPhone
- [ ] Backend bound to Tailscale IP only (`HOST=100.x.x.x`)
- [ ] Port `51234` not forwarded on router
- [ ] Tailscale ACLs configured to restrict port `51234` to personal devices
- [ ] Plugin Backend URL updated to MagicDNS hostname on each device
- [ ] Backend logs reviewed periodically (`/tmp/obsidian-search.log`)
- [ ] `VAULT_PATH` set to iCloud-synced vault folder on Mac Mini
