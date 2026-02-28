# LDAP MemberOf Proxy

A lightweight, low-maintenance LDAP proxy that dynamically adds `memberOf` attributes to LDAP user entries. This proxy sits between your applications and an OpenLDAP server (or similar) that doesn't natively support the `memberOf` overlay.

This is sometimes necessary to support LDAP groups for applications (e.g., Nextcloud, Open WebUI), when the upstream LDAP service cannot be modified (e.g., in corporate environments). Here, this application provides the group membership information via the `memberOf` property.

## Key Features

- **MemberOf Overlay**: Automatically injects `memberOf` attributes into user search results
- **Two Sync Modes**:
  - **LIVE**: Real-time LDAP queries with in-memory TTL cache
  - **DATABASE**: SQLite-backed cache with configurable crawl schedules
- **Flexible Scheduling**: (For DATABASE  mode) Support for interval-based (seconds) and cron-style scheduling
- **TLS Support**: Support for LDAP, LDAPS (TLS), and STARTTLS to upstream LDAP server; support for LDAP and LDAPS for the LDAP proxy server.
- **Configurable**: Extensive environment variable configuration
- **Lightweight**: Alpine-based Docker image
- **Low-Maintenance**: No complex service states, no external service requirements

**Security Note**: This proxy is meant to be integrated to only interact with a particular target application (e.g., Nextcloud). If the LDAP server requires authentication, the proxy needs to know the credentials, and provides an **anonymous** LDAP view. Thus, **apply appropriate measures** to avoid illegitimate use and access of the proxy service. For instance, rely on Docker-based network isolation to limit remote access, and avoid exposing the proxy port to remote hosts (e.g., by firewall rules).

## How to Use

Download the required files:
```bash
# [For upgrades] Remove old code
rm ldap-memberof-proxy/ -rf

# Clone repository
git clone https://github.com/j-frei/ldap-memberof-proxy ldap-memberof-proxy

# [Optional] Remove all unnecessary files
./ldap-memberof-proxy/minimize.sh
```

Now, continue either with `Docker-compose`, `Docker only`, or `Standalone`.

### Docker-compose
<details>
<summary>Click to show Docker-compose instructions.</summary>
If you are using Docker-compose, add the following service to your `docker-compose.yml`:

```yml
services:
  ... other services ...

  ldap-memberof-proxy:
    build: ./ldap-memberof-proxy
    # Optional entries
    image: ldap-memberof-proxy:latest
    container_name: ldap-memberof-proxy
    pull_policy: never
    restart: unless-stopped
    # Configurations (change values accordingly)
    ports:
      - "3890:3890"
    environment:
      # Basic settings
      - LISTEN_PORT=3890
      - LOG_LEVEL=WARNING
      - CACHE_MODE=LIVE

      # Upstream LDAP
      - UPSTREAM_HOST=ldap.example.com
      - UPSTREAM_PORT=389
      - UPSTREAM_MODE=NONE

      # LDAP Schema
      - GROUP_SEARCH_BASE=ou=groups,dc=example,dc=com
      - GROUP_MEMBER_ATTR=memberUid
      - USER_ID_ATTR=uid
```

and you are done.

Run the following command to build and/or run the service:
```bash
# Just build
docker compose build

# Run the service
docker compose up -d
```

</details>


### Docker only
<details>
<summary>Click to show plain Docker instructions.</summary>
If you are using Docker, run the following commands:

```bash
# Build the image
docker build -t ldap-memberof-proxy:latest ./ldap-memberof-proxy

# Configure and run the Docker container (change values accordingly):
docker run -d \
  --name ldap-memberof-proxy \
  --restart unless-stopped \
  -p 3890:3890 \
  -e LISTEN_PORT=3890 \
  -e LOG_LEVEL=WARNING \
  -e CACHE_MODE=LIVE \
  -e UPSTREAM_HOST=ldap.example.com \
  -e UPSTREAM_PORT=389 \
  -e UPSTREAM_MODE=NONE \
  -e GROUP_SEARCH_BASE=ou=groups,dc=example,dc=com \
  -e GROUP_MEMBER_ATTR=memberUid \
  -e USER_ID_ATTR=uid \
  ldap-memberof-proxy:latest
```

and you are done.

</details>

### Standalone
<details>
<summary>Click to show standalone instructions.</summary>
If you want to run the script as standalone, run the following commands:

```bash
# Create and enter venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies
python3 -m pip install -r ldap-memberof-proxy/requirements.txt

# Run the script
LISTEN_PORT=3890 \
LOG_LEVEL=WARNING \
CACHE_MODE=LIVE \
UPSTREAM_HOST=ldap.example.com \
UPSTREAM_PORT=389 \
UPSTREAM_MODE=NONE \
GROUP_SEARCH_BASE="ou=groups,dc=example,dc=com" \
GROUP_MEMBER_ATTR=memberUid \
USER_ID_ATTR=uid \
python3 ldap-memberof-proxy/proxy.py
```

</details>

## Configuration
**Note**: All configuration is done via ENV variables. Check `docker-compose.full-options.yml` to directly see all available ENV variables.

### Basic Settings

| Variable | Default | Description |
|----------|---------|-------------|
| `LISTEN_PORT` | `3890` | Port for the proxy to listen on (must be >1024, see note below) |
| `LOG_LEVEL` | `WARNING` | Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL |
| `CACHE_TTL` | `300` | In-memory cache TTL in seconds |

**Port Restrictions**: The proxy runs as non-root user (UID 1000) and cannot bind to privileged ports (<=1024) inside the container. Use ports >1024 for `LISTEN_PORT`. You can still map to privileged ports on the host (e.g., `-p 636:6360` maps host port 636 to container port 6360).

