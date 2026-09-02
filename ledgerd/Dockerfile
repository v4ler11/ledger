# ledgerd: commits *.bean/*.jsonl changes hourly (as
# "<dd-mm-yyyy>-<hh>"), squashes each day into one "<dd-mm-yyyy>" commit
# at 23:59 (including leftover hourly commits from previous days), and
# pushes to the private repo over SSH.
#
# Build:   docker build -t ledgerd .
# Run:     docker run -d --name ledgerd --restart unless-stopped \
#            -e REPO_URL=ssh://git@ssh.git.valerii.casa/valerii/finances.git \
#            -e TZ=Europe/Kyiv \
#            -v ~/.ssh/id_rsa:/root/.ssh/id_rsa:ro \
#            -v "$PWD":/repo \
#            ledgerd
#
# Notes:
#   * Mount ~/.ssh/id_rsa read-only; the key must be allowed to push.
#     The read-only key is copied to a writable path with mode 0600 at
#     startup, so loose host-side key permissions (e.g. 644) never trigger
#     OpenSSH's "UNPROTECTED PRIVATE KEY FILE" refusal.
#   * Pass TZ matching your local time so day/hour boundaries align.
#   * Only files matching FILE_PATTERNS (default "*.bean *.jsonl") are
#     ever staged by the writer. recurring.json is NOT matched by
#     default; add it via -e FILE_PATTERNS if it should be tracked.

FROM alpine:3.20

RUN apk add --no-cache git openssh-client python3 \
 && mkdir -p /root/.ssh && chmod 700 /root/.ssh

COPY writer.py /usr/local/bin/writer.py
RUN chmod +x /usr/local/bin/writer.py

WORKDIR /repo

ENV REPO_DIR=/repo \
    BRANCH=main \
    FILE_PATTERNS="*.bean *.jsonl" \
    SSH_KEY_PATH=/root/.ssh/id_rsa \
    TICK_SLEEP=30

ENTRYPOINT ["/usr/local/bin/writer.py"]