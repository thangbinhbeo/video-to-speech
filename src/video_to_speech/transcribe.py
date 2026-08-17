"""
Transcribe video/audio to text using faster-whisper — runs 100% locally.

Uses CTranslate2 backend for 4-8x faster inference and ~4x less RAM than
openai-whisper. Includes Silero VAD (Voice Activity Detection) to skip
silent segments and prevent hallucination.

Supports single file or batch mode (entire directory).
Outputs: timestamped transcript (.txt), plain text (.txt), subtitles (.srt).
"""

import argparse
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


def filter_hallucination(segments: list[dict], max_repeat: int = 3) -> list[dict]:
    """Clean hallucinated segments using two strategies:

    1. Keyword filter: replace segments matching known Whisper hallucination patterns
       (e.g., "subscribe cho kênh", "đăng ký kênh") with a "[... không nghe rõ ...]"
       placeholder — these are artifacts from YouTube training data.
    2. Repeat filter: replace segments that repeat consecutively > max_repeat times.

    Segments are kept (with replaced text) to preserve the timeline.
    """
    PLACEHOLDER = "[... không nghe rõ ...]"

    # Known hallucination phrases (lowercase, partial match)
    HALLUCINATION_PATTERNS = [
        "subscribe cho kênh",
        "đăng ký kênh",
        "đăng kí cho kênh",
        "không bỏ lỡ những video",
        "ủng hộ kênh của mình",
        "ủng hộ kênh mình",
    ]

    cleaned = []
    repeat_count = 0
    prev_text = None
    keyword_replaced = 0
    repeat_replaced = 0

    for seg in segments:
        text = seg["text"].strip()
        text_lower = text.lower()
        new_seg = dict(seg)  # shallow copy to avoid mutating original

        # Strategy 1: keyword filter
        if any(pattern in text_lower for pattern in HALLUCINATION_PATTERNS):
            new_seg["text"] = PLACEHOLDER
            keyword_replaced += 1
            cleaned.append(new_seg)
            continue

        # Strategy 2: repeat filter
        if text == prev_text:
            repeat_count += 1
        else:
            repeat_count = 1
            prev_text = text

        if repeat_count > max_repeat:
            new_seg["text"] = PLACEHOLDER
            repeat_replaced += 1

        cleaned.append(new_seg)

    total = keyword_replaced + repeat_replaced
    if total:
        print(f"   🧹 Replaced {total} unclear segments with '{PLACEHOLDER}'"
              f" (keyword: {keyword_replaced}, repeat: {repeat_replaced})")

    return cleaned


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

    print(f"🎙  Transcribing (with VAD filtering)...")
    start = time.time()

    # --- faster-whisper transcribe ---
    # VAD (Voice Activity Detection) via Silero: tự phát hiện đoạn im lặng,
    # skip luôn → tránh hallucination + nhanh hơn đáng kể.
    transcribe_kwargs = {
        "language": language if language else None,
        "condition_on_previous_text": False,   # Không dùng text trước làm context → phá vòng lặp hallucination
        "no_speech_threshold": 0.6,            # Ngưỡng phát hiện đoạn không có tiếng nói
        "log_prob_threshold": -1.0,            # Lọc output confidence thấp
        "compression_ratio_threshold": 2.4,    # Loại đoạn lặp lại bất thường
        # --- VAD settings (Silero) ---
        "vad_filter": True,                    # BẬT VAD — killer feature chống hallucination
        "vad_parameters": {
            "min_silence_duration_ms": 500,    # Đoạn im lặng >= 500ms sẽ bị skip
            "speech_pad_ms": 300,              # Thêm 300ms padding quanh đoạn có tiếng nói
            "threshold": 0.5,                  # Ngưỡng VAD (0-1, cao hơn = strict hơn)
        },
    }

    segments_gen, info = model.transcribe(input_file, **transcribe_kwargs)

    print(f"   Detected language: {info.language} (probability: {info.language_probability:.2f})")

    # Iterate segments (generator) and collect results with progress bar
    segments = []
    for seg in segments_gen:
        seg_dict = {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
        }
        segments.append(seg_dict)

        # Update progress bar
        if total_duration:
            print_progress(seg.end, total_duration, start)

    if total_duration:
        print_progress(total_duration, total_duration, start)
        print()  # newline after progress bar

    elapsed = time.time() - start
    print(f"✅ Done in {format_duration_hms(elapsed)} ({elapsed / 60:.1f} min).")

    # --- Post-processing: filter out hallucination loops ---
    cleaned_segments = filter_hallucination(segments, max_repeat=3)
    removed = len(segments) - len(cleaned_segments)
    if removed:
        print(f"   ⚠️  Filtered {removed} repeated segments (likely hallucination)")

    # --- Write output files ---
    base_name = os.path.splitext(os.path.basename(input_file))[0]
    # Each file gets its own subfolder under outdir
    file_outdir = os.path.join(outdir, base_name)
    os.makedirs(file_outdir, exist_ok=True)

    # 1) Timestamped transcript (.txt) — with optional time markers
    txt_path = os.path.join(file_outdir, f"{base_name}_transcript.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        next_marker = marker_interval if marker_interval > 0 else None

        for seg in cleaned_segments:
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
    plain_txt_path = os.path.join(file_outdir, f"{base_name}_plain.txt")
    with open(plain_txt_path, "w", encoding="utf-8") as f:
        f.write(" ".join(seg["text"].strip() for seg in cleaned_segments))
    print(f"   📄 Plain text:               {plain_txt_path}")

    # 3) SRT subtitles — standard format for video players
    srt_path = os.path.join(file_outdir, f"{base_name}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(cleaned_segments, start=1):
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
        description="Transcribe video/audio to text using faster-whisper (local, free)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  transcribe                          # process all files in input/ -> output/
  transcribe meeting.mp4              # process a single file -> output/
  transcribe --model large-v3         # use the most accurate model
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
             "'large-v3' is most accurate and now feasible on CPU thanks to "
             "CTranslate2 + int8 quantization. (default: medium)",
    )
    parser.add_argument(
        "--language",
        default="vi",
        help="Audio language code (default: vi = Vietnamese). "
             "Set to '' for auto-detection.",
    )
    parser.add_argument(
        "--device",
        default="auto",
        help="'cuda' for NVIDIA GPU, 'cpu' to force CPU. Default: auto-detect.",
    )
    parser.add_argument(
        "--compute-type",
        default=None,
        help="Quantization type: 'int8' (fastest, CPU), 'float16' (GPU), "
             "'int8_float16' (GPU balanced). Default: auto (int8 for CPU, float16 for CUDA).",
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

    # --- Load faster-whisper model ---
    from faster_whisper import WhisperModel

    # Auto-select compute type based on device
    device = args.device
    if device == "auto":
        try:
            import ctranslate2
            device = "cuda" if "cuda" in ctranslate2.get_supported_compute_types("cuda") else "cpu"
        except Exception:
            device = "cpu"

    compute_type = args.compute_type
    if compute_type is None:
        compute_type = "float16" if device == "cuda" else "int8"

    print(f"\n🔄 Loading model '{args.model}' (device={device}, compute={compute_type})...")

    # Check if model is already cached to avoid confusing "download" message
    try:
        from huggingface_hub import try_to_load_from_cache
        _cached = try_to_load_from_cache(f"Systran/faster-whisper-{args.model}", "model.bin")
        if _cached is None or isinstance(_cached, str) is False:
            print(f"   ⬇️  First run — downloading model, may take a few minutes...")
    except Exception:
        print(f"   (First run will download the model, may take a few minutes)")

    model = WhisperModel(args.model, device=device, compute_type=compute_type)
    print(f"✅ Model '{args.model}' ready.\n")

    # --- Process files (skip those that already have output) ---
    total_start = time.time()
    success_count = 0
    skipped_count = 0

    for i, fpath in enumerate(files_to_process, start=1):
        base_name = os.path.splitext(os.path.basename(fpath))[0]
        file_outdir = os.path.join(args.outdir, base_name)

        # Check if output folder already has all 3 expected files
        expected_files = [
            os.path.join(file_outdir, f"{base_name}_transcript.txt"),
            os.path.join(file_outdir, f"{base_name}_plain.txt"),
            os.path.join(file_outdir, f"{base_name}.srt"),
        ]
        if all(os.path.isfile(f) for f in expected_files):
            skipped_count += 1
            print(f"\n⏭  [{i}/{len(files_to_process)}] Skipping '{base_name}' — output folder already exists")
            continue

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
        f"({skipped_count} skipped) "
        f"in {format_duration_hms(total_elapsed)}."
    )
    print(f"📂 Results saved to: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