### Cache Mode

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_MODE` | `LIVE` | `LIVE` (real-time queries) or `DATABASE` (SQLite cache) |
| `CRAWL_INTERVAL` | `86400` | **DATABASE mode only**: Crawl schedule (seconds or cron) |
| `DB_PATH` | `/app/data/ldap_cache.db` | SQLite database path |
| `LOOKUP_MAX_USERS` | `0` | **LIVE mode only**: Max users per query (<=0 = unlimited) |

#### Crawl Interval Examples

```bash
# Every 24 hours (86400 seconds)
CRAWL_INTERVAL=86400

# Daily at 2 AM UTC (cron expression)
CRAWL_INTERVAL="0 2 * * *"

# Every 6 hours
CRAWL_INTERVAL="0 */6 * * *"

# Every Sunday at midnight
CRAWL_INTERVAL="0 0 * * 0"
```

### Upstream LDAP

| Variable | Default | Description |
|----------|---------|-------------|
| `UPSTREAM_HOST` | `ldap.example.com` | Upstream LDAP server hostname |
| `UPSTREAM_PORT` | `389` | Upstream LDAP server port |
| `UPSTREAM_MODE` | `NONE` | Connection mode: `NONE`, `STARTTLS`, or `TLS` (LDAPS) |
| `UPSTREAM_TLS_VERIFY` | `true` | Verify upstream TLS certificates |
| `BIND_DN` | *(empty)* | Bind DN for upstream authentication |
| `BIND_PASSWORD` | *(empty)* | Bind password |
| `BIND_PASSWORD_FILE` | *(empty)* | Path to file containing bind password |

### TLS Configuration (Proxy)

| Variable | Default | Description |
|----------|---------|-------------|
| `TLS_CERT_FILE` | *(empty)* | Path to TLS certificate (enables LDAPS) |
| `TLS_KEY_FILE` | *(empty)* | Path to TLS private key |

**Note**: If both `TLS_CERT_FILE` and `TLS_KEY_FILE` are set and files exist, the proxy will serve LDAPS instead of plain LDAP.

### LDAP Schema Mapping

| Variable | Default | Description |
|----------|---------|-------------|
| `GROUP_SEARCH_BASE` | `ou=groups,dc=example,dc=com` | LDAP base DN for group searches |
| `GROUP_MEMBER_ATTR` | `memberUid` | Group attribute containing member references |
| `USER_ID_ATTR` | `uid` | User attribute to match against group members |
| `MEMBEROF_ATTR` | `memberOf` | Attribute name to inject into user entries |

## Cache Mode Remarks

**LIVE Mode**:

In LIVE mode, every incoming user request is forwarded to the upstream LDAP server.
For each user entry in the response from the upstream LDAP server, another LDAP query is sent to the upstream LDAP server in order to determine the group memberships of the users.

**Pros**: Always up-to-date, no disk usage, lean.

**Cons**: Higher upstream query load, and **can be very slow for LDAP response containing multiple users**!

**Only use this mode if you know that your application queries only individual users.**

**DATABASE Mode**:

The membership tuple (user, group)-pairs are stored into a SQLite database and re-crawled on a fixed interval.

**Pros**: Very fast, low upstream load, handles large result sets

**Cons**: Slight data staleness (based on crawl interval), SQLite database may use some disk space in **very** large LDAP environments. (For reference: 50MB for 28,000 total users with 260,000 total memberships.)

## Some Advanced Examples

### With LDAPS (TLS) to Upstream

```yaml
services:
  ldap-proxy:
    ...
    ports:
      - "636:6360" # Map host port 636 to container port 6360
    environment:
      ...
      - LISTEN_PORT=6360 # Must be >1024 (non-root user)
      - UPSTREAM_HOST=ldaps.example.com
      - UPSTREAM_PORT=636
      - UPSTREAM_MODE=TLS
      - UPSTREAM_TLS_VERIFY=true # Set to skip validation of upstream certificates
      - TLS_CERT_FILE=/certs/server.crt
      - TLS_KEY_FILE=/certs/server.key
    volumes:
      - ./certs:/certs:ro
```

### With DATABASE Mode and Persistence

```yaml
services:
  ldap-proxy:
    ...
    environment:
      ...
      - CACHE_MODE=DATABASE
      - CRAWL_INTERVAL="0 */4 * * *"  # Every 4 hours
      - DB_PATH=/app/data/ldap_cache.db
    volumes:
      - ./ldap_data:/app/data
```

**Note**: Ensure **first** that the `./ldap_data` directory is writable by UID 1000 (the `ldap` user in the container) (e.g., using `mkdir ldap_data && sudo chown -R 1000:1000 ldap_data`).

### With Bind Authentication

```yaml
services:
  ldap-proxy:
    ...
    environment:
      ...
      - BIND_DN=cn=admin,dc=example,dc=com
      - BIND_PASSWORD_FILE=/run/secrets/ldap_password
    secrets:
      - ldap_password

secrets:
  ldap_password:
    file: ./ldap_password.txt
```


### Test LDAP Connection
You may check the connection with the following command:
```bash
ldapsearch -H ldap://localhost:3890 -x -b "dc=example,dc=com" "(uid=testuser)"
```

## Acknowledgments

- Built with [Twisted](https://twisted.org/) and [Ldaptor](https://github.com/twisted/ldaptor)
- Uses [ldap3](https://github.com/cannatag/ldap3) for upstream queries
- Cron scheduling via [croniter](https://github.com/pallets-eco/croniter)
