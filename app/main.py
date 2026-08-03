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
    version="1.2.0"
)

app.include_router(strategy_router)

ASSETS_DIR = Path(__file__).parent / "assets"
MUSIC_DIR = ASSETS_DIR / "music"
LOGO_PATH = ASSETS_DIR / "logo" / "logo.png"

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

FPS = 15
MAX_DURATION_SECONDS = 60  # tope de seguridad para Reels / TikTok / Shorts
WORDS_PER_SUBTITLE_CHUNK = 6

# --- Escena de cierre (marca) ---
OUTRO_SECONDS = 2.5
SLOGAN_TEXT = "Mas facil, con un click."
OUTRO_BG_COLOR = "0x0a0a12"
BRAND_GREEN = "0x39FF14"


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


def build_outro_chain(
    outro_bg_index: int,
    logo_index: Optional[int],
    slogan_file: Path,
) -> str:
    """Escena de cierre de marca: fondo oscuro, logo con fade-in y slogan.
    Se concatena al final del clip principal. No depende de Whisper/libass."""
    parts = [
        f"[{outro_bg_index}:v]trim=duration={OUTRO_SECONDS},"
        f"setpts=PTS-STARTPTS,fps={FPS}[outrobg]"
    ]
    last = "outrobg"

    if logo_index is not None:
        parts.append(
            f"[{logo_index}:v]format=rgba,scale=280:-1,"
            "fade=t=in:st=0:d=0.6:alpha=1[logobig]"
        )
        parts.append(f"[{last}][logobig]overlay=(W-w)/2:(H-h)/2-80[ologo]")
        last = "ologo"

    parts.append(
        f"[{last}]drawtext=fontfile={FONT_PATH}:textfile={slogan_file}:"
        f"fontsize=46:fontcolor={BRAND_GREEN}:borderw=3:bordercolor=black:"
        "x=(w-text_w)/2:y=h*0.64:"
        r"alpha='min(max((t-1.0)/0.6\,0)\,1)'[outrov]"
    )
    return ";".join(parts)


@app.post("/generate-video")
async def generate_video(
    background_tasks: BackgroundTasks,
    image: UploadFile = File(...),
    audio: UploadFile = File(...),
    script_text: Optional[str] = Form(None),
    add_logo: bool = Form(True),
):
    print("IMAGE:", image.filename, image.content_type)
    print("AUDIO:", audio.filename, audio.content_type)

    if not image.filename.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
        raise HTTPException(400, "La imagen debe ser JPEG, PNG o WEBP")
    if not audio.filename.lower().endswith(".mp3"):
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

    print("=" * 60)
    print("AUDIO GUARDADO:", audio_path, "-", audio_path.stat().st_size, "bytes")
    probe = subprocess.run(
        ["ffprobe", "-hide_banner", str(audio_path)],
        capture_output=True, text=True,
    )
    print("FFPROBE RETURN:", probe.returncode)
    print("FFPROBE STDERR:", probe.stderr[-800:])
    print("=" * 60)

    # --- Duración: voz + escena de cierre, con tope de seguridad total ---
    voice_duration = get_audio_duration(audio_path)
    main_duration = min(voice_duration, MAX_DURATION_SECONDS - OUTRO_SECONDS)
    main_duration = max(main_duration, 1.0)
    total_duration = main_duration + OUTRO_SECONDS
    main_frames = max(int(main_duration * FPS), 1)

    slogan_file = tmp_dir / "slogan.txt"
    slogan_file.write_text(SLOGAN_TEXT, encoding="utf-8")

    # --- Clip principal ---
    main_chain = (
        "[0:v]scale=720:1280:force_original_aspect_ratio=increase,"
        "crop=720:1280,"
        f"zoompan=z='min(zoom+0.0015,1.15)':d={main_frames}:s=720x1280:fps={FPS},"
        # zoompan no se detiene solo en el frame 'd': sin este trim, se queda
        # congelado repitiendo el último frame y el concat de abajo nunca
        # llega a la escena de cierre.
        f"trim=start_frame=0:end_frame={main_frames},setpts=PTS-STARTPTS"
    )
    if script_text:
        main_chain += build_subtitle_filters(script_text, main_duration, tmp_dir)
    main_chain += "[mainv]"

    # --- Inputs: imagen, voz, música, [logo], fondo de cierre ---
    inputs = [
        "-loop", "1", "-i", str(image_path),  # 0
        "-i", str(audio_path),                # 1
        "-i", str(music_path),                # 2
    ]
    logo_index = None
    if add_logo and LOGO_PATH.exists():
        inputs += ["-loop", "1", "-i", str(LOGO_PATH)]
        logo_index = 3
    inputs += ["-f", "lavfi", "-i", f"color=c={OUTRO_BG_COLOR}:s=720x1280:r={FPS}"]
    outro_bg_index = 4 if logo_index is not None else 3

    outro_chain = build_outro_chain(outro_bg_index, logo_index, slogan_file)

    filter_complex = (
        main_chain + ";" + outro_chain
        + ";[mainv][outrov]concat=n=2:v=1:a=0[v]"
        + f";[1:a]apad=whole_dur={total_duration:.3f}[voice_p]"
        + ";[2:a]volume=0.18[bg]"
        + ";[voice_p][bg]amix=inputs=2:duration=first:dropout_transition=3[a]"
    )

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{total_duration:.3f}",
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