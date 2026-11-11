# The runtime (documentation/runtime.md): it holds the active chats, their agents' processes on sandboxes, and the
# way out. It keeps nothing on disk but its identity. Run it as user 999 with no right, read-only.
#
#   docker build -f runtime/Dockerfile .

FROM python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b

WORKDIR /opt/decentai
COPY runtime/requirements.txt ./runtime-requirements.txt
RUN pip install --no-cache-dir --timeout 60 --retries 5 --require-hashes -r runtime-requirements.txt \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai decentai

COPY shared ./shared
COPY communication/*.py ./communication/
COPY runtime/src ./runtime

USER decentai

EXPOSE 8004 8005
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import ssl, urllib.request; c = ssl.create_default_context(); c.check_hostname = False; c.verify_mode = ssl.CERT_NONE; urllib.request.urlopen('https://127.0.0.1:8004/health', timeout=4, context=c)"]
CMD ["python", "-m", "runtime"]
