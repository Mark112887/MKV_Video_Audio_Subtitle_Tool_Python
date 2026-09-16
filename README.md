# MKV Video Audio & Subtitle Tool — Python

A desktop application for processing MKV video files — individually or in batch — changing display aspect ratio, removing subtitles and closed captions, and filtering audio tracks — all without re-encoding. Powered by mkvtoolnix for fast, lossless container operations. Built with Python and tkinter.

<div align="center">

:rocket: **Quick Start**

:arrow_down: Download the latest release → launch `MKV Video Audio & Subtitle Tool.exe` → drop your MKV files → click **Process**. No installation or setup required.

</div>

---

## :bulb: Purpose

This tool is designed for users who need to quickly remux MKV files to:

- :art: Set a specific display aspect ratio (DAR) — e.g. `16:9`, `4:3`, `2.35:1` — so media players render the video with the correct pixel aspect.
- :loud_sound: Remove embedded subtitles that clutter the playback interface.
- 💬 Strip embedded closed captions (CEA-608/708) without re-encoding.
- :musical_note: Keep only one audio track (useful when a file contains multiple languages and you want to strip extras).
- :recycle: Delete original source files after successful processing, freeing up disk space automatically.

Everything runs locally on your machine — **no re-encoding** means processing is fast and quality lossless.

---

## :sparkles: Features

### Core Capabilities

| Feature | Description |
|---------|-------------|
| :art: **Aspect Ratio Conversion** | Set any display aspect ratio (DAR) using the `W:H` or `W/H` format (e.g., `16:9`, `4/3`, `2.35:1`). The tool writes `display-width`, `display-height`, and `display-unit` metadata into the MKV container via `mkvpropedit`. |
| :loud_sound: **Subtitle Removal** | Strip all subtitle tracks from one or more MKV files with a single checkbox. Uses mkvmerge's `-S` flag to remove subtitles during remuxing. |
| 💬 **Closed Caption Removal** | Strip embedded closed captions (CEA-608/708) from the video stream independently of subtitle removal. Uses ffmpeg's bitstream filter to strip type-6 NAL units — no re-encoding required. |
| :recycle: **Delete Original Files** | Check a box before processing and the original source file(s) are deleted after successful remuxing, freeing up disk space. Available in both Batch and Individual modes with per-file granularity. |
| :musical_note: **Audio Track Filtering** | Keep only a single audio track while discarding all others. A dropdown auto-populates after loading an MKV file, showing each track with its full language name (e.g., "Track #2 (English)") and track number for easy identification. Selecting "Keep All" leaves every audio track untouched. |
| :repeat: **Batch Processing** | Drag-and-drop an entire folder of MKV files (or a single file) and process them all in one go. The progress bar shows live percentage updates as each file completes. |
| :zap: **Smart Step Skipping** | Before processing, the tool checks each file to see what operations are actually needed. If a file already has the desired aspect ratio or doesn't have subtitles to remove, that step is skipped entirely — no unnecessary work is done. |
| :arrow_right: **Per-File Override Mode** | Switch to "Individual" mode to customize DAR, audio track, and subtitle settings for each file independently. Click any file name in the list to load its saved settings; toggle the Batch/Individual switch to revert to global defaults. |

### :computer: User Interface

- :black_heart: **Dark theme** — easy on the eyes for extended use.
- :clipboard: **Drag-and-drop support** — drop MKV files or folders directly onto the blue drop zone at the top of the window.
- :floppy_disk: **Persistent output folder** — remembers your last chosen output location across launches.
- :bar_chart: **Progress feedback** — a blue animated progress bar with a percentage overlay tracks processing status at the file and overall level.
- :point_right: **Clickable file list** — each entry displays a color-coded status tag (green = custom per-file settings, blue = global default, yellow-green highlight = selected).
- :information_desk_person: **File info bar** — after dropping files, a status line shows your current aspect ratio, whether subtitles or closed captions were detected, and whether all required binaries are available.


### :gear: Technical Details

| Detail | Description |
|--------|-------------|
| :wrench: **Backend** | Uses mkvtoolnix utilities (mkvmerge, mkvpropedit, mkvinfo) and FFmpeg for bitstream filtering — all bundled with the app so nothing extra to install. |
| :construction: **Processing Pipeline** | Each file is first scanned to see what operations are needed (DAR check, subtitle/CC presence). Only the necessary steps run: Step 1: mkvmerge remuxes your content with the chosen filters (audio track deletion, subtitle removal). Step 1b: If CC removal is enabled and closed captions were found, ffmpeg strips them via bitstream filter. Step 2: mkvpropedit writes display aspect ratio metadata and enables the remaining audio track flag. |

---

## :fire: Requirements

- :desktop: **Operating System:** Windows 10 or later (64-bit)
- :snake: **Python 3.11+** — only needed if building from source, not for running the pre-built EXE.
- :package: **Pywin32** (`pip install pywin32`) — required for drag-and-drop support at runtime.
- The three mkvtoolnix executables are bundled with the app and do **not** need to be installed separately.

### :construction_worker: From Source (Build Instructions)

