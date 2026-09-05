import logging
import os
import tempfile
import threading
import time
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
CACHE_DIR = os.getenv("GIGAAM_CACHE", "").strip() or None

logger = logging.getLogger("uvicorn.error")

_model = None
_model_lock = threading.RLock()


def get_model():
    global _model
    with _model_lock:
        if _model is None:
            import gigaam

            load_kw = dict(
                device=DEVICE,
                fp16_encoder=False,
                use_flash=False,
            )
            if CACHE_DIR:
                load_kw["download_root"] = CACHE_DIR
            _model = gigaam.load_model(MODEL_NAME, **load_kw)
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
        "cache": CACHE_DIR or "~/.cache/gigaam",
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str = Form("ru"),
) -> dict[str, str | float]:
    del language  # GigaAM-v3 RU models; kept for API parity with Whisper
    suffix = Path(file.filename or "voice.ogg").suffix or ".ogg"
    t_all = time.perf_counter()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    wav_path = tmp_path
    converted = False
    ffmpeg_s = 0.0
    infer_s = 0.0
    try:
        # Prefer wav 16k mono for stable decoding.
        if suffix.lower() != ".wav":
            import subprocess

            wav_path = tmp_path + ".wav"
            t_ff = time.perf_counter()
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
            ffmpeg_s = time.perf_counter() - t_ff
            converted = True

        with _model_lock:
            model = get_model()
            t_inf = time.perf_counter()
            text = model.transcribe(wav_path)
            infer_s = time.perf_counter() - t_inf
        if hasattr(text, "text"):
            text = text.text
        text = str(text or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="Empty transcript")
        total_s = time.perf_counter() - t_all
        logger.info(
            "TIMING gigaam ffmpeg=%.2fs infer=%.2fs total=%.2fs chars=%s",
            ffmpeg_s,
            infer_s,
            total_s,
            len(text),
        )
        return {
            "text": text,
            "model": MODEL_NAME,
            "ffmpeg_s": round(ffmpeg_s, 3),
            "infer_s": round(infer_s, 3),
            "total_s": round(total_s, 3),
        }
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
