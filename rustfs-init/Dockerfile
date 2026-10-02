# The RustFS CLI (rc), for bootstrapping the object store.
#
# rc is not in the RustFS image, and MinIO's mc — what minio-init used — is no
# longer distributed at all (the image is gone from Docker Hub and quay, and
# dl.min.io answers 410). The binary is pinned by version and checksum: this
# container holds the store's root credentials.
FROM debian:stable-slim

ARG RC_VERSION=0.1.36
ARG RC_SHA256=4a8128911ccad4e7b481f26635a4cfd1ec064412210526e57ad2c748d356f3b7

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl jq \
 && curl -fsSL -o /tmp/rc.tgz "https://github.com/rustfs/cli/releases/download/v${RC_VERSION}/rustfs-cli-linux-amd64-v${RC_VERSION}.tar.gz" \
 && echo "${RC_SHA256}  /tmp/rc.tgz" | sha256sum -c - \
 && tar -xzf /tmp/rc.tgz -C /tmp \
 && install -m 0755 "$(find /tmp -type f -name rc -perm -u+x | head -1)" /usr/local/bin/rc \
 && rm -rf /tmp/* /var/lib/apt/lists/*

COPY bootstrap.sh /bootstrap.sh
ENTRYPOINT ["/bin/sh", "/bootstrap.sh"]
