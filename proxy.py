#!/usr/bin/env python3
"""
LDAP MemberOf Proxy

Transparent LDAP proxy that dynamically injects memberOf attributes into
search results when upstream LDAP servers lack memberOf overlay support.

Author: Johann Frei
Copyright: (c) 2026 Johann Frei
License: MIT License
Repository: https://github.com/jfrei/ldap-memberof-proxy
"""

import os
import sys
import logging
import ssl as py_ssl
import sqlite3
import threading

from twisted.internet import reactor, protocol, endpoints
from twisted.internet import ssl as twisted_ssl
from twisted.python import log as twisted_log
from ldaptor.protocols import pureldap
from ldaptor.protocols.ldap import proxybase
from ldaptor.protocols.ldap.ldapclient import LDAPClient

from cachetools import TTLCache
import ldap3
from ldap3.utils.conv import escape_filter_chars
from croniter import croniter
from datetime import datetime

# Setup logging
LOG_LEVEL = os.environ.get("LOG_LEVEL", "WARNING").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.WARNING),
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

# Basic configuration
UPSTREAM_MODE = os.environ.get("UPSTREAM_MODE", "NONE").upper()
if UPSTREAM_MODE not in ("NONE", "STARTTLS", "TLS"):
    logging.critical(f"Invalid UPSTREAM_MODE: '{UPSTREAM_MODE}'. Must be 'NONE', 'STARTTLS', or 'TLS'.")
    sys.exit(1)

# LIVE does not use SQLite logic & performs (potentially many) real-time LDAP queries per request.
CACHE_MODE = os.environ.get("CACHE_MODE", "LIVE").upper()
if CACHE_MODE not in ("LIVE", "DATABASE"):
    logging.critical(f"Invalid CACHE_MODE: '{CACHE_MODE}'. Must be 'LIVE' or 'DATABASE'.")
    sys.exit(1)

# Exposed proxy port
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", 3890))

# In-memory cache: Default is 5min
CACHE_TTL = int(os.environ.get("CACHE_TTL", 300))

# Only relevant for DATABASE mode
# Can be either:
#   - Seconds between full crawls (e.g., "86400" for daily)
#   - Cron expression (e.g., "0 2 * * *" for daily at 2 AM UTC)
CRAWL_INTERVAL_STR = os.environ.get("CRAWL_INTERVAL", "86400")
# Verify CRAWL_INTERVAL env
try:
    int(CRAWL_INTERVAL_STR)
except ValueError:
    try:
        croniter(CRAWL_INTERVAL_STR, datetime.now())
    except Exception as e:
        logging.critical(f"Invalid CRAWL_INTERVAL '{CRAWL_INTERVAL_STR}': {e}")
        sys.exit(1)

# Threshold of user entries when memberOf subqueries are stopped. <= 0 means unlimited
LOOKUP_MAX_USERS = int(os.environ.get("LOOKUP_MAX_USERS", 0))
DB_PATH = os.environ.get("DB_PATH", "data/ldap_cache.db")

UPSTREAM_HOST = os.environ.get("UPSTREAM_HOST", "ldap.example.com")
UPSTREAM_PORT = int(os.environ.get("UPSTREAM_PORT", 389))
UPSTREAM_TLS_VERIFY = os.environ.get("UPSTREAM_TLS_VERIFY", "true").lower() == "true"

# Required if UPSTREAM_MODE is STARTTLS or TLS
TLS_CERT_FILE = os.environ.get("TLS_CERT_FILE", "")
TLS_KEY_FILE = os.environ.get("TLS_KEY_FILE", "")

# Bind credentials for LDAP queries to the upstream LDAP server
BIND_DN = os.environ.get("BIND_DN", "")
BIND_PASSWORD = os.environ.get("BIND_PASSWORD", "")
BIND_PASSWORD_FILE = os.environ.get("BIND_PASSWORD_FILE", "")

if BIND_PASSWORD_FILE and os.path.isfile(BIND_PASSWORD_FILE):
    with open(BIND_PASSWORD_FILE, 'r') as f:
        BIND_PASSWORD = f.read().strip()

GROUP_SEARCH_BASE = os.environ.get("GROUP_SEARCH_BASE", "ou=groups,dc=example,dc=com")
GROUP_MEMBER_ATTR = os.environ.get("GROUP_MEMBER_ATTR", "memberUid")
USER_ID_ATTR = os.environ.get("USER_ID_ATTR", "uid")
MEMBEROF_ATTR = os.environ.get("MEMBEROF_ATTR", "memberOf")

