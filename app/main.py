import random
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

app = FastAPI(
    title="Nexa Media Engine",
    version="1.0.0"
)

MUSIC_DIR = Path(__file__).parent / "assets" / "music"


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


@app.post("/generate-video")
async def generate_video(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
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

    filter_complex = (
        "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "zoompan=z='min(zoom+0.0012,1.15)':d=125:s=1080x1920:fps=25[v];"
        "[2:a]volume=0.18[bg];"
        "[1:a][bg]amix=inputs=2:duration=first:dropout_transition=3[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-i", str(audio_path),
        "-i", str(music_path),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0 or not output_path.exists():
        cleanup_dir(tmp_dir)
        raise HTTPException(500, f"Error generando video: {result.stderr[-800:]}")

    background_tasks.add_task(cleanup_dir, tmp_dir)

    return FileResponse(
        path=output_path,
        media_type="video/mp4",
        filename="video.mp4",
    )