"""
Transcribe video/audio to text using OpenAI Whisper — runs 100% locally.

Supports single file or batch mode (entire directory).
Outputs: timestamped transcript (.txt), plain text (.txt), subtitles (.srt).
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time

# Force UTF-8 output on Windows (avoids cp1252 encoding errors with emoji)
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


# ---------------------------------------------------------------------------
# Supported media extensions for batch mode
# ---------------------------------------------------------------------------
MEDIA_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv",  # video
    ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".wma",  # audio
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_media_duration(file_path: str) -> float | None:
    """Get total duration (seconds) of a media file via ffprobe.

    Returns None if ffprobe is unavailable or fails.
    """
    try:
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            file_path,
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            info = json.loads(out.stdout)
            return float(info["format"]["duration"])
    except Exception:
        pass
    return None


def format_duration_hms(seconds: float) -> str:
    """Convert seconds to a human-readable duration string like 1h23m45s."""
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    if h > 0:
        return f"{h}h{m:02d}m{s:02d}s"
    return f"{m}m{s:02d}s"


def format_timestamp(seconds: float) -> str:
    """Convert seconds to SRT timestamp format HH:MM:SS,mmm."""
    ms = int((seconds - int(seconds)) * 1000)
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def format_timestamp_readable(seconds: float) -> str:
    """Convert seconds to short timestamp like [00:12:35]."""
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def make_marker_line(seconds: float) -> str:
    """Create a visual separator line with a timestamp marker."""
    ts = format_timestamp_readable(seconds)
    return f"──────────── ⏱ {ts} ────────────"


def print_progress(seg_end: float, total_duration: float, start_wall: float):
    """Print a real-time progress bar to the terminal."""
    pct = min(seg_end / total_duration * 100, 100.0)
    elapsed = time.time() - start_wall
    # Estimate remaining time
    if pct > 0:
        eta = elapsed / pct * (100 - pct)
        eta_str = format_duration_hms(eta)
    else:
        eta_str = "???"

    bar_len = 30
    filled = int(bar_len * pct / 100)
    bar = "█" * filled + "░" * (bar_len - filled)

    pos_str = format_duration_hms(seg_end)
    total_str = format_duration_hms(total_duration)

    print(
        f"\r  ┃{bar}┃ {pct:5.1f}%  "
        f"[{pos_str} / {total_str}]  "
        f"elapsed {format_duration_hms(elapsed)} · ~{eta_str} left   ",
        end="",
        flush=True,
    )


def collect_media_files(directory: str) -> list[str]:
    """Collect all media files from a directory (non-recursive)."""
    files = []
    for entry in sorted(os.listdir(directory)):
        ext = os.path.splitext(entry)[1].lower()
        if ext in MEDIA_EXTENSIONS:
            files.append(os.path.join(directory, entry))
    return files


# ---------------------------------------------------------------------------
# Core transcription
# ---------------------------------------------------------------------------

def transcribe_file(
    model,
    input_file: str,
    outdir: str,
    language: str,
    marker_interval: int,
):
    """Transcribe a single file and write all output formats."""
    print(f"\n{'=' * 60}")
    print(f"📁 File: {os.path.basename(input_file)}")

    # --- Get total duration for progress tracking ---
    total_duration = get_media_duration(input_file)
    if total_duration:
        print(f"⏱  Duration: {format_duration_hms(total_duration)}")
    else:
        print("⚠  Could not detect duration (missing ffprobe?). No progress bar.")

    print(f"🎙  Transcribing...")
    start = time.time()

    # Disable Whisper's built-in verbose output; we show our own progress bar
    transcribe_kwargs = {"verbose": False}
    if language:
        transcribe_kwargs["language"] = language

    # Monkey-patch model.decode to count 30s chunks and update progress bar
    if total_duration:
        _orig_decode = model.decode
        chunk_i = [0]

        def _patched_decode(*a, **kw):
            res = _orig_decode(*a, **kw)
            chunk_i[0] += 1
            seg_end = min(chunk_i[0] * 30, total_duration)
            print_progress(seg_end, total_duration, start)
            return res

        model.decode = _patched_decode
        try:
            result = model.transcribe(input_file, **transcribe_kwargs)
        finally:
            model.decode = _orig_decode  # restore original

        # Final 100% bar
        print_progress(total_duration, total_duration, start)
        print()  # newline after progress bar
    else:
        # No duration info → fall back to Whisper's verbose mode
        transcribe_kwargs["verbose"] = True
        result = model.transcribe(input_file, **transcribe_kwargs)

    elapsed = time.time() - start
    print(f"✅ Done in {format_duration_hms(elapsed)} ({elapsed / 60:.1f} min).")

    # --- Write output files ---
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    os.makedirs(outdir, exist_ok=True)

    # 1) Timestamped transcript (.txt) — with optional time markers
    txt_path = os.path.join(outdir, f"{base_name}_transcript.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        next_marker = marker_interval if marker_interval > 0 else None

        for seg in result["segments"]:
            # Insert time marker if we've crossed the next boundary
            if next_marker is not None:
                while seg["start"] >= next_marker:
                    f.write(f"\n{make_marker_line(next_marker)}\n\n")
                    next_marker += marker_interval

            ts = format_timestamp_readable(seg["start"])
            text = seg["text"].strip()
            f.write(f"{ts} {text}\n")
    print(f"   📝 Transcript (timestamped): {txt_path}")

    # 2) Plain text (.txt) — no timestamps, easy to copy/paste or feed to AI
    plain_txt_path = os.path.join(outdir, f"{base_name}_plain.txt")
    with open(plain_txt_path, "w", encoding="utf-8") as f:
        f.write(result["text"].strip())
    print(f"   📄 Plain text:               {plain_txt_path}")

    # 3) SRT subtitles — standard format for video players
    srt_path = os.path.join(outdir, f"{base_name}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(result["segments"], start=1):
            start_ts = format_timestamp(seg["start"])
            end_ts = format_timestamp(seg["end"])
            text = seg["text"].strip()
            f.write(f"{i}\n{start_ts} --> {end_ts}\n{text}\n\n")
    print(f"   🎬 SRT subtitles:            {srt_path}")

    return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    # Resolve project root (where pyproject.toml lives)
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    default_input = os.path.join(project_root, "input")
    default_output = os.path.join(project_root, "output")

    parser = argparse.ArgumentParser(
        description="Transcribe video/audio to text using OpenAI Whisper (local, free)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  transcribe                          # process all files in input/ -> output/
  transcribe meeting.mp4              # process a single file -> output/
  transcribe --model large-v3         # use the most accurate model (needs GPU)
  transcribe --marker-interval 600    # insert time markers every 10 minutes
  transcribe --marker-interval 0      # disable time markers
        """,
    )
    parser.add_argument(
        "input",
        nargs="?",
        default=default_input,
        help="Path to a media file OR a directory of media files. "
             "Defaults to the 'input/' folder in the project root.",
    )
    parser.add_argument(
        "--model",
        default="medium",
        choices=["tiny", "base", "small", "medium", "large-v3"],
        help="Whisper model size. 'medium' balances speed/accuracy for Vietnamese. "
             "'large-v3' is most accurate but much slower — GPU recommended. "
             "(default: medium)",
    )
    parser.add_argument(
        "--language",
        default="Vietnamese",
        help="Audio language (default: Vietnamese). Set to '' for auto-detection.",
    )
    parser.add_argument(
        "--device",
        default=None,
        help="'cuda' for NVIDIA GPU, 'cpu' to force CPU. Default: auto-detect.",
    )
    parser.add_argument(
        "--outdir",
        default=default_output,
        help="Output directory for transcription results. "
             "Defaults to the 'output/' folder in the project root.",
    )
    parser.add_argument(
        "--marker-interval",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Insert a visual time marker every N seconds in the transcript. "
             "Set to 0 to disable. (default: 300 = every 5 minutes)",
    )
    args = parser.parse_args()

    # --- Determine input files ---
    if os.path.isfile(args.input):
        files_to_process = [args.input]
    elif os.path.isdir(args.input):
        files_to_process = collect_media_files(args.input)
        if not files_to_process:
            print(f"❌ No media files found in: {args.input}")
            print(f"   Supported formats: {', '.join(sorted(MEDIA_EXTENSIONS))}")
            sys.exit(1)
    else:
        print(f"❌ Input not found: {args.input}")
        sys.exit(1)

    print(f"🔍 Found {len(files_to_process)} file(s) to transcribe.")
    if args.marker_interval > 0:
        print(f"📌 Time markers every {format_duration_hms(args.marker_interval)}")
    else:
        print(f"📌 Time markers: disabled")
    print(f"📂 Output directory: {args.outdir}")

    # --- Load Whisper model ---
    import whisper  # import here so "not installed" error is clear after arg parsing

    print(f"\n🔄 Loading model '{args.model}' (first run will download it, may take a few minutes)...")
    load_kwargs = {}
    if args.device:
        load_kwargs["device"] = args.device
    model = whisper.load_model(args.model, **load_kwargs)
    print(f"✅ Model '{args.model}' ready.\n")

    # --- Process files ---
    total_start = time.time()
    success_count = 0

    for i, fpath in enumerate(files_to_process, start=1):
        if len(files_to_process) > 1:
            print(f"\n📋 [{i}/{len(files_to_process)}]", end="")
        try:
            ok = transcribe_file(
                model=model,
                input_file=fpath,
                outdir=args.outdir,
                language=args.language,
                marker_interval=args.marker_interval,
            )
            if ok:
                success_count += 1
        except Exception as e:
            print(f"\n❌ Error processing {fpath}: {e}")

    total_elapsed = time.time() - total_start
    print(f"\n{'=' * 60}")
    print(
        f"🏁 All done! {success_count}/{len(files_to_process)} files transcribed "
        f"in {format_duration_hms(total_elapsed)}."
    )
    print(f"📂 Results saved to: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
