FROM ubuntu:24.04

# Ubuntu 24.04 ships Asterisk 20 (LTS) in the `universe` component, which is
# already enabled on the default ubuntu:24.04 image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      asterisk \
      asterisk-core-sounds-en \
      gettext-base \
      ca-certificates \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

COPY etc/ /etc/asterisk-templates/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 5060/udp 8089/tcp 10000-10100/udp

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["asterisk", "-fvvvc"]
