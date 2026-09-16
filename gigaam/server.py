import logging
import os
import shutil
import subprocess
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
# GigaAM transcribe() rejects wav longer than 25s; official longform needs pyannote.
CHUNK_SEC = float(os.getenv("GIGAAM_CHUNK_SEC", "24"))

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


def _ffmpeg_failed(proc: subprocess.CompletedProcess, what: str) -> HTTPException:
    err = (proc.stderr or b"").decode("utf-8", "replace")[-800:]
    logger.error("%s rc=%s: %s", what, proc.returncode, err)
    return HTTPException(
        status_code=500,
        detail=f"{what} (rc={proc.returncode}): {err.strip()[-400:]}",
    )


def _wav_duration_sec(path: str) -> float | None:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _result_text(result: object) -> str:
    if hasattr(result, "text"):
        return str(getattr(result, "text") or "").strip()
    segments = getattr(result, "segments", None)
    if segments:
        return " ".join(
            str(getattr(seg, "text", "") or "").strip() for seg in segments
        ).strip()
    return str(result or "").strip()


def _transcribe_short(model, wav_path: str) -> str:
    return _result_text(model.transcribe(wav_path))


def _transcribe_chunked(model, wav_path: str) -> str:
    chunk_dir = tempfile.mkdtemp(prefix="gigaam-chunks-")
    pattern = str(Path(chunk_dir) / "chunk_%03d.wav")
    try:
        split = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-i",
                wav_path,
                "-f",
                "segment",
                "-segment_time",
                str(CHUNK_SEC),
                "-ac",
                "1",
                "-ar",
                "16000",
                pattern,
            ],
            capture_output=True,
        )
        if split.returncode != 0:
            raise _ffmpeg_failed(split, "ffmpeg split")
        chunks = sorted(Path(chunk_dir).glob("chunk_*.wav"))
        if not chunks:
            raise RuntimeError("ffmpeg produced no audio chunks")
        logger.info("GigaAM longform chunks=%s chunk_sec=%s", len(chunks), CHUNK_SEC)
        parts: list[str] = []
        for i, chunk in enumerate(chunks, start=1):
            t0 = time.perf_counter()
            part = _transcribe_short(model, str(chunk))
            logger.info(
                "GigaAM chunk %s/%s %.2fs chars=%s",
                i,
                len(chunks),
                time.perf_counter() - t0,
                len(part),
            )
            if part:
                parts.append(part)
        return " ".join(parts)
    finally:
        shutil.rmtree(chunk_dir, ignore_errors=True)


def transcribe_wav(model, wav_path: str) -> str:
    duration = _wav_duration_sec(wav_path)
    if duration is not None and duration > CHUNK_SEC:
        logger.info("GigaAM wav duration=%.1fs, using chunks", duration)
        return _transcribe_chunked(model, wav_path)
    try:
        return _transcribe_short(model, wav_path)
    except ValueError as exc:
        if "longform" not in str(exc).lower() and "Too long" not in str(exc):
            raise
        logger.info("GigaAM transcribe rejected length, using chunks: %s", exc)
        return _transcribe_chunked(model, wav_path)


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
        raw = await file.read()
        tmp.write(raw)
        tmp_path = tmp.name
    logger.info("GigaAM upload bytes=%s suffix=%s", len(raw), suffix)
    if not raw:
        Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="Empty audio file")
    wav_path = tmp_path
    converted = False
    ffmpeg_s = 0.0
    infer_s = 0.0
    try:
        # Prefer wav 16k mono for stable decoding.
        if suffix.lower() != ".wav":
            wav_path = tmp_path + ".wav"
            t_ff = time.perf_counter()
            converted_run = subprocess.run(
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
                capture_output=True,
            )
            ffmpeg_s = time.perf_counter() - t_ff
            if converted_run.returncode != 0:
                raise _ffmpeg_failed(converted_run, "ffmpeg failed")
            converted = True

        with _model_lock:
            model = get_model()
            t_inf = time.perf_counter()
            text = transcribe_wav(model, wav_path)
            infer_s = time.perf_counter() - t_inf
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
        logger.exception("GigaAM transcribe failed")
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
