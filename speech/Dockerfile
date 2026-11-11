# The speech container: speech to text and text to speech from models
# that run here (speech/service.py). Built from the repository root:
#   docker build -f speech/Dockerfile .
#
# The image carries the two engines and no model. Models are fetched
# once, into the volume at /data/models, from the pinned list in
# speech/catalogue.py.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

WORKDIR /opt/decentai
COPY speech/requirements.txt ./speech/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r speech/requirements.txt \
    && groupadd --system --gid 999 decentai \
    && useradd --system --uid 999 --gid decentai --create-home decentai \
    && mkdir -p /data/models \
    && chown decentai:decentai /data/models

# The code, last: the only layer a change to it rebuilds.
COPY speech ./speech

ENV SPEECH_MODELS_DIR=/data/models
USER decentai
EXPOSE 8004
CMD ["python", "-m", "speech.service"]
