FROM debian:bookworm-slim
ARG TARGETARCH
ENV DEBIAN_FRONTEND=noninteractive DISPLAY=:1 HOME=/home/browser
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg xfce4-session xfwm4 xfce4-panel xfdesktop4 \
    thunar xterm dbus-x11 tigervnc-standalone-server tigervnc-common \
    novnc websockify tini fonts-liberation fonts-noto-color-emoji \
    x11-utils procps dnsutils \
    && if [ "$TARGETARCH" = "amd64" ]; then \
      curl -fsSL https://dl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /usr/share/keyrings/google.gpg; \
      echo 'deb [arch=amd64 signed-by=/usr/share/keyrings/google.gpg] https://dl.google.com/linux/chrome/deb/ stable main' > /etc/apt/sources.list.d/google.list; \
      apt-get update && apt-get install -y --no-install-recommends google-chrome-stable; \
    else apt-get install -y --no-install-recommends chromium; fi \
    && rm -rf /var/lib/apt/lists/*
RUN groupadd -g 1000 browser && useradd -m -u 1000 -g 1000 -s /bin/bash browser \
    && mkdir -p /home/browser/.config /etc/opt/chrome/policies/managed /etc/chromium/policies/managed \
    && chown -R 1000:1000 /home/browser
COPY policies.json /etc/opt/chrome/policies/managed/fleet.json
COPY policies.json /etc/chromium/policies/managed/fleet.json
COPY start.sh launch-browser.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/start.sh /usr/local/bin/launch-browser.sh
USER 1000:1000
WORKDIR /home/browser
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "/usr/local/bin/start.sh"]
