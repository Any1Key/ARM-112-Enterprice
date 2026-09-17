FROM ubuntu:24.04
RUN apt-get update && apt-get install -y --no-install-recommends asterisk asterisk-modules ca-certificates && rm -rf /var/lib/apt/lists/*
COPY pjsip.conf extensions.conf http.conf rtp.conf modules.conf confbridge.conf /etc/asterisk/
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
