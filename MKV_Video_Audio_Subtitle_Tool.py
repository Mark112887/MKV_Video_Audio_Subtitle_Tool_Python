# -*- mode: python ; coding: utf-8 -*-
"""MKV Pixel Aspect Ratio Changer + Subtitle Remover

tkinter desktop app — visual layout matches the original MP4 Pixel Aspect Ratio Changer.
Backend uses mkvtoolnix for container operations (no re-encoding).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import sys
import os
import re
import math
import json
import threading
import msvcrt
import _winapi

# ─── UI colour constants ───
GREEN = "#9ece6a"
RED   = "#f7768e"


# ─── Windows DND support (pywin32 + ctypes for DragQueryFile) ──────────────
import ctypes
from ctypes import wintypes

WM_DROPFILES = 0x0233

_shell32 = ctypes.windll.shell32
_DragQueryFileW = _shell32.DragQueryFileW
_DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT, ctypes.POINTER(wintypes.WCHAR), wintypes.UINT]
_DragQueryFileW.restype = wintypes.UINT
_DragFinish = _shell32.DragFinish
_DragFinish.argtypes = [ctypes.c_void_p]
_DragFinish.restype = ctypes.c_int


# ─── Settings persistence ──────────────────────────────────────────────

def _settings_path():
    """Return a path for a JSON settings file next to the script/executable."""
    base = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__
    ))
    return os.path.join(base, 'settings.json')


def _load_output_dir():
    """Load the previously saved output directory, or None."""
    try:
        with open(_settings_path(), 'r') as f:
            data = json.load(f)
        return data.get('output_dir')
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_output_dir(path):
    """Save the output directory to the settings file."""
    try:
        with open(_settings_path(), 'r') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data['output_dir'] = path
    with open(_settings_path(), 'w') as f:
        json.dump(data, f, indent=2)


def _ensure_default_output_dir():
    """Ensure a default output directory exists.

    Returns the output directory path:
    - previously saved one if available;
    - otherwise creates (if missing) and returns a 'Processed Files' folder
      next to the script/executable.
    """
    saved = _load_output_dir()
    if saved and os.path.isdir(saved):
        return saved

    base = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__
    ))
    default = os.path.join(base, 'Processed Files')
    os.makedirs(default, exist_ok=True)
    return default


def parse_dar(dar_str):
    """Parse a DAR string like '16:9', '4/3', '2.35:1' → (dar_w, dar_h) ints.

    Raises ValueError on bad input.
    """
    dar_str = dar_str.strip()
    sep = re.search(r'[/:]', dar_str)
    if not sep:
        raise ValueError(f"Invalid DAR format: {dar_str!r}. Use 'W:H' or 'W/H'.")
    left, right = dar_str[:sep.start()], dar_str[sep.end():]
    left_f, right_f = float(left), float(right)
    if right_f == 0:
        raise ValueError("DAR denominator cannot be zero.")

    # Determine scale needed to convert both sides to integers.
    scale = 1
    for token in (left, right):
        token = token.strip()
        dot = token.find('.')
        if dot != -1:
            digits = len(token[dot + 1:])
            scale *= (10 ** digits)

    left_i = int(round(left_f * scale))
    right_i = int(round(right_f * scale))
    g = math.gcd(left_i, right_i)
    return left_i // g, right_i // g


# ─── mkvtoolnix helpers ──────────────────────────────────────────────

def _mkv_exe(name):
    """Find an mkvtoolnix executable — bundled with PyInstaller or alongside the script."""
    candidates = []
    if hasattr(sys, '_MEIPASS'):
        candidates.append(os.path.join(sys._MEIPASS, f'{name}.exe'))
    base_dir = os.path.dirname(os.path.abspath(
        sys.executable if getattr(sys, 'frozen', False) else __file__
    ))
    candidates.append(os.path.join(base_dir, f'{name}.exe'))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _get_mkvinfop():
    """Return the path to mkvinfo.exe or None."""
    return _mkv_exe('mkvinfo')


def _get_mkvmerge():
    """Return the path to mkvmerge.exe or None."""
    return _mkv_exe('mkvmerge')


def _get_mkvpropedit():
    """Return the path to mkvpropEdit.exe or None."""
    return _mkv_exe('mkvpropedit')


def _get_ffmpeg():
    """Return the path to ffmpeg.exe or None."""
    return _mkv_exe('ffmpeg')


def run_mkvtl(args, timeout=300):
    """Run an mkvtoolnix tool; returns (returncode, combined_text).

    On Windows some mkvtoolnix tools write to stderr — merge stdout+stderr.
    """
    # CREATE_NO_WINDOW hides the command prompt window on Windows for mkvtoolnix tools
    try:
        r = subprocess.run(
            args,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        return r.returncode, r.stdout or ''
    except subprocess.TimeoutExpired:
        return -1, "Timeout"


# ─── mkvmerge --json-status progress parser ──────────────────────

_gui_progress_re = re.compile(r'^#GUI#progress\s+(\d+)%')


def _parse_json_progress(line):
    """Parse a 'progress' field from mkvmerge --json-status JSON lines.

    Returns the percentage int if found, otherwise None.
    """
    try:
        data = json.loads(line)
        return int(data.get('progress', -1)) if data.get('progress') is not None else None
    except (json.JSONDecodeError, ValueError):
        return None


def _run_with_progress(args, timeout=600, callback=None):
    """Run a subprocess and collect '#GUI#progress N%' lines from stdout.

    Returns (returncode, combined_text, progress_events) where progress_events
    is a list of ints.  If --gui-mode was not used the list will be empty.

    *callback* may be called on any thread during polling — callers should
    marshal Tk updates to the main thread if needed (the caller in this file
    does so via _process()'s state-machine scheduling).
    """
    import time as _time

    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
    )

    events: list[int] = []
    out_lines: list[str] = []
    buf = b""
    deadline = _time.time() + timeout

    while proc.poll() is None and _time.time() < deadline:
        try:
            chunk = os.read(proc.stdout.fileno(), 4096)
        except OSError:
            break
        if not chunk:
            _time.sleep(0.05)
            continue
        buf += chunk
        while b"\n" in buf:
            line_bytes, buf = buf.split(b"\n", 1)
            try:
                line = line_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue
            out_lines.append(line + "\n")
            m = _gui_progress_re.match(line)
            if m:
                pct_val = int(m.group(1))
                events.append(pct_val)
                if callback:
                    try:
                        callback('mkvmerge', pct_val)
                    except Exception:
                        pass

    # Final drain — collect any remaining data after EOF
    try:
        remainder = proc.stdout.read()
        if remainder:
            buf += remainder
            while b"\n" in buf:
                line_bytes, buf = buf.split(b"\n", 1)
                try:
                    line = line_bytes.decode("utf-8", errors="replace")
                except Exception:
                    continue
                out_lines.append(line + "\n")
    except OSError:
        pass

    return proc.returncode, ''.join(out_lines), events


def probe_mkv(mkv_path):
    """Probe an MKV file with mkvinfo.

    Returns (info_dict | None, error_msg | None).
    info_dict keys: width, height, video_track_ids, subtitle_track_ids, total_tracks,
                    has_cc, actual_display_w, actual_display_h
    """
    mkvinfo = _get_mkvinfop()
    if mkvinfo is None:
        return None, "mkvinfo.exe not found."

    rc, text = run_mkvtl([mkvinfo, mkv_path], timeout=30)
    if rc != 0:
        return None, f"mkvinfo failed: {text}"

    width = height = actual_display_w = actual_display_h = None
    video_track_ids = []
    subtitle_track_ids = []
    has_cc = False
    total_tracks = 0
    in_codec_private = False

    lines = text.splitlines()

    # First pass: collect track blocks using tree markers (same approach as probe_audio_tracks).
    for i, raw_line in enumerate(lines):
        stripped = raw_line.strip()

        # Track count
        if 'Number of tracks' in stripped:
            m = re.search(r'(\d+)', stripped)
            if m:
                total_tracks = int(m.group(1))

    track_blocks = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Detect start of a track block via tree markers.
        if re.search(r'\|\s*\+\s+Track\s+number:', line):
            m_num = re.search(r'Track number:\s*(\d+)', line)
            if not m_num:
                i += 1
                continue
            track_num = int(m_num.group(1))

            # Extract the parenthetical mkvmerge ID.
            m_mkvmerge_id = re.search(r'mkvmerge.*:\s*(\d+)', line)
            merge_id = int(m_mkvmerge_id.group(1)) if m_mkvmerge_id else track_num

            track_type = None
            codec_id = ''
            in_codec_private = False
            j = i + 1
            while j < len(lines):
                raw_j = lines[j]
                s = raw_j.strip()
                # New track block or top-level section ends the current block.
                if re.search(r'\|\s*\+\s+Track\s+number:', raw_j) or '| Tracks' in raw_j or '| + Segment' in raw_j:
                    break
                m_type = re.search(r'Track type:\s*(\w+)', s)
                if m_type:
                    track_type = m_type.group(1).lower()
                if not codec_id:
                    m_codec = re.search(r'Codec ID:\s*(\S+)', s)
                    if m_codec:
                        codec_id = m_codec.group(1)
                # Collect codec private data lines for CC detection.
                if 'Codec private type' in s:
                    in_codec_private = True
                elif in_codec_private:
                    lower = s.lower()
                    if 'eia-608' in lower or 'eia-708' in lower:
                        has_cc = True
                    elif 'hdmv_pgs_subtitle' in lower or 'hevc_metadata' in lower:
                        has_cc = True
                    # Stop scanning codec_private when hit a new top-level field.
                    if not s.startswith((' ', '\t')) and 'Codec private' not in s:
                        in_codec_private = False
                j += 1

            if track_type == 'video':
                video_track_ids.append(merge_id)
            elif track_type in ('subtitles', 'subtitle', 'text'):
                subtitle_track_ids.append(merge_id)
                # Also check codec ID for CC indicators on subtitle tracks.
                cid_upper = codec_id.upper()
                if cid_upper.startswith('S_EIA') or cid_upper.startswith('S_HDMV_PGS'):
                    has_cc = True

        i += 1

    # ── Extract display & pixel dimensions (matches C++ MkvProber logic) ──────
    # Display dimensions — explicit DAR metadata (may be absent in many MKV files).
    disp_w = disp_h = pix_w = pix_h = 0
    width_prefixes = ['Display width', 'Pixel width', 'Frame width']
    height_prefixes = ['Display height', 'Pixel height', 'Frame height']

    for raw_line in lines:
        t = raw_line.strip()

        # Display dimensions (check only if not already found)
        if disp_w == 0:
            for prefix in width_prefixes:
                idx = t.lower().find(prefix.lower())
                if idx >= 0:
                    after = t[idx + len(prefix):].strip()
                    if after.startswith(':'):
                        val = int(after[1:].strip())
                        if val > 0:
                            disp_w = val
                            break
        if disp_h == 0:
            for prefix in height_prefixes:
                idx = t.lower().find(prefix.lower())
                if idx >= 0:
                    after = t[idx + len(prefix):].strip()
                    if after.startswith(':'):
                        val = int(after[1:].strip())
                        if val > 0:
                            disp_h = val
                            break

        # Pixel dimensions — bare "Width:" / "Height:" (must not be preceded by a word).
        if pix_w == 0:
            wid_idx = t.lower().find('width:')
            if wid_idx >= 0:
                # Reject if preceded by alphabetic char or ends with "Pixel"/"Display".
                before_word = t[:wid_idx].strip().split()[-1] if t[:wid_idx].strip() else ''
                has_prefix = (wid_idx > 0 and t[wid_idx - 1].isalpha()) or \
                             before_word.lower() in ('pixel', 'display', 'frame')
                if not has_prefix:
                    val = int(t[wid_idx + 6:].strip())
                    if val > 2:
                        pix_w = val

        if pix_h == 0:
            hid_idx = t.lower().find('height:')
            if hid_idx >= 0:
                before_word = t[:hid_idx].strip().split()[-1] if t[:hid_idx].strip() else ''
                has_prefix = (hid_idx > 0 and t[hid_idx - 1].isalpha()) or \
                             before_word.lower() in ('pixel', 'display', 'frame')
                if not has_prefix:
                    val = int(t[hid_idx + 7:].strip())
                    if val > 2:
                        pix_h = val

    # Prefer display dimensions (what mkvpropedit uses for DAR), fallback to pixel.
    actual_display_w = disp_w or None
    actual_display_h = disp_h or None
    width = pix_w or width
    height = pix_h or height

    return {
        'width': width or 0,
        'height': height or 0,
        'video_track_ids': video_track_ids,
        'subtitle_track_ids': subtitle_track_ids,
        'total_tracks': total_tracks,
        'has_cc': has_cc,
        'actual_display_w': actual_display_w or 0,
        'actual_display_h': actual_display_h or 0,
    }, None



def process_mkv(mkv_path, output_dir, dar_str="16:9", remove_subs=True,
                remove_cc=False, audio_sel=None, delete_original=False, callback=None):
    """Process one MKV — set DAR via mkvmerge + mkvpropedit.  Returns (ok, msg).

    *callback*: optional callable(step, pct, text) for real-time progress updates.
                 step is 'mkvmerge', 'ffmpeg', 'mkvpropedit', or 'done'.
    """
    # Parse DAR
    try:
        dar_w, dar_h = parse_dar(dar_str)
    except ValueError as e:
        msg = f"Invalid DAR: {e}"
        return False, msg

    basename = os.path.splitext(os.path.basename(mkv_path))[0]
    output_path = os.path.join(output_dir, f"{basename}.mkv")

    # Ensure absolute path for reliable file operations (delete, size checks, etc.)
    mkv_path = os.path.abspath(mkv_path)

    # ── Step 1 — mkvmerge: copy tracks, optionally filter audio & remove subs ─
    mkvmerge = _get_mkvmerge()
    if mkvmerge is None:
        msg = "mkvmerge.exe not found."
        return False, msg

    # Build mkvmerge args: copy tracks, optionally delete subtitles, filter audio.
    # Use --json-status so progress arrives even when stdout is piped (not a console),
    # where mkvmerge suppresses #GUI#progress events and switches to batch mode.
    merge_args = ['-o', output_path, '--json-status']

    if audio_sel is not None:
        merge_args += ['--audio-tracks', str(audio_sel)]

    if remove_subs:
        # -S removes all subtitle tracks (matches the batch file approach)
        merge_args.append('-S')

    merge_args.append(mkv_path)
    full_command = [mkvmerge] + merge_args

    rc, out_text, progress_events = _run_with_progress(full_command, timeout=600, callback=callback)

    # Estimate time for remaining steps if we got real progress from mkvmerge.
    # When muxing finishes (100% or close), report estimated time for CC removal
    # and metadata editing so the bar keeps moving.
    if callback and (not progress_events or 100 not in progress_events):
        size_mb = max(1, os.path.getsize(mkv_path) / (1024 * 1024))
        mux_est = size_mb / 50.0  # ~50 MB/s mux speed estimate
        rem_secs = mux_est + (mux_est * 0.3 if remove_cc else 0) + 2.0  # propedit ~2s
        callback('mkvmerge', 99, f"Muxing video… {rem_secs:.0f}s remaining")

    # Verify output file was actually created
    if not os.path.exists(output_path):
        msg = f"mkvmerge ran but output file does not exist at:\n  {output_path}"
        return False, msg

    # ── Step 1b — ffmpeg: strip closed captions (CEA-608/708, type-6 NAL units) ─
    if remove_cc:
        cc_path = output_path.rsplit('.', 1)[0] + '_cc.mkv'
        ffmpeg = _get_ffmpeg()
        if ffmpeg is None:
            os.remove(output_path)
            return False, "ffmpeg.exe not found."

        # Estimate time for ffmpeg CC removal (copy mode — fast)
        size_mb = max(1, os.path.getsize(output_path) / (1024 * 1024))
        cc_est = size_mb / 100.0

        def _run_cc():
            proc = subprocess.Popen(
                [ffmpeg, '-i', output_path, '-codec', 'copy',
                 '-bsf:v', 'filter_units=remove_types=6', cc_path],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            import time as _t
            start = _t.time()
            while proc.poll() is None:
                elapsed = _t.time() - start
                pct = min(98, int((elapsed / cc_est) * 100)) if cc_est > 0 else 0
                remaining = max(0, cc_est - elapsed)
                if callback:
                    callback('ffmpeg', pct, f"Removing CC… {remaining:.0f}s remaining")
                _t.sleep(0.1)
            return proc.returncode

        rc_cc = _run_cc()

        if rc_cc != 0:
            os.remove(output_path)
            return False, "Failed to remove closed captions."
        if not os.path.exists(cc_path):
            os.remove(output_path)
            return False, "CC removal produced no output file."

        # Replace original output with CC-stripped version
        os.remove(output_path)
        os.rename(cc_path, output_path)

    # ── Step 2 — mkvpropedit: set display width/height on the NEW file ─
    mkvpropedit = _get_mkvpropedit()
    if mkvpropedit is None:
        os.remove(output_path)
        return False, "mkvpropEdit.exe not found."

    # Build mkvpropedit arguments for DAR editing
    dar_args = ['--edit', 'track:v1', '--set', f'display-width={dar_w}',
                '--set', f'display-height={dar_h}', '--set', 'display-unit=3']

    prop_est = 2.0  # mkvpropedit is typically fast (~2s)
    if callback:
        callback('mkvpropedit', 99, f"Setting metadata… {prop_est:.0f}s remaining")
        callback('done', 100)

    rc, out_text = run_mkvtl([mkvpropedit, output_path] + dar_args, timeout=60)

    if rc != 0:
        os.remove(output_path)
        msg = f"mkvpropedit failed (rc={rc}):\n{out_text}"
        return False, msg

    # If a specific audio track was filtered out, the source may have had
    # "Enabled flag: 0" which persists through mkvmerge remuxing.
    # Set flag-enabled=1 on the remaining audio track so players will play it.
    if audio_sel is not None and audio_sel != 1:
        en_args = ['--edit', 'track:a1', '--set', 'flag-enabled=1']
        rc_en, out_en = run_mkvtl([mkvpropedit, output_path] + en_args, timeout=60)
        if rc_en != 0:
            return False, f"Failed to enable audio track:\n{out_en}"

    # Build the success message
    msg_parts = [f"Display Aspect Ratio Set to {dar_w}:{dar_h}"]
    if remove_subs:
        msg_parts.append("  Subtitles Removed: Yes")
    else:
        msg_parts.append("  Subtitles Removed: No")
    if remove_cc:
        msg_parts.append("  Closed Captions Removed: Yes")
    if audio_sel is not None:
        msg_parts.append(f"  Audio Kept: Track #{audio_sel}")

    # Delete original file if requested (only after ALL steps succeed)
    if delete_original:
        try:
            os.remove(mkv_path)
            msg_parts.append("  Original Deleted: Yes")
        except OSError as e:
            # Don't fail the whole operation — output file is already good
            msg_parts.append(f"  Original Deleted: No ({e})")

    msg = " ".join(msg_parts)
    return True, msg


# ─── Audio track probing ──────────────────────────────────────────────

# ─── Language code → full name mapping ─────────────────────────────────────

_ISO_639_1_TO_NAME = {
    'af': 'Afrikaans',       'sq': 'Albanian',      'ar': 'Arabic',
    'eu': 'Basque',          'be': 'Belarusian',    'bn': 'Bengali',
    'bg': 'Bulgarian',       'ca': 'Catalan',       'zh': 'Chinese',
    'hr': 'Croatian',        'cs': 'Czech',         'da': 'Danish',
    'nl': 'Dutch',           'en': 'English',       'et': 'Estonian',
    'fi': 'Finnish',         'fr': 'French',        'gl': 'Galician',
    'ka': 'Georgian',        'de': 'German',        'el': 'Greek',
    'gu': 'Gujarati',        'he': 'Hebrew',        'hi': 'Hindi',
    'hu': 'Hungarian',       'is': 'Icelandic',     'id': 'Indonesian',
    'ga': 'Irish',           'it': 'Italian',       'ja': 'Japanese',
    'kn': 'Kannada',         'kk': 'Kazakh',        'ko': 'Korean',
    'lv': 'Latvian',         'lt': 'Lithuanian',    'mk': 'Macedonian',
    'ms': 'Malay',           'ml': 'Malayalam',     'mt': 'Maltese',
    'mr': 'Marathi',         'mn': 'Mongolian',     'no': 'Norwegian',
    'fa': 'Persian',         'pl': 'Polish',        'pt': 'Portuguese',
    'ro': 'Romanian',        'ru': 'Russian',       'sr': 'Serbian',
    'sk': 'Slovak',          'sl': 'Slovenian',     'es': 'Spanish',
    'sw': 'Swahili',         'sv': 'Swedish',       'ta': 'Tamil',
    'te': 'Telugu',          'th': 'Thai',          'tr': 'Turkish',
    'uk': 'Ukrainian',       'ur': 'Urdu',          'vi': 'Vietnamese',
}

# ISO 639-2/3 three-letter codes — maps plain mkvinfo 'Language:' values
# that aren't IETF BCP 47 tags (which use 2-letter codes) to full names.
_ISO_639_2_TO_NAME = {
    'eng': 'English',      'fra': 'French',       'deu': 'German',
    'spa': 'Spanish',       'por': 'Portuguese',   'ita': 'Italian',
    'rus': 'Russian',       'jpn': 'Japanese',     'kor': 'Korean',
    'zho': 'Chinese',       'ara': 'Arabic',       'hin': 'Hindi',
    'nld': 'Dutch',         'swe': 'Swedish',      'pol': 'Polish',
    'tur': 'Turkish',       'gre': 'Greek',        'heb': 'Hebrew',
    'dan': 'Danish',         'fin': 'Finnish',      'nor': 'Norwegian',
    'ces': 'Czech',         'ukr': 'Ukrainian',   'ron': 'Romanian',
    'hun': 'Hungarian',     'ell': 'Greek',        'tha': 'Thai',
}


def probe_audio_tracks(mkv_path):
    """Probe an MKV file for audio tracks using mkvinfo.

    Returns a list of dicts:
        [{'id': <int>, 'language': <str or None>}, ...]
    Language is from the IETF BCP 47 track language tag, falls back to 'und' if not set.
    The id is the `Track number: N` value from mkvinfo — this works directly with
    mkvmerge --audio-tracks and mkvpropedit --edit track:N.
    """
    mkvinfo = _get_mkvinfop()
    if mkvinfo is None:
        return []

    rc, text = run_mkvtl([mkvinfo, mkv_path], timeout=30)
    if rc != 0 or not text.strip():
        return []

    audio_tracks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect start of a track block: "| + Track number:" (with possible leading whitespace)
        if re.search(r'\|\s*\+\s+Track\s+number:', line):
            m_num = re.search(r'Track number:\s*(\d+)', line)
            if not m_num:
                i += 1
                continue
            track_num = int(m_num.group(1))

            # Extract the parenthetical mkvmerge ID — this is what --audio-tracks expects
            m_mkvmerge_id = re.search(r'mkvmerge.*:\s*(\d+)', line)
            merge_id = int(m_mkvmerge_id.group(1)) if m_mkvmerge_id else track_num

            # Parse this track block (collect until next Track block or top-level section)
            track_type = None
            language = None
            j = i + 1
            while j < len(lines):
                raw_j = lines[j]
                s = raw_j.strip()
                # New track block or top-level section ends the current block
                if re.search(r'\|\s*\+\s+Track\s+number:', raw_j) or '| Tracks' in raw_j:
                    break
                m_type = re.search(r'Track type:\s*(\w+)', s)
                if m_type:
                    track_type = m_type.group(1).lower()
                # IETF BCP 47 language (preferred over plain Language)
                m_lang = re.search(r'Language \(IETF BCP 47\):\s*(.+)', s)
                if m_lang:
                    language = m_lang.group(1).strip()
                # Fallback: plain 'Language:' field — mkvinfo may output
                # either "Language:" or "[+\s]Language:" depending on version.
                # We only match when there's no IETF BCP 47 parenthetical, and we
                # anchor with a non-word boundary before "Language" to avoid false
                # matches (e.g., in codec private data strings).
                elif not language:
                    m_lang_plain = re.search(r'(?<![a-zA-Z(])Language:\s*(.+)', s)
                    if m_lang_plain:
                        language = m_lang_plain.group(1).strip()
                j += 1

            if track_type == 'audio':
                audio_tracks.append({
                    'id': merge_id,
                    'track_num': track_num,  # MKV Track Number element for mkvpropedit
                    'language': language,
                })

        i += 1

    return audio_tracks


# ─── GUI ─────────────────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        self.root.title("MKV Video Audio & Subtitle Tool")
        self.root.geometry("850x560")
        self.root.resizable(False, False)
        self.root.configure(bg="#1a1b26")
        self.file_list = []
        self.selected_file_index = -1  # -1 = no file selected → global settings apply
        self.processing = False
        self.output_dir = _ensure_default_output_dir()

        # Per-file settings dict: path -> {'dar': str, 'audio_sel': int|None, 'remove_subs': bool, 'remove_cc': bool, 'delete_originals': bool}
        self.file_settings = {}

        # Colours (matching the original app)
        BG       = "#1a1b26"
        CARD     = "#24283b"
        FG       = "#c0caf5"
        ACCENT   = "#7aa2f7"
        MUTED    = "#565f89"

        # ── Drop zone ────────────────────────────────────────────────
        self.drop_frame = tk.Frame(root, bg=CARD, height=100,
                                   highlightbackground="#414868", highlightthickness=2)
        self.drop_frame.pack(fill="x", padx=14, pady=10)
        self.drop_frame.pack_propagate(False)

        self.drop_icon = tk.Label(self.drop_frame, text="📂", font=("Segoe UI Emoji", 28),
                                  bg=CARD, fg=FG)
        self.drop_icon.place(relx=0.15, rely=0.4, anchor="center")

        self.drop_label = tk.Label(self.drop_frame,
                                   text="Drag & Drop MKV Files or Folders here\nor click Browse",
                                   font=("Segoe UI", 10), bg=CARD, fg=MUTED, justify="center")
        self.drop_label.place(relx=0.55, rely=0.4, anchor="center")

        self.drop_frame.bind("<Button-1>", self._browse)
        self.drop_frame.configure(cursor="hand2")

        # Hover effect
        def on_enter(_e):
            self.drop_frame.configure(highlightbackground=ACCENT)
        def on_leave(_e):
            self.drop_frame.configure(highlightbackground="#414868")
        self.drop_frame.bind("<Enter>", on_enter)
        self.drop_frame.bind("<Leave>", on_leave)

        # ── File list ────────────────────────────────────────────────
        list_frame = tk.Frame(root, bg=BG)
        list_frame.pack(fill="x", padx=14, pady=(0, 4))

        hdr = tk.Label(list_frame, text="Files & Status", font=("Segoe UI", 9, "bold"),
                       bg=BG, fg=MUTED)
        hdr.pack(anchor="nw")

        self.txt = tk.Text(list_frame, height=7,
                           font=("Consolas", 9), bg="#16171a", fg=FG,
                           insertbackground=ACCENT, state="disabled",
                           highlightthickness=0, wrap="word",
                           cursor="hand2")
        vsb = ttk.Scrollbar(list_frame, command=self.txt.yview)
        self.txt.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.txt.pack(fill="both", expand=True)
        self.txt.bind("<Button-1>", self._on_text_click)

        # ── Info bar ─────────────────────────────────────────────────
        self.info_var = tk.StringVar(value="")
        self.info_lbl = tk.Label(root, textvariable=self.info_var,
                                 font=("Segoe UI", 9), bg=BG, fg=MUTED, anchor="w")
        self.info_lbl.pack(fill="x", padx=16, pady=(0, 4))

        # Binary status label — visible only when no files present.
        self.bin_status_lbl = tk.Label(root, font=("Segoe UI", 9), bg=BG, anchor="w")
        self.bin_status_lbl.pack(fill="x", padx=16, pady=(0, 2))

        # Initial status — binary check since no files yet.
        self._update_info()

        # ── Mode toggle (Batch / Individual) ─────────────────────────
        mode_row = tk.Frame(root, bg=BG)
        mode_row.pack(fill="x", padx=16, pady=(4, 2))

        self.mode_var = tk.StringVar(value="batch")

        # Batch button starts active (blue bg), Individual is inactive (gray text on dark card)
        self._btn_mode("Batch",       "batch",       mode_row)
        self._btn_mode("Individual",  "individual",  mode_row, inactive=True)

        # ── DAR input + Audio dropdown + Processing options (single row) ───
        dar_row = tk.Frame(root, bg=BG)
        dar_row.pack(side="top", fill="x", padx=16, pady=(0, 2))

        tk.Label(dar_row, text="Desired Aspect Ratio:", font=("Segoe UI", 9),
                 bg=BG, fg=MUTED).pack(side="left")

        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.style.configure(
            "DAR.TEntry",
            fieldbackground="#16171a",
            foreground=FG,
            font=("Consolas", 9),
            padding=3
        )

        # Progress bars use canvas - no ttk style needed

        # DAR uses a tk.StringVar with textvariable on the Entry.
        # trace_add fires on BOTH user typing AND programmatic .set() calls.
        # We save per-file settings in the trace, skipping during file restoration.
        self._dar_last_valid = "16:9"  # track last valid DAR for reverting invalid input
        self._dar_reverting = False    # guard against re-entrant trace callbacks on DAR validation
        self._restoring_file = False   # set around file restoration to skip per-file save

        def _dar_validate(val):
            """Validate a DAR value. Returns True if valid (complete or partial typing)."""
            if not val:
                return False
            sep = re.search(r'[:/]', val)
            if sep:
                left, right = val[:sep.start()], val[sep.end():]
                if not left or not right:
                    return True  # partial typing (e.g. "16:" or ":9")
                if all(c.isdigit() for c in left) and all(c.isdigit() for c in right):
                    self._dar_last_valid = val
                    return True
            else:
                if all(c.isdigit() for c in val):
                    return True
            return False

        def _dar_trace(*_args):
            """Trace callback on dar_str — validates and saves per-file settings.

            IMPORTANT: We DO NOT restore dar_str via after_idle here. When the user
            highlights all text and types, tkinter's textvariable sync updates dar_str
            synchronously during key processing, but by the time after_idle fires it has
            already synced from the Entry — so attempting to restore "16:9" conflicts with
            what the user is typing. Instead we only validate and save for valid values;
            invalid input (like "abc") stays in the entry and is caught on blur or process.
            """
            if self._dar_reverting or self._restoring_file:
                return

            val = self.dar_str.get()

            # Validate and save immediately for valid values (complete or partial typing)
            if _dar_validate(val):
                self._save_current_file_settings()
                return

            # For empty/invalid — just update last_valid and return without restoring.
            # The user might still be typing; any remaining invalid value is caught on blur.
            self._dar_last_valid = val if val else self._dar_last_valid

        self.dar_str = tk.StringVar(value="16:9")
        self.dar_entry = ttk.Entry(dar_row, style="DAR.TEntry", textvariable=self.dar_str, width=8)
        self.dar_entry.pack(side="left", padx=(6, 0))

        def _select_dar(_event=None):
            self.dar_entry.selection_range(0, "end")
        self.dar_entry.bind("<FocusIn>", _select_dar)

        def _on_dar_focus_out(_event=None):
            """Validate DAR on blur — revert to default if invalid."""
            val = self.dar_str.get()
            if val and _dar_validate(val):
                self._save_current_file_settings()
            elif val:
                # Invalid value — revert to last valid
                self._dar_reverting = True
                try:
                    self.dar_str.set(self._dar_last_valid)
                finally:
                    self._dar_reverting = False
        self.dar_entry.bind("<FocusOut>", _on_dar_focus_out)

        # Spacer
        tk.Label(dar_row, text="", bg=BG, width=2).pack(side="left")

        # "Audio", "Track", "To", "Keep" stacked vertically beside the combobox
        audio_frame = tk.Frame(dar_row, bg=BG)
        audio_frame.pack(side="left", padx=(0, 4))
        for word in ("Audio", "Track", "To", "Keep"):
            tk.Label(audio_frame, text=word, font=("Segoe UI", 9),
                     bg=BG, fg=MUTED).pack(side="top")

        self.audio_var = tk.StringVar(value="Keep All")
        self.audio_combo = ttk.Combobox(
            dar_row, textvariable=self.audio_var,
            font=("Segoe UI", 9), state="readonly", width=26, justify="center"
        )
        self.audio_combo["values"] = ("Keep All",)
        self.audio_combo.pack(side="left", padx=(0, 14))

        def _on_audio_change(*_args):
            """Save per-file settings on audio change — skip if restoring controls."""
            if getattr(self, '_restoring_file', False):
                return
            self._save_current_file_settings()
        self.audio_var.trace_add('write', _on_audio_change)

        # ── Processing options (side-by-side checkboxes in the same row) ──
        self.remove_subs_var = tk.BooleanVar(value=False)
        subs_cb = tk.Checkbutton(
            dar_row, text="Remove Subtitles", variable=self.remove_subs_var,
            font=("Segoe UI", 9), bg=BG, fg=FG,
            activebackground=BG, selectcolor=CARD, anchor="w", cursor="hand2"
        )
        subs_cb.pack(side="left")

        def _on_subs_change(*_args):
            """Save per-file settings on subs change — skip if restoring controls."""
            if getattr(self, '_restoring_file', False):
                return
            self._save_current_file_settings()
        self.remove_subs_var.trace_add('write', _on_subs_change)

        self.remove_cc_var = tk.BooleanVar(value=False)
        cc_cb = tk.Checkbutton(
            dar_row, text="Remove Closed Captions", variable=self.remove_cc_var,
            font=("Segoe UI", 9), bg=BG, fg=FG,
            activebackground=BG, selectcolor=CARD, anchor="w", cursor="hand2"
        )
        cc_cb.pack(side="left")

        def _on_cc_change(*_args):
            """Save per-file settings on CC change — skip if restoring controls."""
            if getattr(self, '_restoring_file', False):
                return
            self._save_current_file_settings()
        self.remove_cc_var.trace_add('write', _on_cc_change)

        self.delete_originals_var = tk.BooleanVar(value=False)
        del_cb = tk.Checkbutton(
            dar_row, text="Delete\nOriginal\nFiles", variable=self.delete_originals_var,
            font=("Segoe UI", 9), bg=BG, fg=FG,
            activebackground=BG, selectcolor=CARD, anchor="w", cursor="hand2"
        )
        del_cb.pack(side="left")

        def _on_delete_change(*_args):
            """Save per-file settings on delete-originals change — skip if restoring controls."""
            if getattr(self, '_restoring_file', False):
                return
            self._save_current_file_settings()
        self.delete_originals_var.trace_add('write', _on_delete_change)

        # ── Progress ─────────────────────────────────────────────────

        # Card container holding both bars
        CARD_BG = "#1b1e28"  # Dark card background for progress area
        pbar_card = tk.Frame(root, bg=CARD_BG)
        pbar_card.pack(fill="x", padx=14, pady=6)

        # --- Current file progress (label + percentage → track) ---
        self.cur_row = tk.Frame(pbar_card, bg=CARD_BG)
        self.cur_row.pack(fill="x", padx=8)

        self.cur_text = tk.Label(
            self.cur_row, text="Current File: 0%", font=("Segoe UI", 9),
            bg=CARD_BG, fg="#7aa2f7")
        self.cur_text.pack(anchor="w")

        self.cur_canvas = tk.Canvas(
            self.cur_row, width=520, height=14, bg="#161923",
            highlightthickness=1, highlightbackground="#ffffff")
        self.cur_canvas.pack(fill="x", pady=(2, 0))

        # --- Overall progress (label + percentage → track) ---
        self.over_row = tk.Frame(pbar_card, bg=CARD_BG)
        self.over_row.pack(fill="x", padx=8, pady=(6, 0))

        self.over_text = tk.Label(
            self.over_row, text="Overall: 0%", font=("Segoe UI", 9),
            bg=CARD_BG, fg="#7aa2f7")
        self.over_text.pack(anchor="w")

        self.over_canvas = tk.Canvas(
            self.over_row, width=520, height=14, bg="#161923",
            highlightthickness=1, highlightbackground="#ffffff")
        self.over_canvas.pack(fill="x")

        # ── Buttons ──────────────────────────────────────────────────
        btn_row = tk.Frame(root, bg=BG)
        btn_row.pack(fill="x", padx=14, pady=6)

        self._btn("📁 Browse", self._browse, btn_row, side="left")
        self._btn("🗑 Clear",  self._clear,  btn_row, side="left")
        tk.Label(btn_row, text="", bg=BG, width=2).pack(side="left")
        self._btn("📂 Output Dir",  self._pick_output, btn_row, side="right")
        self.proc_btn = self._btn("▶  Process", self._start_process, btn_row,
                                  side="right", bg=GREEN, state="disabled")

        # ── CLI args ─────────────────────────────────────────────────
        for arg in sys.argv[1:]:
            for mkv in self._collect_mkv(arg):
                self.add_file(mkv)

        # ── Windows drag-and-drop hook ───────────────────────────────
        self._setup_win_dnd()

    def _btn(self, text, cmd, master, side="left", bg="#3b4261", **kw):
        b = tk.Button(master, text=text, command=cmd, font=("Segoe UI", 9),
                      width=10, bg=bg, fg="#e1e5ee", padx=6, pady=3,
                      activebackground="#474c68", activeforeground="white",
                      relief="flat", cursor="hand2")
        b.config(**kw)
        b.pack(side=side, padx=3)
        return b

    def _dnd_log(self, msg):
        """Append a DND diagnostic message to the app text area."""
        self._append_text(msg + "\n", color="#7aa2f7")

    # -- Windows DND via pywin32 --
    def _setup_win_dnd(self):
        try:
            import win32gui
            self.root.after(200, self._enable_dnd_delayed)
        except ImportError as e:
            self._dnd_log(f"pywin32 not available ({e}), DND disabled")
        except Exception as e:
            self._dnd_log(f"Setup failed: {e}")

    def _enable_dnd_delayed(self):
        import win32gui

        try:
            target = self.drop_frame
            hwnd = int(target.winfo_id())

            result = ctypes.windll.shell32.DragAcceptFiles(hwnd, True)
            if not result:
                self._dnd_log(f"DragAcceptFiles returned FALSE (hwnd=0x{hwnd:x})")
                return

            GWLP_WNDPROC = -4
            original_proc = win32gui.GetWindowLong(hwnd, GWLP_WNDPROC)
            self._dnd_original_proc = original_proc

            def wnd_callback(h, msg, wp, lp):
                if msg == WM_DROPFILES:
                    try:
                        count = _DragQueryFileW(wp, -1, None, 0)
                        dropped_paths = []
                        for i in range(count):
                            buf = ctypes.create_unicode_buffer(260)
                            _DragQueryFileW(wp, i, buf, 260)
                            dropped_paths.append(buf.value)
                        _DragFinish(wp)
                        mkvs = []
                        for dp in dropped_paths:
                            mkvs.extend(self._collect_mkv(dp))
                        added = sum(1 for f in mkvs if self.add_file(f))
                        self._dnd_log(f"Added {added} mkv(s).")
                    except Exception as e:
                        self._dnd_log(f"error querying drop: {e}")
                    return 0
                proc = int(self._dnd_original_proc)
                return ctypes.windll.user32.CallWindowProcW(
                    proc, h, msg, wp, lp)

            win32gui.SetWindowLong(hwnd, GWLP_WNDPROC, wnd_callback)

        except Exception as e:
            self._dnd_log(f"enable failed: {type(e).__name__}: {e}")

    @staticmethod
    def _collect_mkv(path):
        """Return a list of .mkv paths from *path*.

        If *path* is a file → return it (if .mkv).
        If *path* is a directory  → walk the tree and return all .mkv files.
        """
        if os.path.isfile(path) and path.lower().endswith(".mkv"):
            return [path]
        if os.path.isdir(path):
            mkvs = []
            for root, _dirs, files in os.walk(path):
                for fname in files:
                    if fname.lower().endswith(".mkv"):
                        mkvs.append(os.path.join(root, fname))
            return mkvs
        return []

    # -- file management --
    def _browse(self, event=None):
        files = filedialog.askopenfilenames(
            title="Select MKV Files", filetypes=[("MKV Files", "*.mkv")]
        )
        for f in files:
            self.add_file(f)

    def add_file(self, path):
        if path not in self.file_list and os.path.isfile(path):
            self.file_list.append(path)
            self._append_text(os.path.basename(path) + "\n", color="#c0caf5", tag_name=path)
            self._update_info()
            # Probe audio tracks from the first file added
            self.root.after(100, self._probe_and_populate_audio)
            if not self.processing and self.output_dir:
                self.proc_btn.config(state="normal")
            return True
        return False

    def _clear(self):
        self.file_list.clear()
        self.selected_file_index = -1
        self.file_settings.clear()
        self._global_audio_sel = None
        self.dar_str.set("16:9")
        self.audio_var.set("Keep All")
        self.audio_combo.config(values=("Keep All",))
        self._audio_track_data = {"Keep All": None}
        self.txt.config(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.config(state="disabled")
        self.cur_canvas.delete("all")
        self.over_canvas.delete("all")
        self.cur_text.config(text="Current File: 0%")
        self.over_text.config(text="Overall: 0%")
        if not self.processing:
            self.proc_btn.config(state="disabled")
        self._update_info()

    def _update_info(self):
        """Update the info bar — shows binary status when no files, file count otherwise."""
        if self.file_list:
            # Files present — show file count on main line, hide binary status.
            self.info_var.set(f"Files: {len(self.file_list)}")
            self.info_lbl.config(fg="#565f89")
        else:
            # No files — "Processing complete." after batch, blank at idle startup.
            if getattr(self, '_batch_ran', False):
                self.info_var.set("Processing complete.")
            else:
                self.info_var.set("")
            self.info_lbl.config(fg="#c0caf5")
            found = sum(1 for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is not None)
            missing = 4 - found
            if missing == 0:
                self.bin_status_lbl.config(text="All 4 binaries found", fg="#9ece6a")
            else:
                names = [b for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is None]
                self.bin_status_lbl.config(text=f"Missing: {', '.join(names)}", fg="#f7768e")
            # bin_status_lbl stays packed — it's always visible when no files are present.
            found = sum(1 for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is not None)
            missing = 4 - found
            if missing == 0:
                self.bin_status_lbl.config(text="All 4 binaries found", fg="#9ece6a")
            else:
                names = [b for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is None]
                self.bin_status_lbl.config(text=f"Missing: {', '.join(names)}", fg="#f7768e")
            found = sum(1 for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is not None)
            missing = 4 - found
            if missing == 0:
                self.bin_status_lbl.config(text="All 4 binaries found", fg="#9ece6a")
            else:
                names = [b for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is None]
                self.bin_status_lbl.config(text=f"Missing: {', '.join(names)}", fg="#f7768e")
            # bin_status_lbl is packed at init; only pack if not already visible.
            try:
                self.bin_status_lbl.pack_info()
            except tk.TclError:
                self.bin_status_lbl.pack(fill="x", padx=16, pady=(0, 2))
            found = sum(1 for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is not None)
            missing = 4 - found
            if missing == 0:
                self.bin_status_lbl.config(text="All 4 binaries found", fg="#9ece6a")
            else:
                names = [b for b in ("mkvinfo", "mkvmerge", "mkvpropedit", "ffmpeg") if _mkv_exe(b) is None]
                self.bin_status_lbl.config(text=f"Missing: {', '.join(names)}", fg="#f7768e")
            # bin_status_lbl is packed at init; only pack if not already visible.
            try:
                bbox = self.bin_status_lbl.pack_info()
            except tk.TclError:
                self.bin_status_lbl.pack(fill="x", padx=16, pady=(0, 2))

    def _pick_output(self):
        d = filedialog.askdirectory(title="Choose Output Directory")
        if d:
            self.output_dir = d
            _save_output_dir(d)
            self._update_info()
            if not self.processing and self.file_list:
                self.proc_btn.config(state="normal", bg="#9ece6a")

    def _start_process(self):
        if not self.file_list or self.processing:
            return
        if not self.output_dir:
            messagebox.showwarning("No Output Folder",
                                  "Please select an output folder first.\n\nClick 📂 Output Dir to choose a destination.")
            return
        try:
            parse_dar(self.dar_str.get())
        except ValueError as e:
            messagebox.showerror("Invalid DAR", str(e))
            return
        self._process()

    def _save_current_file_settings(self):
        """Save current control values to per-file settings for the selected file.

        If no file is selected (selected_file_index == -1), also store global defaults
        so the user can see there's nothing special for any single file.
        """
        if 0 <= self.selected_file_index < len(self.file_list):
            sel_path = self.file_list[self.selected_file_index]
            # Resolve audio selection to track ID
            display = self.audio_var.get()
            data = getattr(self, '_audio_track_data', {"Keep All": None})
            audio_sel = data.get(display, None)

            self.file_settings[sel_path] = {
                'dar': self.dar_str.get(),
                'audio_sel': audio_sel,
                'remove_subs': self.remove_subs_var.get(),
                'remove_cc': self.remove_cc_var.get(),
                'delete_originals': self.delete_originals_var.get(),
            }
        else:
            # No file selected — store global "no override" marker
            pass

    def _process(self):
        """Batch-process files — now runs entirely on the main thread via after() scheduling.

        Replaced the old threading.Thread(daemon=True) pattern because canvas pixels
        never flush to screen when Tk widget updates happen on a background thread.
        WM_PAINT messages only process through Win32's message pump, which is tied to
        the main thread's mainloop().  Canvas drawing from any other thread produces
        invisible changes — root.update() does not help across thread boundaries.

        This method sets up state, then kicks off the first after()-scheduled step:
        _process_step(), which drives the entire pipeline as a state machine.
        """
        import time as _time

        remove_subs_global = self.remove_subs_var.get()
        remove_cc_global = self.remove_cc_var.get()
        delete_originals_global = self.delete_originals_var.get()
        dar_str_global = self.dar_str.get()
        display = self.audio_var.get()
        data = getattr(self, '_audio_track_data', {"Keep All": None})
        audio_sel_global = data.get(display, None)

        # Cache global values for comparison in _highlight_selected
        self._global_audio_sel = audio_sel_global

        # Save any pending per-file settings first
        self._save_current_file_settings()

        self.processing = True
        self._tk(lambda: self.proc_btn.config(state="disabled", bg="#3b4261"))
        self._tk(lambda: self.info_var.set("Processing…"))
        self._tk(lambda: self._append_text("\n── processing ──\n", color="#565f89"))

        # Reset progress bars for new batch
        cw = 518
        ow = 518
        self.cur_canvas.create_rectangle(1, 2, 1 + cw, 13, fill="#1e2130", outline="")
        self.over_canvas.create_rectangle(1, 2, 519, 13, fill="#1e2130", outline="")
        self.cur_text.config(text="Current File: 0%")
        self.over_text.config(text="Overall: 0%")

        total = len(self.file_list)
        ok = 0
        fail = 0

        # ── Initialise state-machine internals ────────────────────────────
        self._proc_state = {
            'idx': 0,           # next file index (0-based)
            'total': total,
            'ok': ok,
            'fail': fail,
            'file_actions_count': 0,   # how many files needed actual work across all files
            # Per-file fields filled when we reach BUILD_FILE:
            'fp': None,         # file path being processed
            'fname': None,
            'dar_str': None,
            'remove_subs': False,
            'remove_cc': False,
            'audio_sel': None,
            'delete_originals': False,
            'output_path': None,
            'merge_args': None,
            # Mux step:
            'mux_proc': None,
            'mux_buf': b'',
            'mux_events': [],
            # Step state machine states:
            'step': None,      # None → BUILD_FILE → MKV_START → MKV_POLL → FFmpeg_CC
                              #                → PROPEDIT → DONE_FILE → NEXT or COMPLETE
        }

        # Kick off the state machine on the main thread via after()
        self.root.after(0, self._process_step)

    def _process_step(self):
        """Tk-driven state machine step — called via root.after() so all Tk updates
        happen on the main thread and canvas pixels flush to screen in real time.
        """
        import time as _time
        s = self._proc_state
        if not s:
            return

        step = s.get('step') or 'BUILD_FILE'
        self._proc_step = step  # track for error reporting

        try:
            # ─── BUILD_FILE: pick next file, gather settings, build args ───
            if step == 'BUILD_FILE':
                idx = s['idx']
                total = s['total']
                if idx >= total:
                    self._process_complete()
                    return

                fp = self.file_list[idx]

                if not os.path.isfile(fp):
                    fname = os.path.basename(fp)
                    s['fail'] += 1
                    pct_done = int(idx * 100 / total)
                    ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
                    fw = max(2, int(ow * pct_done / 100.0))
                    self.root.after(0, lambda fw=fw, ow=ow, pd=pct_done: (
                        self.over_canvas.delete("fill"),
                        self.over_canvas.create_rectangle(1, 2, 1 + fw, 13, fill="#7ab5cf", tag="fill"),
                        self.over_text.config(text=f"Overall: {pd}%", fg=RED)
                    ))
                    self.root.after(0, lambda fn=fname: self._append_text(f"⊘ SKIP {fn} (not found)\n", color="#f7768e"))
                    s['idx'] = idx + 1
                    s['step'] = 'BUILD_FILE'
                    self.root.after(0, self._process_step)
                    return

                fs = self.file_settings.get(fp)
                if fs:
                    dar_str = fs['dar']
                    remove_subs = fs['remove_subs']
                    remove_cc = fs.get('remove_cc', False)
                    audio_sel = fs['audio_sel']
                    delete_originals = fs.get('delete_originals', False)
                else:
                    dar_str = self.dar_str.get()
                    remove_subs = self.remove_subs_var.get()
                    remove_cc = self.remove_cc_var.get()
                    audio_sel = self._global_audio_sel
                    delete_originals = self.delete_originals_var.get()

                # ─── Probe this file for validation (compare against current state) ───
                info_dict, probe_err = None, None
                try:
                    info_dict, probe_err = probe_mkv(fp)
                except Exception:
                    pass

                has_subtitles = bool(info_dict.get('subtitle_track_ids')) if info_dict else False
                has_cc_from_file = info_dict.get('has_cc', False) if info_dict else False
                cur_dar_w = (info_dict.get('actual_display_w') or info_dict.get('width')) if info_dict else 0
                cur_dar_h = (info_dict.get('actual_display_h') or info_dict.get('height')) if info_dict else 0

                # ─── Subtitle validation: skip -S if no subs exist ───
                validation_notes = []
                if remove_subs and not has_subtitles:
                    remove_subs = False
                    validation_notes.append("Subtitles: none present (skipped)")

                # ─── DAR validation: compare against desired ───
                skip_dar = False
                dar_display_str = ""
                if cur_dar_w > 0 and cur_dar_h > 0:
                    dar_display_str = f"{cur_dar_w}:{cur_dar_h}"
                    try:
                        if self._matches_dar(cur_dar_w, cur_dar_h, dar_str):
                            skip_dar = True
                    except ValueError:
                        pass
                elif info_dict and (pw := info_dict.get('width') or 0) > 0 and (ph := info_dict.get('height') or 0) > 0:
                    # Fallback to pixel resolution when no actual display dimensions stored.
                    dar_display_str = f"{pw}:{ph}"
                    try:
                        if self._matches_dar(pw, ph, dar_str):
                            skip_dar = True
                    except ValueError:
                        pass

                if skip_dar and dar_display_str:
                    validation_notes.append(f"DAR already {dar_display_str} (skipped)")

                # ─── CC validation flag for later FFmpeg step ───
                skip_cc = True  # default to "no CC work needed" unless user explicitly requested removal
                if remove_cc:
                    if has_cc_from_file:
                        skip_cc = False  # CC exists — we'll need to remove it
                    else:
                        validation_notes.append("CC: none present (skipped)")

                fname = os.path.basename(fp)
                output_path = os.path.join(self.output_dir,
                                           os.path.splitext(fname)[0] + '.mkv')
                merge_args = ['-o', output_path, '--json-status']
                if audio_sel is not None:
                    merge_args += ['--audio-tracks', str(audio_sel)]
                if remove_subs:
                    merge_args.append('-S')
                merge_args.append(fp)

                # Store per-file state with validation flags
                s.update({
                    'fp': fp, 'fname': fname, 'dar_str': dar_str,
                    'remove_subs': remove_subs, 'remove_cc': remove_cc,
                    'audio_sel': audio_sel, 'delete_originals': delete_originals,
                    'output_path': output_path, 'merge_args': merge_args,
                    'skip_dar': skip_dar, 'skip_cc': skip_cc,
                    'validation_notes': validation_notes,
                })

                # ─── Pre-flight: if all operations skipped for this file — skip mkvmerge entirely ───
                if skip_dar and not remove_subs and skip_cc:
                    self.root.after(0, lambda n=fname, ix=idx + 1, t=total:
                                   self._append_text(f"[{ix}/{t}] {n} — no action necessary\n", color="#565f89"))
                    # Update overall progress bar (just the chunk) without filling it
                    pct_before = int(idx * 100 / total)
                    ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
                    fw_ov = max(2, int(ow * pct_before / 100.0))
                    self.root.after(0, lambda fw=fw_ov: (
                        self.over_canvas.delete("fill"),
                        self.over_canvas.create_rectangle(1, 2, 1 + fw, 13, fill="#7ab5cf", tag="fill")
                    ))
                    self.root.after(0, lambda pd=pct_before: self.over_text.config(text=f"Overall: {pd}%"))
                    # Keep bars at 0% for this file
                    self.root.after(0, lambda: self.cur_canvas.create_rectangle(1, 2, 3, 13, fill="#1e2130", outline=""))
                    self.root.after(0, lambda: self.cur_text.config(text=f"Current File: 0%"))
                    # No action needed — report via _process_file_result (no cb_ctx = bars stay at 0)
                    s['skip_this_file'] = True
                    s['step'] = 'MKV_POLL'  # fast-forward to MKV_DONE path which calls _process_file_result
                    self.root.after(0, self._process_step)
                    return

                # UI updates on main thread
                self.root.after(0, lambda n=fname, ix=idx + 1, t=total:
                               self._append_text(f"[{ix}/{t}] {n} …\n", color="#c0caf5"))

                # Overall progress before processing this file
                pct_before = int(idx * 100 / total)
                ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
                fw_ov = max(2, int(ow * pct_before / 100.0))
                self.root.after(0, lambda fw=fw_ov: (
                    self.over_canvas.delete("fill"),
                    self.over_canvas.create_rectangle(1, 2, 1 + fw, 13, fill="#7ab5cf", tag="fill")
                ))
                self.root.after(0, lambda pd=pct_before: self.over_text.config(text=f"Overall: {pd}%"))

                # ─── Start mkvmerge subprocess ───
                s['file_actions_count'] = s.get('file_actions_count', 0) + 1
                mkvmerge = _get_mkvmerge()
                s['mux_proc'] = None
                s['mux_buf'] = b''
                s['mux_events'] = []
                s['mux_error'] = None
                # Compute a fixed mux duration based on file size for smooth progress.
                _fp_for_size = s.get('output_path') or s.get('fp', '')
                try:
                    _size_mb = max(1, os.path.getsize(_fp_for_size) / (1024 * 1024))
                except OSError:
                    _size_mb = 1
                s['_fixed_mux_est'] = max(5, min(int(_size_mb / 25), 120))

                start_mux_time = _time.time()  # wall-clock for time-based fallback
                s['_mux_start_time'] = start_mux_time

                # Compute a fixed mux duration based on input file size (avoids the moving-target
                # problem where mux_est = elapsed * 3 never reaches 100%).
                _fp_for_size = s.get('output_path') or s.get('fp', '')
                try:
                    _size_mb = max(1, os.path.getsize(_fp_for_size) / (1024 * 1024))
                except OSError:
                    _size_mb = 1
                s['_fixed_mux_est'] = max(5, min(int(_size_mb / 25), 120))  # ~25 MB/s default

                try:
                    s['mux_proc'] = subprocess.Popen(
                        [mkvmerge] + merge_args,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                    )
                except Exception as exc:
                    s['mux_error'] = str(exc)
                    s['step'] = 'MKV_POLL'
                    self.root.after(0, self._process_step)
                    return

                # Set up callback context for real-time progress updates.
                # The callback draws directly to cur_canvas on the main thread via after(),
                # so the individual file status bar fills dynamically as mkvmerge works.
                cb_ctx = {'pct': 0, '_bar_width': 518}

                def _mux_cb(step, pct=None, text=None):
                    """Progress callback — draws to the file progress bar on the main thread.

                    Uses event-driven progress from mkvmerge when available.
                    Falls back to a fixed-duration time estimate so the bar always moves.
                    """
                    elapsed = _time.time() - start_mux_time
                    mux_est = s.get('_fixed_mux_est', 60)  # fixed duration, not growing with elapsed
                    time_pct = min(95, int((elapsed / mux_est) * 100))

                    if pct is not None:
                        p = min(100, max(pct, 0))
                        blended = max(time_pct, p)
                    else:
                        blended = time_pct
                        p = 0

                    cb_ctx['pct'] = int(blended)
                    w = cb_ctx['_bar_width']
                    fill = max(2, int((w - 2) * blended / 100.0))
                    self.root.after(0, lambda ff=fill, ww=w: (
                        self.cur_canvas.delete("fill"),
                        self.cur_canvas.create_rectangle(1, 2, 1 + ff, ww, fill="#7ab5cf", tag="fill")
                    ))
                    if text is not None:
                        self.root.after(0, lambda tt=text: self.cur_text.config(text=tt))

                s['_mux_cb'] = _mux_cb
                # Also expose mux_start_time and cb_ctx for the progress_timer callback.
                s['_mux_start_time_ref'] = start_mux_time
                s['_cb_ctx_ref'] = cb_ctx

                # Start a polling timer that calls the callback and monitors process state.
                # The timer runs on the main thread via after(), so all Tk updates
                # inside the callback happen on the right thread.
                def _start_poll():
                    if s.get('mux_proc') is None or s.get('step') != 'MKV_POLL':
                        return
                    cb = s.get('_mux_cb')
                    proc = s.get('mux_proc')
                    if proc is None:
                        s['step'] = 'MKV_POLL'
                        self.root.after(0, self._process_step)
                        return

                    # Read available data from pipe (non-blocking via PeekNamedPipe)
                    avail = 0
                    try:
                        handle = msvcrt.get_osfhandle(proc.stdout.fileno())
                        avail_bytes, _ = _winapi.PeekNamedPipe(handle, 0)
                        avail = avail_bytes if isinstance(avail_bytes, int) else 0
                    except Exception:
                        pass

                    if avail > 0:
                        try:
                            chunk = proc.stdout.read(min(avail, 65536))
                            if chunk:
                                s['mux_buf'] += chunk
                                while b"\n" in s['mux_buf']:
                                    line_bytes, s['mux_buf'] = s['mux_buf'].split(b"\n", 1)
                                    try:
                                        line = line_bytes.decode("utf-8", errors="replace")
                                    except Exception:
                                        continue
                                    # Try GUI progress format first, fall back to JSON
                                    m = _gui_progress_re.match(line)
                                    pct_val = None
                                    if m:
                                        pct_val = int(m.group(1))
                                    else:
                                        j = _parse_json_progress(line)
                                        if j is not None:
                                            pct_val = j
                                    if pct_val is not None and 0 <= pct_val <= 100:
                                        s['mux_events'].append(pct_val)
                                        if cb:
                                            self.root.after(0, lambda pp=pct_val: cb('mkvmerge', pp))
                        except OSError:
                            pass

                    if proc.poll() is not None:
                        # Process finished — drain remaining data
                        try:
                            remainder = proc.stdout.read()
                            if remainder and s.get('mux_buf'):
                                s['mux_buf'] += remainder
                        except OSError:
                            pass
                        s['step'] = 'MKV_DONE'
                        return

                    # Schedule next poll (16ms ≈ 60Hz, matches typical display refresh)
                    s['step'] = 'MKV_POLL'
                    self.root.after(16, _start_poll)

                # Independent time-based progress timer: fires every 200ms to update the
                # individual file status bar with elapsed-time estimate. This ensures the
                # bar fills smoothly even when mkvmerge emits no progress events.
                def _progress_timer():
                    if s.get('step') != 'MKV_POLL':
                        return
                    cb = s.get('_mux_cb')
                    proc = s.get('mux_proc')
                    if proc is None or proc.poll() is not None:
                        # Muxing finished — timer will stop on next _process_step check
                        self.root.after(200, _progress_timer)
                        return
                    start_t = s['_mux_start_time_ref']
                    cb_c = s['_cb_ctx_ref']
                    elapsed = _time.time() - start_t
                    mux_est = s.get('_fixed_mux_est', 60)  # fixed duration, not growing with elapsed
                    t_pct = min(95, int((elapsed / mux_est) * 100))
                    p_old = cb_c.get('pct', 0)
                    # Use the higher of event-driven vs time-based so we don't regress
                    blended = max(t_pct, p_old)
                    if blended != p_old:
                        cb_c['pct'] = blended
                        w = cb_c['_bar_width']
                        fill = max(2, int((w - 2) * blended / 100.0))
                        self.root.after(0, lambda pp=blended, ff=fill, ww=w: (
                            self.cur_canvas.delete("fill"),
                            self.cur_canvas.create_rectangle(
                                1, 2, 1 + ff, ww, fill="#7ab5cf", tag="fill"
                            ),
                            self.cur_text.config(text=f"Current File: {pp}%")
                        ))
                    # Schedule next timer
                    self.root.after(200, _progress_timer)

                s['step'] = 'MKV_POLL'
                self.root.after(0, _start_poll)
                # Kick off the time-based progress timer immediately.
                self.root.after(0, _progress_timer)

                # Schedule the next step to handle process completion.
                # This fires immediately but will wait until after any pending
                # Tk events (including the _start_poll timer).  When the poller
                # detects proc.poll() is not None, it sets step = 'MKV_DONE'
                # and this callback becomes a no-op.
                self.root.after(100, self._process_step)

            # ─── MKV_POLL / MKV_DONE: check if muxing completed ───
            elif step in ('MKV_POLL', 'MKV_DONE'):
                proc = s.get('mux_proc')
                if proc is None:
                    # mux was never started — all ops were skipped pre-flight.
                    # Finish batch now so we don't loop forever.
                    self.root.after(50, self._process_complete)
                    return
                if proc.poll() is None:
                    # Still running — poller will handle updates.  Check back.
                    self.root.after(100, self._process_step)
                    return

                rc = proc.returncode
                cb_ctx = s.get('cb_ctx', {})
                events = s.get('mux_events', [])
                last_pct = cb_ctx.get('pct', 0)

                # Final canvas update with captured percentage.
                # If no progress events arrived (last_pct == 0), fall back to time-based
                # estimate so the bar still reflects elapsed time.
                mux_start = s.get('_mux_start_time')
                if mux_start:
                    elapsed = _time.time() - mux_start
                    mux_est = s.get('_fixed_mux_est', 60)  # fixed duration for consistent progress
                    last_pct = max(last_pct, min(95, int((elapsed / mux_est) * 100)))

                # Final canvas update with captured percentage.
                # Always draw to the individual file status bar so it reflects
                # whatever progress was captured (even 0 if mkvmerge had no output).
                self.root.after(0, lambda lp=last_pct: (
                    self.cur_canvas.delete("fill"),
                    self.cur_canvas.create_rectangle(1, 2, 1 + max(2, int((self.cur_canvas.winfo_width() - 2) * lp / 100.0)), 13, fill="#7ab5cf", tag="fill"),
                ))
                self.root.after(0, lambda lp=last_pct:
                               self.cur_text.config(text=f"Current File: {lp}%"))

                # Build final callback message for remaining steps (CC removal + mkvpropedit).
                # These callbacks also draw to cur_canvas so the bar keeps moving.
                def _build_cb_for_remaining():
                    cb_ctx_local = {'pct': last_pct}

                    def _cb(st, pct=None, text=None):
                        # Use a fixed-duration estimate so progress climbs smoothly to 100%.
                        elapsed = _time.time() - mux_start
                        mux_est = s.get('_fixed_mux_est', 60)
                        time_pct = min(95, int((elapsed / mux_est) * 100))

                        if pct is not None:
                            p = min(100, max(pct, 0))
                            blended = max(time_pct, p)
                        else:
                            blended = time_pct
                            p = last_pct

                        cb_ctx_local['pct'] = int(blended)

                        # Draw to individual file status bar on main thread.
                        self.root.after(0, lambda pp=blended: (
                            self.cur_canvas.delete("fill"),
                            self.cur_canvas.create_rectangle(
                                1, 2,
                                1 + max(2, int((518 - 2) * pp / 100.0)),
                                13, fill="#7ab5cf", tag="fill"
                            ),
                        ))
                        self.root.after(0, lambda pp=blended:
                                       self.cur_text.config(text=f"Current File: {pp}%"))
                        if text is not None:
                            self.root.after(0, lambda tt=text: self.cur_text.config(text=tt))
                    return _cb, cb_ctx_local

                mux_cb, mux_cb_ctx = _build_cb_for_remaining()

                # Check if output file exists
                output_path = s.get('output_path', '')
                if not os.path.exists(output_path):
                    self._process_file_result(False, "mkvmerge produced no output file", cb_ctx=mux_cb_ctx)
                    return

                # ─── If all operations are skipped — nothing was needed ───
                all_skipped = (s.get('skip_dar') and not s['remove_subs'] and s.get('skip_cc', False))
                if all_skipped:
                    # Clear the output file since it's identical to input
                    try:
                        os.remove(output_path)
                    except OSError:
                        pass
                    self.root.after(0, lambda: (
                        self.cur_canvas.create_rectangle(1, 2, 3, 13, fill="#1e2130", outline=""),
                        self.over_canvas.create_rectangle(1, 2, 3, 13, fill="#1e2130", outline=""),
                    ))
                    self.root.after(0, lambda: self.cur_text.config(text=f"Current File: 0%"))
                    self.root.after(0, lambda: self.over_text.config(text="Overall: 0%"))
                    # Decrement the action counter since mkvmerge wasn't needed for this file
                    s['file_actions_count'] = max(0, s.get('file_actions_count', 1) - 1)
                    self._process_file_result(True, "No action necessary", cb_ctx=None)
                    return

                # ─── FFmpeg CC removal step (only if CC actually exists) ───
                if s.get('remove_cc') and not s.get('skip_cc'):
                    ffmpeg = _get_ffmpeg()
                    if ffmpeg is None:
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        self._process_file_result(False, "ffmpeg.exe not found.", cb_ctx=mux_cb_ctx)
                        return

                    cc_path = output_path.rsplit('.', 1)[0] + '_cc.mkv'
                    size_mb = max(1, os.path.getsize(output_path) / (1024 * 1024))
                    cc_est = size_mb / 100.0

                    try:
                        mux_cc_proc = subprocess.Popen(
                            [ffmpeg, '-i', output_path, '-codec', 'copy',
                             '-bsf:v', 'filter_units=remove_types=6', cc_path],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
                    except Exception as exc:
                        self._process_file_result(False, f"ffmpeg failed: {exc}", cb_ctx=mux_cb_ctx)
                        return

                    # Poll CC removal with time-based estimate.
                    # A background thread drains stdout via communicate() to prevent
                    # pipe buffer deadlocks (ffmpeg hangs when its stdout fills up) and
                    # avoid handle-type mismatches on Windows (_winapi needs HANDLEs, not FDs).
                    stderr_list: list[str] = []  # shared between reader and writer
                    cc_done = threading.Event()

                    def _cc_reader():
                        """Drain merged stdout/stderr from the ffmpeg process."""
                        try:
                            out, _ = mux_cc_proc.communicate()
                            if out is not None:
                                for line in out.splitlines():
                                    stderr_list.append(line.decode("utf-8", errors="replace"))
                        except Exception:
                            pass  # reader failures should not abort processing
                        finally:
                            cc_done.set()

                    threading.Thread(target=_cc_reader, daemon=True, name="cc-reader").start()

                    cc_start = _time.time()
                    while mux_cc_proc.poll() is None and not cc_done.is_set():
                        elapsed = _time.time() - cc_start
                        pct_cc = min(98, int((elapsed / cc_est) * 100)) if cc_est > 0 else 0
                        rem = max(0, cc_est - elapsed)
                        mux_cb('ffmpeg', pct_cc, f"Removing CC… {rem:.0f}s remaining")
                        self.root.update_idletasks()
                        _time.sleep(0.1)

                    # Wait for the background reader to finish collecting output
                    # (in case process exited while we were in the last sleep iteration)
                    cc_done.wait(timeout=5)
                    stderr_text_lower = ''.join(stderr_list).lower()

                    if mux_cc_proc.returncode != 0:
                        # ffmpeg -bs:v filter_units=remove_types=6 can return non-zero when
                        # there are no SEI (CC) frames — treat that as a no-op and continue.
                        is_no_op = any(
                            kw in stderr_text_lower
                            for kw in ('not found', 'no data', 'no sei')
                        )
                        if not is_no_op:
                            try:
                                os.remove(output_path)
                            except OSError:
                                pass
                            self._process_file_result(False, "Failed to remove closed captions.", cb_ctx=mux_cb_ctx)
                            return

                    # CC removal succeeded (or was a no-op — nothing to remove). Swap output into place.
                    if not os.path.exists(cc_path):
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        self._process_file_result(False, "CC removal produced no output file.", cb_ctx=mux_cb_ctx)
                        return

                    # Success — swap CC file over original
                    try:
                        os.remove(output_path)
                        os.rename(cc_path, output_path)
                    except OSError as exc:
                        self._process_file_result(False, f"CC rename failed: {exc}", cb_ctx=mux_cb_ctx)
                        return

                # ─── mkvpropedit step (DAR) ───
                if s.get('skip_dar'):
                    # DAR already matches — skip mkvpropedit
                    mux_cb('done', 100)
                else:
                    dar_w, dar_h = parse_dar(s['dar_str'])
                    mkvpropedit = _get_mkvpropedit()
                    if mkvpropedit is None:
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        self._process_file_result(False, "mkvpropEdit.exe not found.", cb_ctx=mux_cb_ctx)
                        return

                    mkv_prop_start = _time.time()
                    mux_cb('mkvpropedit', 99, "Setting metadata…")

                    # Give Tk time to render the initial progress state before running
                    mkv_est = max(1, (_time.time() - mkv_prop_start) * 3) if mkv_prop_start else 1
                    sleep_target = max(0, min(0.8, mkv_est - (_time.time() - mkv_prop_start)))
                    while sleep_target > 0.02:
                        elapsed = _time.time() - mkv_prop_start
                        pct_pe = min(96, int((elapsed / mkv_est) * 100)) if mkv_est > 0 else 0
                        mux_cb('mkvpropedit', pct_pe, f"Setting metadata… {elapsed:.1f}s")
                        self.root.update_idletasks()
                        _time.sleep(0.05)
                        sleep_target = max(0, min(0.8, mkv_est - (_time.time() - mkv_prop_start)))

                    mux_cb('done', 100)

                    rc_pe, out_text_pe = run_mkvtl(
                        [mkvpropedit, output_path] +
                        ['--edit', 'track:v1', '--set', f'display-width={dar_w}',
                         '--set', f'display-height={dar_h}', '--set', 'display-unit=3'],
                        timeout=60)

                    if rc_pe != 0:
                        try:
                            os.remove(output_path)
                        except OSError:
                            pass
                        self._process_file_result(False,
                                                  f"mkvpropedit failed (rc={rc_pe}): {out_text_pe}",
                                                  cb_ctx=mux_cb_ctx)
                        return

                # Build concise success message.
                msg_parts = []

                if s.get('skip_dar'):
                    msg_parts.append("DAR Already Matches Desired Value")
                else:
                    dar_w, dar_h = parse_dar(s['dar_str'])
                    msg_parts.append(f"Display Aspect Ratio Set to {dar_w}:{dar_h}")

                # Subtitles — three states: removed / kept / not present.
                has_sub_note = any('Subtitles' in n for n in s.get('validation_notes', []))
                if s['remove_subs'] and not has_sub_note:
                    msg_parts.append("Subtitles: Removed")
                elif has_sub_note:
                    msg_parts.append("Subtitles: Not Present")
                else:
                    msg_parts.append("Subtitles: Not Removed")

                # Captions — three states: removed / kept / not present.
                has_cc_note = any('CC' in n for n in s.get('validation_notes', []))
                if s['remove_cc'] and not has_cc_note:
                    msg_parts.append("Closed Captions: Removed")
                elif has_cc_note:
                    msg_parts.append("Closed Captions: Not Present")
                else:
                    msg_parts.append("Closed Captions: Not Removed")

                # Audio selection (optional, shown only when a track was kept).
                if s['audio_sel'] is not None:
                    msg_parts.append(f"Audio Kept: Track #{s['audio_sel']}")

                # Delete original if requested
                if s.get('delete_originals'):
                    orig_path = os.path.abspath(s['fp'])
                    if os.path.isfile(orig_path):
                        try:
                            os.remove(orig_path)
                            msg_parts.append("Original Deleted: Yes")
                        except OSError as e:
                            msg_parts.append(f"Original Deleted: No ({e})")
                    else:
                        msg_parts.append("Original Deleted: No (file not found)")

                self._process_file_result(True, " ".join(msg_parts), cb_ctx=mux_cb_ctx)

            # ─── DONE_FILE: already handled by _process_file_result ───
            # (transition happens inside _process_file_result)

        except Exception as e:
            s['fail'] += 1
            self.root.after(0, lambda m=f"  ✗ Unexpected error: {e}": self._append_text(m + "\n", color="#f7768e"))
            # Clean up any running subprocesses before completing
            if s.get('mux_proc') is not None and s['mux_proc'].poll() is None:
                try:
                    s['mux_proc'].terminate()
                except Exception:
                    pass
            self._process_complete()

    def _process_file_result(self, ok, msg, cb_ctx=None):
        """Record the result for one file and transition to the next file or complete.

        All Tk updates happen on the main thread via after() so canvas pixels flush
        to screen immediately.
        """
        s = self._proc_state
        if not s:
            return

        if ok:
            s['ok'] += 1
        else:
            s['fail'] += 1

        # Track whether any file had actual work (not "no action necessary")
        if ok and not s.get('skip_this_file'):
            has_notes = msg.find("no action necessary") >= 0 or msg.find("— no action") >= 0
            if not has_notes:
                notes = s.get('validation_notes', [])
                if not any('(skipped)' in n for n in notes) and not s.get('skip_dar'):
                    pass  # mkvmerge ran — count tracked below

        # Decide whether progress bars should be updated for this file.
        # If ALL operations were skipped pre-flight, keep bars at zero.
        # For post-mkvmerge safety-net skips, still let bars update but _process_complete will fix them later.
        was_pre_flight_skip = s.get('skip_this_file')

        if was_pre_flight_skip:
            # No real progress — only advance the index and schedule complete.
            s['idx'] += 1
            if s['idx'] < s['total']:
                self.root.after(50, self._process_step)
            else:
                self.root.after(50, self._process_complete)
            return

        # Update current file label with captured percentage
        pct = cb_ctx.get('pct', 0) if cb_ctx else 0
        self.root.after(0, lambda p=pct: self.cur_text.config(text=f"Current File: {p}%") if p > 0 else None)

        # Update overall progress bar (chunk-based: file completed)
        total = s['total']
        idx_done = s['idx'] + 1  # this file just completed
        pct_done = int(idx_done * 100 / total)
        ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
        fw_ov = max(2, int(ow * pct_done / 100.0))
        color = GREEN if ok else RED
        self.root.after(0, lambda f=fw_ov: (
            self.over_canvas.delete("fill"),
            self.over_canvas.create_rectangle(1, 2, 1 + f, 13, fill="#7ab5cf", tag="fill")
        ))
        self.root.after(0, lambda pd=pct_done, c=color: (
            self.over_text.config(text=f"Overall: {pd}%", fg=c)
        ))

        # Append result to log
        if ok:
            self.root.after(0, lambda m=msg: self._append_text(f"  ✓ {m}\n", color="#9ece6a"))
        else:
            self.root.after(0, lambda m=msg: self._append_text(f"  ✗ {m}\n", color="#f7768e"))

        # Move to next file or complete
        s['idx'] += 1
        if s['idx'] < s['total']:
            # Schedule next file build on the main thread (after current events flush)
            self.root.after(50, self._process_step)
        else:
            # All files done — schedule complete after pending Tk events flush
            self.root.after(50, self._process_complete)

    def _process_complete(self):
        """Finalise batch processing — reset UI state and re-enable controls."""
        s = self._proc_state or {}
        ok = s.get('ok', 0)
        fail = s.get('fail', 0)
        total = s.get('total', 0)

        # Track whether any actual work was done (across ALL files, not just last).
        all_skipped = s.get('file_actions_count', 0) == 0

        # Clear any running subprocess
        if getattr(self, '_mux_proc', None) is not None and self._mux_proc.poll() is None:
            try:
                self._mux_proc.terminate()
            except Exception:
                pass

        if all_skipped:
            # Nothing was needed — keep bars at 0, show informative message.
            ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
            fw_ov = max(2, int(ow * 0 / 100.0))
            self.cur_canvas.create_rectangle(1, 2, 3, 13, fill="#1e2130", outline="")
            self.over_canvas.create_rectangle(1, 2, 3, 13, fill="#1e2130", outline="")
            self.root.after(0, lambda: self.cur_text.config(text="Current File: 0%"))
            self.root.after(0, lambda: self.over_text.config(text=f"Overall: 0%", fg="#7aa2f7"))
            self.root.after(0, lambda: self._append_text("\n═══ no action necessary ═══\n", color="#565f89"))
        else:
            # Final canvas updates (100% overall)
            ow = max(2, (self.over_canvas.winfo_width() - 2) if self.over_canvas.winfo_width() > 0 else 518)
            fw_ov = max(2, int(ow * 100 / 100.0))
            self.cur_canvas.create_rectangle(1, 2, 1 + fw_ov, 13, fill="#7ab5cf", outline="")
            self.over_canvas.create_rectangle(1, 2, 1 + fw_ov, 13, fill="#7ab5cf", outline="")

            # Schedule remaining UI updates on main thread (after any pending events)
            self.root.after(0, lambda: self._append_text(
                f"\n═══ complete: {ok} ok, {fail} failed ═══\n", color="#7aa2f7"))
            self.root.after(0, lambda o=ok, f=fail: self.info_var.set(f"Done — ✓ {o}   ✗ {f}"))
            # Reset overall label text color back to default blue after processing completes
            self.root.after(0, lambda: self.over_text.config(fg="#7aa2f7"))

        # Clear state and re-enable controls
        self._proc_state = {}
        self._mux_proc = None
        self.processing = False
        self._batch_ran = True  # mark that a batch has run so idle state shows "Processing complete."
        self._update_info()     # updates info_var + green binary status
        if fail > 0:
            self.root.after(0, lambda: self.proc_btn.config(state="normal", bg="#e5a536"))
        else:
            self.root.after(0, lambda: self.proc_btn.config(state="normal", bg="#9ece6a"))

    # -- Tk threading helpers --
    # -- audio track probing --
    def _probe_and_populate_audio(self):
        """Probe the first file for audio tracks and display DAR/Sub/CC info."""
        if not self.file_list:
            self.root.after(0, lambda: (
                self.audio_var.set("Keep All"),
                self.audio_combo.config(values=("Keep All",))
            ))
            return

        mkv_path = self.file_list[0]
        if not os.path.isfile(mkv_path):
            return

        # Probe audio tracks
        audio_tracks = probe_audio_tracks(mkv_path)
        options = [("Keep All", None)]  # (display_label, audio_index)
        for track in audio_tracks:
            lang = (track.get('language') or '').lower()
            tid = track.get('id')

            if not lang or lang in ('und', 'unk'):
                display = f"Track #{tid}"
            elif len(lang) == 2:
                display = f"Track #{tid} ({_ISO_639_1_TO_NAME.get(lang, lang.upper())})"
            else:
                name = (_ISO_639_2_TO_NAME.get(lang) or lang.title()) if len(lang) == 3 else lang.upper()
                display = f"Track #{tid} ({name})"

            options.append((display, tid))

        # Also probe for DAR / Subtitle / CC info (replaces the old probe_mkv usage).
        info_dict, err = probe_mkv(mkv_path)
        file_count = len(self.file_list)

        if info_dict and not err:
            # Build DAR string from actual display dimensions, fallback to pixel resolution.
            dar_str = "n/a"
            ad_w = info_dict.get('actual_display_w', 0)
            ad_h = info_dict.get('actual_display_h', 0)
            if ad_w > 0 and ad_h > 0:
                g = self._gcd_dar(ad_w, ad_h)
                dar_str = f"{ad_w // g}:{ad_h // g}"
            elif info_dict.get('width', 0) > 0 and info_dict.get('height', 0) > 0:
                pw = info_dict['width']
                ph = info_dict['height']
                g = self._gcd_dar(pw, ph)
                dar_str = f"{pw // g}:{ph // g} (from {pw}x{ph})"

            subs_found = bool(info_dict.get('subtitle_track_ids'))
            cc_found = info_dict.get('has_cc', False)

            # Update status line below file list with DAR / Sub / CC info.
            parts = [f"{file_count} file(s) added"]
            if dar_str != "n/a":
                parts.append(f"Current DAR: {dar_str}")
            parts.append(f"Has Subtitles: {'Yes' if subs_found else 'No'}")
            parts.append(f"Has CC: {'Yes' if cc_found else 'No'}")
            self.info_var.set("  |  ".join(parts))
        else:
            # Probe failed — show only file count + audio.
            self.info_var.set(f"{file_count} file(s) added (probe: {err})")

        # Update audio combo.
        self.root.after(0, lambda: (
            self.audio_var.set("Keep All"),
            self.audio_combo.config(values=tuple(opt[0] for opt in options))
        ))
        self._audio_track_data = dict(options)

    def _gcd_dar(self, a, b):
        """Compute GCD of two integers (Euclidean algorithm)."""
        while b:
            a, b = b, a % b
        return a

    def _matches_dar(self, cur_w, cur_h, dar_str):
        """Check if current display dimensions match the desired DAR string."""
        dw, dh = parse_dar(dar_str)
        return cur_w * dh == cur_h * dw

    def _btn_mode(self, text, value, master, inactive=False):
        """Create one half of the Batch/Individual toggle.

        Uses plain tk.Button (not ttk) so bg/fg are always respected on Windows.
        Active = ACCENT blue bg + dark text, Inactive = CARD bg + light text.
        """
        btn_bg = "#24283b" if inactive else "#7aa2f7"
        btn_fg = "#c0caf5" if inactive else "#1a1b26"

        btn = tk.Button(
            master, text=text, width=14, font=("Segoe UI", 9),
            bg=btn_bg, fg=btn_fg, activebackground="#7aa2f7", activeforeground="#1a1b26",
            relief="flat", cursor="hand2", padx=10, pady=3, bd=0,
            command=lambda v=value: self._switch_mode(v),
        )
        btn.pack(side="left", padx=(3 if inactive else 0, 0))
        setattr(self, f"_mode_btn_{value}", btn)

    def _switch_mode(self, value):
        """Switch between batch and individual mode and update UI."""
        old_value = self.mode_var.get()
        if old_value == value:
            return

        # Save any pending per-file settings before switching modes
        if self.file_list and 0 <= self.selected_file_index < len(self.file_list):
            sel_path = self.file_list[self.selected_file_index]
            display = self.audio_var.get()
            data = getattr(self, '_audio_track_data', {"Keep All": None})
            audio_sel = data.get(display, None)
            self.file_settings[sel_path] = {
                'dar': self.dar_str.get(),
                'audio_sel': audio_sel,
                'remove_subs': self.remove_subs_var.get(),
                'remove_cc': self.remove_cc_var.get(),
                'delete_originals': self.delete_originals_var.get(),
            }

        # Block trace saves during mode-dependent control updates
        self._restoring_file = True

        self.mode_var.set(value)

        ACCENT_COLOR = "#7aa2f7"
        CARD_COLOR = "#24283b"
        FG_COLOR = "#c0caf5"

        for v in ("batch", "individual"):
            btn = getattr(self, f"_mode_btn_{v}")
            is_active = (v == value)
            if is_active:
                # Active: ACCENT bg + dark text
                btn.config(bg=ACCENT_COLOR, fg="#1a1b26", activebackground=ACCENT_COLOR, activeforeground="#1a1b26")
            else:
                # Inactive: CARD bg + light text
                btn.config(bg=CARD_COLOR, fg=FG_COLOR, activebackground=CARD_COLOR, activeforeground=FG_COLOR)

        # Sync selected_file_index to mode
        if value == "batch":
            self.selected_file_index = -1
        else:
            if self.file_list:
                self.selected_file_index = 0
            else:
                self.selected_file_index = -1

        # Unblock trace saves after mode-dependent control updates
        self._restoring_file = False

        self._highlight_selected()
        self.root.update()

    def _tk(self, fn):
        self.root.after(0, fn)

    def _append_text(self, msg, color=None, tag_name=None):
        """Append text to the file list area.

        *tag_name*: optional full file path to attach as a Tk tag on this insertion.
        Clicking text with a tag_name will select that file for per-file settings.
        """
        self.txt.config(state="normal")
        if color:
            self.txt.tag_configure(color, foreground=color)
            self.txt.insert("end", msg, color)
            if tag_name:
                # Tag the last inserted span (from 'end-1c' back to the start of this insertion)
                line_start = self.txt.index("end-1c linestart")
                self.txt.tag_add(tag_name, line_start, "end-1c")
        else:
            self.txt.insert("end", msg)
        self.txt.config(state="disabled")
        self.txt.see("end")

    def _on_text_click(self, event):
        """Handle clicks on the text widget to select individual files.

        Only works when in Individual mode — batch mode ignores clicks.
        Saves current file's settings before restoring the new file.
        """
        if self.mode_var.get() != "individual" or not self.file_list:
            return

        # First save any pending changes for the currently selected file
        if 0 <= self.selected_file_index < len(self.file_list):
            self._save_current_file_settings()

        idx = self.txt.index(f"@{event.x},{event.y}")
        line_num_str, _ = idx.split(".")
        line_num = int(line_num_str)
        if 1 <= line_num <= len(self.file_list):
            new_idx = line_num - 1
            sel_path = self.file_list[new_idx]

            fs = self.file_settings.get(sel_path)

            if fs and not self.processing:
                # Restore per-file settings into controls — block trace saves mid-restoration
                self._restoring_file = True

                self.dar_str.set(fs['dar'])
                self.remove_subs_var.set(fs['remove_subs'])
                self.remove_cc_var.set(fs.get('remove_cc', False))
                self.delete_originals_var.set(fs.get('delete_originals', False))
                data = getattr(self, '_audio_track_data', {"Keep All": None})
                for display, tid in data.items():
                    if tid == fs['audio_sel']:
                        self.audio_var.set(display)
                        break
                else:
                    self.audio_var.set("Keep All")

                self._restoring_file = False
            elif not self.processing:
                # No per-file override — reset all controls to their defaults
                self._restoring_file = True

                self.dar_str.set(self._dar_last_valid)
                self.audio_var.set("Keep All")
                self.remove_subs_var.set(False)
                self.remove_cc_var.set(False)
                self.delete_originals_var.set(False)

                self._restoring_file = False

            self.selected_file_index = new_idx
            self._highlight_selected()
            self.root.update()

    def _highlight_selected(self):
        """Update overlay tags to highlight the selected file."""
        # Remove old overlays
        for tag in ("sel_bg", "sel_fg_green", "sel_fg_blue"):
            try:
                self.txt.tag_lower(tag, "1.0")
            except tk.TclError:
                pass

        # Reset all overlay tags to empty ranges
        for tag in ("sel_bg", "sel_fg_green", "sel_fg_blue"):
            self.txt.tag_remove(tag, "1.0", "end-1c")

        if 0 <= self.selected_file_index < len(self.file_list):
            sel_path = self.file_list[self.selected_file_index]
            line_num = self.selected_file_index + 1
            line_start = f"{line_num}.0"
            line_end = f"{line_num}.end"

            # Yellow-green background for selected file
            self.txt.tag_configure("sel_bg", background="#2d313a")
            self.txt.tag_add("sel_bg", line_start, line_end)

            # Check if this file's stored settings differ from what's currently displayed.
            # Green when different (has override), blue when matching (no override).
            fs = self.file_settings.get(sel_path)
            if fs:
                has_diff = (fs.get('dar') != self.dar_str.get() or
                           fs.get('remove_subs') != self.remove_subs_var.get() or
                           fs.get('remove_cc') != self.remove_cc_var.get() or
                           fs.get('audio_sel') != self.audio_var.get())
            else:
                has_diff = False

            # Green text for files with custom settings, blue for global default
            fg_tag = "sel_fg_green" if has_diff else "sel_fg_blue"
            self.txt.tag_configure(fg_tag, foreground="#9ece6a" if has_diff else "#7aa2f7")
            self.txt.tag_add(fg_tag, line_start, line_end)
            # Put foreground tag above background so text stays visible
            try:
                self.txt.tag_raise(fg_tag)
            except tk.TclError:
                pass


# ─── Entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.mainloop()
