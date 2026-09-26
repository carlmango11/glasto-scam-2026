FROM ghcr.io/sagernet/sing-box:v1.12.12 AS singbox
FROM alpine:3.22
RUN apk add --no-cache nftables iproute2 ca-certificates curl bind-tools python3
COPY --from=singbox /usr/local/bin/sing-box /usr/local/bin/sing-box
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
