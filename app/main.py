import random
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from app.strategy import router as strategy_router

app = FastAPI(
    title="Nexa Media Engine",
    version="1.1.0"
)

app.include_router(strategy_router)

ASSETS_DIR = Path(__file__).parent / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
LOGO_PATH = ASSETS_DIR / "logo" / "logo.png"

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

FPS = 15
MAX_DURATION_SECONDS = 60  # tope de seguridad para Reels / TikTok / Shorts
WORDS_PER_SUBTITLE_CHUNK = 6


@app.get("/")
async def root():
    return {
        "service": "Nexa Media Engine",
        "status": "running"
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


def cleanup_dir(path: Path):
    shutil.rmtree(path, ignore_errors=True)


def get_audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise HTTPException(500, f"No se pudo leer la duración del audio: {result.stderr[-400:]}")


def build_subtitle_filters(script_text: str, duration: float, tmp_dir: Path) -> str:
    """Divide el guion en fragmentos cortos y genera un drawtext por fragmento,
    repartidos de forma proporcional sobre la duración real del audio.
    No depende de libass/Whisper: liviano en memoria para el plan Free."""
    words = script_text.split()
    if not words:
        return ""

    chunks = [
        " ".join(words[i:i + WORDS_PER_SUBTITLE_CHUNK])
        for i in range(0, len(words), WORDS_PER_SUBTITLE_CHUNK)
    ]
    seg_duration = duration / len(chunks)

    filters = []
    for i, chunk in enumerate(chunks):
        chunk_file = tmp_dir / f"sub_{i}.txt"
        chunk_file.write_text(chunk, encoding="utf-8")
        start = i * seg_duration
        end = (i + 1) * seg_duration
        filters.append(
            f"drawtext=fontfile={FONT_PATH}:textfile={chunk_file}:"
            "fontsize=48:fontcolor=white:borderw=4:bordercolor=black@0.8:"
            "line_spacing=6:x=(w-text_w)/2:y=h-260:"
            f"enable='between(t,{start:.3f},{end:.3f})'"
        )
    return "," + ",".join(filters)


@app.post("/generate-video")
async def generate_video(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
    script_text: Optional[str] = Form(None),
    add_logo: bool = Form(True),
):
    if image.content_type not in ("image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "La imagen debe ser JPEG, PNG o WEBP")
    if audio.content_type not in ("audio/mpeg", "audio/mp3"):
        raise HTTPException(400, "El audio debe ser MP3")

    tracks = list(MUSIC_DIR.glob("*.mp3"))
    if not tracks:
        raise HTTPException(500, "No hay pistas de música en app/assets/music/")
    music_path = random.choice(tracks)

    job_id = uuid.uuid4().hex
    tmp_dir = Path(tempfile.gettempdir()) / job_id
    tmp_dir.mkdir(parents=True, exist_ok=True)

    image_path = tmp_dir / "image.jpg"
    audio_path = tmp_dir / "voice.mp3"
    output_path = tmp_dir / "video.mp4"

    with image_path.open("wb") as f:
        f.write(await image.read())
    with audio_path.open("wb") as f:
        f.write(await audio.read())

    # --- Duración real de la voz, con tope de seguridad ---
    voice_duration = get_audio_duration(audio_path)
    duration = min(voice_duration, MAX_DURATION_SECONDS)
    total_frames = max(int(duration * FPS), 1)

    video_chain = (
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        f"zoompan=z='min(zoom+0.0015,1.15)':d={total_frames}:s=720x1280:fps={FPS}"
    )

    # --- Subtítulos (opcional, si n8n manda el guion) ---
    if script_text:
        video_chain += build_subtitle_filters(script_text, duration, tmp_dir)
    video_chain += "[subbed]"

    use_logo = add_logo and LOGO_PATH.exists()

    inputs = [
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-i", str(music_path),
    ]
    if use_logo:
        inputs += ["-loop", "1", "-i", str(LOGO_PATH)]
        video_chain += ";[3:v]scale=150:-1[logo];[subbed][logo]overlay=W-w-24:H-h-40:shortest=1[v]"
    else:
        video_chain += ";[subbed]copy[v]"

    filter_complex = (
        video_chain
        + ";[2:a]volume=0.18[bg]"
        + ";[1:a][bg]amix=inputs=2:duration=first:dropout_transition=3[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{duration:.3f}",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not output_path.exists():
        cleanup_dir(tmp_dir)
        raise HTTPException(500, f"Error generando video: {result.stderr[-1200:]}")

    background_tasks.add_task(cleanup_dir, tmp_dir)

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename="video.mp4",
    )
