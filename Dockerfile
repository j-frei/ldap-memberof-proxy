FROM python:3.14-alpine

# Install build dependencies for pyOpenSSL (LDAP clients for healthchecks)
RUN apk add --no-cache build-base libffi-dev openssl-dev openldap-clients

# Create ldap user
RUN addgroup -g 1000 ldap && \
    adduser -D -u 1000 -G ldap ldap
WORKDIR /app
COPY requirements.txt .

# Install and remove unused stuff again
RUN pip install --no-cache-dir -r requirements.txt && \
    apk del build-base libffi-dev openssl-dev

COPY proxy.py .
RUN chown -R ldap:ldap /app

USER ldap
CMD ["python", "-u", "proxy.py"]

# Note: Container runs as non-root (UID 1000), so cannot bind to ports <=1024
# Use ports >1024 and map to privileged ports on host if needed
EXPOSE 3890 6360
