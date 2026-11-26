"""Install the pinned offline Chinese/English Kokoro voice pack."""
import argparse
import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path


MODEL = "kokoro-int8-multi-lang-v1_1"
URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/{MODEL}.tar.bz2"
SHA256 = "259c9c8bc906d6078e4e0570421885dabb68b539c2f970fcbb890a58eeaf3e9c"
DEFAULT_DIR = Path(__file__).resolve().parents[1] / "personal" / "models" / MODEL
REQUIRED = (
    "model.int8.onnx", "voices.bin", "tokens.txt", "lexicon-zh.txt", "lexicon-us-en.txt",
    "phone-zh.fst", "date-zh.fst", "number-zh.fst", "espeak-ng-data/phondata",
)


def complete_pack(directory: Path) -> bool:
    return all((directory / name).is_file() and (directory / name).stat().st_size > 0 for name in REQUIRED)


def install(destination: Path, archive: Path | None) -> None:
    marker = destination / ".archive-sha256"
    if destination.exists():
        if (marker.is_file() and marker.read_text().strip() == SHA256
                and complete_pack(destination)):
            return
        raise SystemExit(f"Model directory already exists. Choose an empty destination: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".voice-install-") as temp:
        staging = Path(temp)
        if archive is None:
            archive = staging / "model.tar.bz2"
            print("Downloading the offline voice pack (147 MB)...", flush=True)
            with urllib.request.urlopen(URL, timeout=90) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        with archive.open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != SHA256:
                raise SystemExit("Voice archive checksum mismatch. Download the pinned archive again.")
        with tarfile.open(archive, "r:bz2") as package:
            package.extractall(staging, filter="data")
        unpacked = staging / MODEL
        if not complete_pack(unpacked):
            raise SystemExit("The voice pack is missing required model files.")
        (unpacked / marker.name).write_text(SHA256 + "\n", encoding="ascii")
        unpacked.rename(destination)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, help="Use a previously downloaded release archive")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args()
    install(args.destination.resolve(), args.archive)
    print(f"Offline voice pack ready: {args.destination.resolve()}")
