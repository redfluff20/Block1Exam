"""PPTX -> PDF conversion via Microsoft PowerPoint (or Keynote fallback) AppleScript.

Requires Microsoft PowerPoint installed (macOS). The app requires all lectures to
be PDF before import; PowerPoint is preferred, Keynote is used as a fallback for
files PowerPoint cannot export.
"""
import os
import subprocess


def _osascript(script):
    return subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
    )


def _pptx_to_pdf_powerpoint(src_path, out_path):
    script = f"""
set timeoutSeconds to 300
with timeout of timeoutSeconds seconds
    tell application "Microsoft PowerPoint"
        open POSIX file "{os.path.abspath(src_path)}"
        set theDoc to active presentation
        save theDoc in POSIX file "{os.path.abspath(out_path)}" as save as PDF
        close theDoc saving no
    end tell
end timeout
"""
    return _osascript(script)


def _pptx_to_pdf_keynote(src_path, out_path):
    script = f"""
set timeoutSeconds to 300
with timeout of timeoutSeconds seconds
    tell application "Keynote"
        open POSIX file "{os.path.abspath(src_path)}"
        set theDoc to front document
        export theDoc to POSIX file "{os.path.abspath(out_path)}" as PDF
        close theDoc
    end tell
end timeout
"""
    return _osascript(script)


def pptx_to_pdf(src_path, out_path=None):
    """Convert a .pptx to .pdf using PowerPoint AppleScript, falling back to Keynote.

    Returns the path to the generated PDF. Raises on failure."""
    if not out_path:
        out_path = os.path.splitext(src_path)[0] + ".pdf"
    out_path = os.path.abspath(out_path)

    result = _pptx_to_pdf_powerpoint(src_path, out_path)
    if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    # Fallback: Keynote
    result = _pptx_to_pdf_keynote(src_path, out_path)
    if result.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    raise RuntimeError(
        f"PPTX->PDF conversion failed: {result.stderr.strip() or 'unknown error'}"
    )
