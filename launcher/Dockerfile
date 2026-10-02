# The launcher: a container that starts the platform's containers.
#
# It needs the engine and nothing else of the machine it runs on, so it
# is the same on Windows, macOS and Linux, and it is updated the way the
# platform is: by pulling an image. The engine's own command line is in
# it, which Docker and Podman both answer.
#
# Build context is the REPOSITORY ROOT: docker build -f launcher/Dockerfile .
FROM docker:27-cli

RUN apk add --no-cache python3 py3-cryptography

WORKDIR /launcher
COPY launcher/compose.yml ./compose.yml
COPY launcher/keys ./keys
# The release it was built with, and its signature when it has one.
COPY launcher/release.json* ./
COPY launcher/launcher ./launcher

# Where its releases are published. A published launcher is built with
# the address and reads the current release there, believed for its
# signature; a build of one's own has none and installs the file above.
ARG RELEASE_URL=""
ENV DECENTAI_RELEASE_URL=${RELEASE_URL}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "-m", "launcher"]
CMD ["status"]
