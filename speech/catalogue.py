"""The models the container may fetch, each pinned to a revision and every file to its size and digest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

#: Where the files are published; SPEECH_MODEL_HOST names a mirror with the same paths.
PUBLISHED_AT = "https://huggingface.co"


@dataclass(frozen=True)
class ModelFile:
    #: its path in the publisher's repository
    path: str
    size: int
    sha256: str

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


@dataclass(frozen=True)
class Model:
    """A folder of files, of use only when every one of them is there."""

    #: the folder it is kept in, under the models' directory
    key: str
    repository: str
    revision: str
    files: Tuple[ModelFile, ...]

    @property
    def size(self) -> int:
        return sum(file.size for file in self.files)

    def address(self, file: ModelFile, host: str = PUBLISHED_AT) -> str:
        return (f"{host.rstrip('/')}/{self.repository}/resolve/"
                f"{self.revision}/{file.path}")


#: Speech to text: Whisper small, every language in one model, for CTranslate2.
WHISPER = Model(
    key="whisper-small",
    repository="Systran/faster-whisper-small",
    revision="536b0662742c02347bc0e980a01041f333bce120",
    files=(
        ModelFile("config.json", 2370,
                  "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828"),
        ModelFile("model.bin", 483546902,
                  "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671"),
        ModelFile("tokenizer.json", 2203239,
                  "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"),
        ModelFile("vocabulary.txt", 459861,
                  "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"),
    ),
)

_VOICES_REPOSITORY = "rhasspy/piper-voices"
_VOICES_REVISION = "c10ece1aade47bb51c153c893d14e5bf8e5b7117"


def _voice(key: str, folder: str, onnx: tuple, described: tuple) -> Model:
    """A voice is two files, the model and its settings, each (size, digest)."""
    return Model(
        key=key, repository=_VOICES_REPOSITORY, revision=_VOICES_REVISION,
        files=(
            ModelFile(f"{folder}/{key}.onnx", *onnx),
            ModelFile(f"{folder}/{key}.onnx.json", *described),
        ))


#: Text to speech: one Piper voice a language. Their recordings' terms: documentation/operating.md#7-speech.
VOICES: Dict[str, Model] = {
    "en": _voice(
        "en_US-ljspeech-medium", "en/en_US/ljspeech/medium",
        (63531379,
         "6f52a751e2349abe7a76735eb09dc1875298c77ea2342ffd2fef79ff81b87f22"),
        (4972,
         "141d612cc0a95ed7efc1ca936b845c2364967f2e9217c5dbfcf69fc4d6c65860")),
    "ar": _voice(
        "ar_JO-kareem-medium", "ar/ar_JO/kareem/medium",
        (63201294,
         "9e95cab07b679da603bba17c4dec7ab3111320571964ee95c0379603c086491e"),
        (5024,
         "ea6d9b9d9076dbdb6bf5c98c6a141ef154959d2359709b37855727964e7d6c4d")),
}

#: The two things a deployment turns on, and what each needs fetched.
TRANSCRIPTION = "transcription"
SPEECH = "speech"
NEEDS: Dict[str, Tuple[Model, ...]] = {
    TRANSCRIPTION: (WHISPER,),
    SPEECH: tuple(VOICES.values()),
}
