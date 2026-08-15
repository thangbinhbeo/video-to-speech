# 🎙 video-to-speech

> Turn any video or audio into text — 100% local, no API keys, no cloud, no cost. Powered by OpenAI's Whisper model running right on your machine. 

Drop your files in `input/`, run one command, grab your transcripts from `output/`. That's it. That's the whole thing.

---

## ✨ What You Get

For each input file, you'll get **3 output files**:

| File | What it is | Use case |
|------|-----------|----------|
| `*_transcript.txt` | Full text with timestamps + time markers | Skim through a meeting, jump to a specific part |
| `*_plain.txt` | Just the raw text, nothing else | Feed it to ChatGPT, copy-paste it, whatever |
| `*.srt` | Standard subtitle file | Add subtitles to your video in any player |

The transcript file also has **periodic time markers** (every 5 min by default) so you can instantly see where you are:

```
[00:04:52] và cái phần này thì chúng ta sẽ discuss thêm

──────────── ⏱ [00:05:00] ────────────

[00:05:01] okay vậy thì tiếp theo là phần demo
```

---

## 📋 Prerequisites

Before you start, make sure you have these installed:

### 1. Python 3.10+

Check if you have it:
```bash
python --version
```
If not, download from [python.org](https://www.python.org/downloads/) or use your package manager.

### 2. uv (Python package manager)

The blazing fast Python package manager. Install it:

```bash
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. FFmpeg

Whisper needs this to read video/audio files.

```bash
# Windows (pick one)
choco install ffmpeg          # if you use Chocolatey
winget install ffmpeg         # if you use winget
# or just download from https://ffmpeg.org/download.html

# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

Verify it's installed:
```bash
ffmpeg -version
```

---

## 🚀 Setup (one-time)

```bash
# 1. Clone or navigate to this project
cd video-to-speech

# 2. Let uv set up everything (creates venv + installs dependencies)
uv sync
```

That's it. `uv` handles the virtual environment and all the dependencies automatically. No `pip install`, no `venv activate`, nothing.

> **First run heads up**: Whisper will download the AI model (~1.5 GB for `medium`) the first time you run it. This is cached so it only happens once.

---

## 🎬 How to Use

### The Simple Way (recommended)

1. **Drop your video/audio files** into the `input/` folder
2. **Run the command**:
   ```bash
   uv run transcribe
   ```
3. **Grab your results** from the `output/` folder

Done. ✅

### Single File Mode

Don't want to use the `input/` folder? Just point directly to a file:

```bash
uv run transcribe path/to/your/meeting.mp4
```

### More Options

```bash
# Use the most accurate model (slower, GPU recommended)
uv run transcribe --model large-v3

# Force GPU (if you have an NVIDIA GPU with CUDA)
uv run transcribe --device cuda

# Force CPU
uv run transcribe --device cpu

# Change output directory
uv run transcribe --outdir ./my-results

# Auto-detect language (instead of defaulting to Vietnamese)
uv run transcribe --language ""

# Time markers every 10 minutes instead of 5
uv run transcribe --marker-interval 600

# Time markers every 2 minutes
uv run transcribe --marker-interval 120

# Disable time markers
uv run transcribe --marker-interval 0

# Combine options
uv run transcribe --model large-v3 --device cuda --marker-interval 600
```

---

## ⚙️ CLI Reference

```
usage: transcribe [-h] [--model {tiny,base,small,medium,large-v3}]
                  [--language LANGUAGE] [--device DEVICE] [--outdir OUTDIR]
                  [--marker-interval SECONDS]
                  [input]
```

| Argument | Default | Description |
|----------|---------|-------------|
| `input` | `input/` | A file or directory of media files to transcribe |
| `--model` | `medium` | Whisper model size (see table below) |
| `--language` | `Vietnamese` | Audio language. Set to `""` for auto-detect |
| `--device` | auto | `cuda` for GPU, `cpu` for CPU |
| `--outdir` | `output/` | Where to save the results |
| `--marker-interval` | `300` | Seconds between time markers in transcript. `0` to disable |

### Model Comparison

| Model | Size | Speed | Accuracy | Best for |
|-------|------|-------|----------|----------|
| `tiny` | ~75 MB | ⚡⚡⚡⚡ | ★☆☆☆ | Quick & dirty, testing |
| `base` | ~140 MB | ⚡⚡⚡ | ★★☆☆ | Casual use |
| `small` | ~460 MB | ⚡⚡ | ★★★☆ | Good balance |
| `medium` | ~1.5 GB | ⚡ | ★★★★ | **Recommended for Vietnamese** |
| `large-v3` | ~3 GB | 🐌 | ★★★★★ | Best accuracy, needs GPU |

---

## 📁 Project Structure

```
video-to-speech/
├── pyproject.toml                  # project config & dependencies
├── README.md                       # you're reading this rn
├── .python-version                 # Python version for uv
├── .gitignore
├── input/                          # 👈 drop your files here
│   └── meeting.mp4
├── output/                         # 👈 results appear here
│   ├── meeting_transcript.txt
│   ├── meeting_plain.txt
│   └── meeting.srt
└── src/
    └── video_to_speech/
        ├── __init__.py
        └── transcribe.py           # the main script
```

---

## 🤔 FAQ / Troubleshooting

### "It's taking forever"

- On CPU, a 1-hour video with `medium` model can take **30-90 minutes**. That's normal.
- If you have an NVIDIA GPU, use `--device cuda` — it'll be **5-10x faster**.
- For a quick test, try `--model tiny` first.

### "I get an error about ffmpeg"

Make sure ffmpeg is installed and in your PATH:
```bash
ffmpeg -version
```

### "The Vietnamese transcription quality is bad"

- Make sure you're using `medium` or `large-v3` model. The smaller models struggle with Vietnamese.
- Check your audio quality — clean audio = better results.

### "I want to transcribe English / other languages"

```bash
uv run transcribe --language English
uv run transcribe --language Japanese
uv run transcribe --language ""  # let Whisper auto-detect
```

### "Where are the Whisper models downloaded?"

They're cached in `~/.cache/whisper/` (Linux/macOS) or your user cache directory (Windows). Delete that folder to re-download.

---

## 📝 License

MIT — do whatever you want with it. No cap.