```powershell
# 1. Clone or download this repository
# 2. Install dependencies
pip install pyinstaller pywin32

# 3. Build the standalone executable
python -m PyInstaller --clean MKV_Video_Audio_Subtitle_Tool.spec
```

The compiled application will be in `dist/MKV Video Audio & Subtitle Tool.exe`.

---

## :books: How to Use

### :tada: Quick Start (Pre-Built EXE)

1. :zap: **Launch** `MKV Video Audio & Subtitle Tool.exe`.
2. :clipboard: **Drop MKV files** — drag any MKV file(s) or an entire folder onto the blue drop zone at the top, or click **Browse**.
3. :gear: **Choose global settings:**
   - :art: **Desired Aspect Ratio** — type a ratio like `16:9` (default), `4:3`, or `2.35:1`.
   - :musical_note: **Audio Track to Keep** — select "Keep All" (default) or pick a specific track from the dropdown. Tracks show their full language name (e.g., "Track #2 (English)") for easy identification.
   - :loud_sound: **Remove Subtitles** — check the box if you want subtitles stripped.
   - 💬 **Remove Closed Captions** — check the box to strip embedded closed captions (CEA-608/708) from the video stream.
   - :recycle: **Delete Original Files** — check the box to remove source files after successful processing (per-file in Individual mode).

   Once you drop files, the status line just below the file list automatically shows what was detected for your first file — its current aspect ratio, whether it has subtitles or closed captions. The tool will only perform operations that are actually needed; if a file already matches your settings, that step is skipped entirely.
4. :floppy_disk: **Choose output location** (optional) — click **Output Dir** to set where processed files will be saved. Defaults to a `Processed Files` folder next to the executable.
5. :triangular_flag_on_post: **Click Process** — the batch job begins. Watch progress in the panel below.

#### :page_facing_up: Default Output Folder

When you first launch the app, it automatically creates a **`Processed Files`** folder in the same location where the executable is launched (e.g., next to `MKV Video Audio & Subtitle Tool.exe`). All processed output goes there by default unless you explicitly change the output location via **Output Dir**.

### :art: Using Individual Mode

1. Click the **Individual** tab (next to Batch) above the settings area.
2. Click any file name in the list below — its saved settings load into the controls.
3. Change DAR, audio track, subtitle, or delete-originals setting for that file only. Changes are saved automatically as you type or select.
4. Switch back to **Batch** at any time to apply global defaults across all files.

### :bar_chart: Understanding Status Colors in the File List

| Color | Meaning |
|-------|---------|
| :green_circle: Green text | This file has custom per-file settings that differ from global defaults |
| :large_blue_circle: Blue text | This file uses global default settings (no individual override) |
| :yellow_circle: Yellow-green background highlight | The currently selected file in the list |

### :wrench: Processing Output

During processing, each file entry shows:

- :repeat: **Progress counter** — `[1/3] filename.mkv …` (which file out of total)
- :white_check_mark: **Success** — `✓ Display Aspect Ratio Set to 16:9 | Subtitles: Removed | Closed Captions: Not Present | Audio Kept: Track #2` or similar, depending on which operations actually ran for that file
- :x: **Failure** — `✗ mkvmerge.exe not found.` or other error message

Each operation reports its own result independently. If a step was skipped because the file already had the desired setting (for example, the DAR already matches), you'll see something like `DAR Already Matches Desired Value` instead of the set message.

When all files in a batch are already correctly configured, the tool shows:

- :point_right: **Nothing to do** — `═══ no action necessary ═══` with progress bars at zero. No files are remuxed or modified.

When processing finishes normally, a summary line appears at the end:

`═══ complete: X ok, Y failed ═══`

---

## :page_facing_up: File Structure

```
MKV Video Audio & Subtitle Tool/
├── MKV_Video_Audio_Subtitle_Tool.py    # Main application source code (GPL v3)
├── MKV_Video_Audio_Subtitle_Tool.spec  # PyInstaller build configuration
├── LICENSE                              # GNU GPL v3 license text
├── README.md                            # This file
│
├── ffmpeg.exe                           # Bundled FFmpeg bitstream filter (CC removal)
├── mkvmerge.exe                         # Bundled mkvtoolnix remuxer (redistributable)
├── mkvpropedit.exe                      # Bundled mkvtoolnix property editor
└── mkvinfo.exe                          # Bundled mkvtoolnix file inspector
```

> :information_source: **Note:** The `dist/` folder is generated at build time by PyInstaller and contains the compiled standalone executable (`MKV Video Audio & Subtitle Tool.exe`). It is not included in source distributions.

---

## :computer: Configuration

Your output folder choice is saved automatically — no configuration needed. To reset it, just delete the `settings.json` file in the app's directory.

### :terminal: Launching from Explorer

You can also launch the tool by double-clicking its executable with MKV files passed as arguments (for example, drag and drop an MKV file or folder onto the `.exe` icon). The app opens and adds those files automatically.

---

## :scroll: License

This project is licensed under the **GNU General Public License v3.0** (GPL-3.0). You are free to copy, distribute, and modify it under the terms of the GPL. See `LICENSE` for the full text.

The bundled mkvtoolnix executables remain property of their respective authors (MKVToolNix project by Moritz Bunge / molly). Refer to their license for redistribution terms.
