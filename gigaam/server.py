import os
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

MODEL_NAME = os.getenv("GIGAAM_MODEL", "v3_e2e_ctc")
HOST = os.getenv("GIGAAM_HOST", "127.0.0.1")
PORT = int(os.getenv("GIGAAM_PORT", "9001"))
DEVICE = os.getenv("GIGAAM_DEVICE", "cpu")
KEEP_ALIVE = os.getenv("GIGAAM_KEEP_ALIVE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}

_model = None
_model_lock = threading.RLock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            import gigaam

            _model = gigaam.load_model(
                MODEL_NAME,
                device=DEVICE,
                fp16_encoder=False,
                use_flash=False,
            )
        return _model


def unload_model() -> None:
    global _model
    with _model_lock:
        _model = None
    try:
        import gc

        import torch

        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if KEEP_ALIVE:
        get_model()
    yield
    if not KEEP_ALIVE:
        unload_model()


app = FastAPI(title="Food GigaAM STT", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str | bool]:
    loaded = _model is not None
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "device": DEVICE,
        "keep_alive": KEEP_ALIVE,
        "loaded": loaded,
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ru"),
) -> dict[str, str]:
    del language  # GigaAM-v3 RU models; kept for API parity with Whisper
    suffix = Path(file.filename or "voice.ogg").suffix or ".ogg"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    wav_path = tmp_path
    converted = False
    try:
        # Prefer wav 16k mono for stable decoding.
        if suffix.lower() != ".wav":
            import subprocess

            wav_path = tmp_path + ".wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    tmp_path,
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    wav_path,
                ],
                check=True,
                capture_output=True,
            )
            converted = True

        with _model_lock:
            model = get_model()
            text = model.transcribe(wav_path)
        if hasattr(text, "text"):
            text = text.text
        text = str(text or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Empty transcript")
        return {"text": text, "model": MODEL_NAME}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        if converted:
            Path(wav_path).unlink(missing_ok=True)
        if not KEEP_ALIVE:
            unload_model()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)
