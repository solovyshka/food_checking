import os
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

MODEL_NAME = os.getenv("WHISPER_MODEL", "small")
MODEL_DIR = os.getenv("WHISPER_MODEL_DIR", "/opt/food_checking/whisper_models")
HOST = os.getenv("WHISPER_HOST", "127.0.0.1")
PORT = int(os.getenv("WHISPER_PORT", "9000"))

app = FastAPI(title="Food Whisper Service")
_model: WhisperModel | None = None
_model_lock = threading.Lock()


def get_model() -> WhisperModel:
    global _model
    with _model_lock:
        if _model is None:
            Path(MODEL_DIR).mkdir(parents=True, exist_ok=True)
            _model = WhisperModel(
                MODEL_NAME,
                device="cpu",
                compute_type="int8",
                download_root=MODEL_DIR,
            )
        return _model


def unload_model() -> None:
    global _model
    with _model_lock:
        _model = None


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ru"),
) -> dict[str, str]:
    suffix = Path(file.filename or "voice.ogg").suffix or ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        model = get_model()
        segments, _info = model.transcribe(tmp_path, language=language, beam_size=5)
        text = " ".join(segment.text.strip() for segment in segments).strip()
        if not text:
            raise HTTPException(status_code=422, detail="Empty transcript")
        return {"text": text}
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        unload_model()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
