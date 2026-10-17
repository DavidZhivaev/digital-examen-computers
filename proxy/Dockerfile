FROM alpine:3.20

RUN apk add --no-cache squid python3

COPY helpers/de-proxy-auth /usr/local/bin/de-proxy-auth
COPY helpers/de-proxy-policy /usr/local/bin/de-proxy-policy
COPY squid.conf /etc/squid/squid.conf

RUN chmod 0755 /usr/local/bin/de-proxy-auth /usr/local/bin/de-proxy-policy \
    && mkdir -p /etc/squid/policy /var/log/squid \
    && chown -R squid:squid /var/log/squid

ENV DE_POLICY_DIR=/etc/squid/policy
ENV DE_PROXY_SECRET_FILE=/etc/squid/proxy.secret

EXPOSE 3128

CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