# Route twisted internal logging to standard python logging
observer = twisted_log.PythonLoggingObserver()
observer.start()

# SQLite-based group membership cache (only used if CACHE_MODE=DATABASE)
class CacheDB:
    def __init__(self):
        db_dir = os.path.dirname(DB_PATH)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS group_members (
                uid TEXT NOT NULL,
                group_dn TEXT NOT NULL,
                PRIMARY KEY (uid, group_dn)
            )
        """)
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_uid ON group_members(uid)")
        self.conn.commit()

    def get_groups(self, uid):
        cursor = self.conn.execute(
            "SELECT group_dn FROM group_members WHERE uid = ?",
            (uid,)
        )
        return [row[0] for row in cursor.fetchall()]

    def bulk_replace(self, rows):
        with self.conn:
            self.conn.execute("DELETE FROM group_members")
            self.conn.executemany(
                "INSERT INTO group_members (uid, group_dn) VALUES (?, ?)",
                rows
            )
        logging.info(f"Database updated with {len(rows)} membership rows.")
db = CacheDB() if CACHE_MODE == "DATABASE" else None

# In-memory TTL cache
group_cache = TTLCache(maxsize=2000, ttl=CACHE_TTL)

def get_ldap_conn():
    use_implicit = (UPSTREAM_MODE == "TLS")
    use_starttls = (UPSTREAM_MODE == "STARTTLS")
    tls_config = ldap3.Tls(validate=py_ssl.CERT_REQUIRED if UPSTREAM_TLS_VERIFY else py_ssl.CERT_NONE) if (use_implicit or use_starttls) else None
    server = ldap3.Server(UPSTREAM_HOST, port=UPSTREAM_PORT, use_ssl=use_implicit, tls=tls_config, get_info=ldap3.NONE, connect_timeout=5)
    conn = ldap3.Connection(server, user=BIND_DN, password=BIND_PASSWORD)

    if use_starttls:
        conn.open()
        conn.start_tls()
    else:
        conn.bind()
    return conn

def crawl_groups():
    logging.info("Background task: Starting full group crawl...")
    try:
        conn = get_ldap_conn()
        membership_rows = []
        with conn:
            for entry in conn.extend.standard.paged_search(
                search_base=GROUP_SEARCH_BASE,
                search_filter=f'({GROUP_MEMBER_ATTR}=*)',
                search_scope=ldap3.SUBTREE,
                attributes=['dn', GROUP_MEMBER_ATTR],
                paged_size=1000,
                generator=True
            ):
                if entry.get('type') != 'searchResEntry':
                    continue

                group_dn = entry['dn']
                members = entry['attributes'].get(GROUP_MEMBER_ATTR, [])

                for m in members:
                    uid_value = m
                    # If attribute contains full DN (e.g. member=uid=...,ou=...)
                    if GROUP_MEMBER_ATTR.lower() == "member":
                        parts = m.split(",")
                        uid_part = next(
                            (p for p in parts if p.lower().startswith("uid=")),
                            None
                        )
                        if not uid_part:
                            continue
                        uid_value = uid_part.split("=", 1)[1]

                    membership_rows.append((uid_value, group_dn))

        db.bulk_replace(membership_rows)

    except Exception as e:
        logging.error(f"Group crawl failed: {e}")

def parse_crawl_schedule():
    try:
        # Try parsing as integer (seconds)
        interval_seconds = int(CRAWL_INTERVAL_STR)
        return (False, interval_seconds)
    except ValueError:
        # Try parsing as cron expression
        try:
            cron = croniter(CRAWL_INTERVAL_STR, datetime.now())
            return (True, cron)
        except Exception as e:
            logging.critical(f"Invalid CRAWL_INTERVAL '{CRAWL_INTERVAL_STR}': {e}")
            sys.exit(1)

def schedule_next_crawl():
    """
    Schedule the next crawl based on the configured schedule.
    """
    is_cron, value = parse_crawl_schedule()

    if is_cron:
        # Calculate seconds until next cron trigger
        next_run = value.get_next(datetime)
        delay = (next_run - datetime.now()).total_seconds()
        logging.info(f"Next crawl scheduled at {next_run} (in {delay:.0f}s)")
        reactor.callLater(delay, lambda: (
            threading.Thread(target=crawl_groups).start(),
            schedule_next_crawl()
        ))
    else:
        # Just wait n seconds
        logging.info(f"Next crawl scheduled in {value}s")
        reactor.callLater(value, lambda: (
            threading.Thread(target=crawl_groups).start(),
            schedule_next_crawl()
        ))

def fetch_groups_for_uid(uid):
    if CACHE_MODE == "DATABASE":
        return db.get_groups(uid)

    if uid in group_cache:
        return group_cache[uid]

    try:
        conn = get_ldap_conn()
        with conn:
            safe_uid = escape_filter_chars(uid)
            conn.search(GROUP_SEARCH_BASE, f'({GROUP_MEMBER_ATTR}={safe_uid})', attributes=['dn'])
            groups = [entry.entry_dn for entry in conn.entries]
            group_cache[uid] = groups
            return groups
    except Exception as e:
        logging.error(f"Live lookup failed for {uid}: {e}")
        return []

# LDAP Proxy Response Handler
class MemberOfProxy(proxybase.ProxyBase):
    def __init__(self):
        super().__init__()
        self.entry_count = 0

    def handleProxiedResponse(self, response, request, controls):
        if isinstance(response, pureldap.LDAPSearchResultEntry):
            self.entry_count += 1

            # LOOKUP_MAX_USERS <= 0 -> never skip
            if CACHE_MODE == "LIVE" and LOOKUP_MAX_USERS > 0 and self.entry_count > LOOKUP_MAX_USERS:
                if self.entry_count == LOOKUP_MAX_USERS + 1:
                    logging.warning(f"Lookup threshold of {LOOKUP_MAX_USERS} reached. Skipping further group injections for this query.")
                return response

            uid = None
            target_attr = USER_ID_ATTR.lower()
            for attr in response.attributes:
                attr_name = attr[0].decode('utf-8') if isinstance(attr[0], bytes) else attr[0]
                if attr_name.lower() == target_attr:
                    val_raw = list(attr[1])[0]
                    uid = val_raw.decode('utf-8') if isinstance(val_raw, bytes) else val_raw
                    break

            if uid:
                groups = fetch_groups_for_uid(uid)
                if groups:
                    response.attributes.append((MEMBEROF_ATTR.encode('utf-8'), [g.encode('utf-8') for g in groups]))

        elif isinstance(response, pureldap.LDAPSearchResultDone):
            self.entry_count = 0

        return response

class ProxyFactory(protocol.ServerFactory):
    def buildProtocol(self, addr):
        proto = MemberOfProxy()
        def connect_upstream():
            cf = protocol.Factory()
            cf.protocol = LDAPClient
            if UPSTREAM_MODE == "TLS":
                ctx = twisted_ssl.optionsForClientTLS(UPSTREAM_HOST) if UPSTREAM_TLS_VERIFY else twisted_ssl.CertificateOptions(verify=False)
                return endpoints.SSL4ClientEndpoint(reactor, UPSTREAM_HOST, UPSTREAM_PORT, ctx).connect(cf)
            return endpoints.TCP4ClientEndpoint(reactor, UPSTREAM_HOST, UPSTREAM_PORT).connect(cf)

        proto.clientConnector = connect_upstream
        proto.use_tls = (UPSTREAM_MODE == "STARTTLS")
        return proto

# Entry point
if __name__ == '__main__':
    if CACHE_MODE == "DATABASE":
        # Run initial crawl immediately
        threading.Thread(target=crawl_groups).start()
        # Schedule subsequent crawls
        schedule_next_crawl()

    use_inbound_tls = TLS_CERT_FILE and TLS_KEY_FILE and os.path.isfile(TLS_CERT_FILE)

    if use_inbound_tls:
        with open(TLS_CERT_FILE, 'rb') as cf, open(TLS_KEY_FILE, 'rb') as kf:
            cert = twisted_ssl.PrivateCertificate.loadPEM(cf.read() + b'\n' + kf.read())
        logging.warning(f"LDAPS Proxy (Port {LISTEN_PORT}) -> {UPSTREAM_HOST}:{UPSTREAM_PORT} (Mode: {UPSTREAM_MODE}, Cache: {CACHE_MODE})")
        reactor.listenSSL(LISTEN_PORT, ProxyFactory(), cert.options())
    else:
        logging.warning(f"LDAP Proxy (Port {LISTEN_PORT}) -> {UPSTREAM_HOST}:{UPSTREAM_PORT} (Mode: {UPSTREAM_MODE}, Cache: {CACHE_MODE})")
        reactor.listenTCP(LISTEN_PORT, ProxyFactory())

    reactor.run()
