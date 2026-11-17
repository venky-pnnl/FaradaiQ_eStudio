"""FaradaIQ Edison Scientific report helpers.

No network calls here.  These helpers are intentionally dependency-light and use
matplotlib, which is already part of the FaradaIQ private environment, to write
multi-page PDF reports from Edison responses and FaradaIQ comparison artifacts.
"""
from __future__ import annotations

import json
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Any


def _safe_read_text(path: Path, limit: int | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    if limit and len(text) > limit:
        return text[:limit] + "\n...[truncated for report]..."
    return text


def format_edison_response(result: Mapping[str, Any] | Any, *, title: str = "FaradaIQ Edison Scientific response") -> str:
    """Return a readable, deterministic response text for the GUI text box."""
    if not isinstance(result, Mapping):
        return str(result)
    lines: list[str] = [title, "=" * len(title), ""]
    status = result.get("status") or result.get("task_status") or result.get("state") or "unknown"
    lines.append(f"Status: {status}")
    if result.get("mode") or result.get("mode_requested"):
        lines.append(f"Mode: {result.get('mode') or result.get('mode_requested')}")
    for key, label in [
        ("trajectory_id", "Edison trajectory/task ID"),
        ("task_id", "Edison task ID"),
        ("platform_url", "Edison platform URL"),
        ("zip_path", "Submitted ZIP"),
        ("handoff_dir", "Local response folder"),
        ("h5_path", "Input H5"),
        ("json_path", "Input JSON"),
    ]:
        val = result.get(key)
        if val:
            lines.append(f"{label}: {val}")
    if result.get("edison_api_key_present") is not None:
        lines.append(f"Edison API key found: {bool(result.get('edison_api_key_present'))}")
    lines.append("")

    answer = result.get("answer") or result.get("formatted_answer") or result.get("output") or result.get("message") or ""
    if answer:
        lines.append("Edison answer")
        lines.append("-------------")
        if isinstance(answer, str):
            lines.append(answer.strip())
        else:
            lines.append(json.dumps(answer, ensure_ascii=False, indent=2))
        lines.append("")

    if result.get("error"):
        lines.append("Error")
        lines.append("-----")
        lines.append(str(result.get("error")))
        if result.get("traceback"):
            lines.append("")
            lines.append(str(result.get("traceback"))[:6000])
        lines.append("")

    # Include compact file/artifact summary when present.
    for key in ("comparison_artifacts", "files_sent", "artifacts_sent", "missing"):
        val = result.get(key)
        if val:
            lines.append(key.replace("_", " ").title())
            lines.append("-" * len(lines[-1]))
            try:
                lines.append(json.dumps(val, ensure_ascii=False, indent=2)[:12000])
            except Exception:
                lines.append(str(val))
            lines.append("")

    lines.append("Raw response JSON")
    lines.append("-----------------")
    try:
        lines.append(json.dumps(result, ensure_ascii=False, indent=2)[:30000])
    except Exception:
        lines.append(str(result))
    return "\n".join(lines).rstrip() + "\n"


def _pdf_text_page(pdf, title: str, text: str, *, footer: str = "") -> None:
    import matplotlib.pyplot as plt

    max_chars = 82
    max_lines = 44
    raw_lines: list[str] = []
    for para in str(text or "").splitlines():
        if not para.strip():
            raw_lines.append("")
        else:
            raw_lines.extend(textwrap.wrap(para, width=max_chars, replace_whitespace=False, drop_whitespace=False) or [""])
    chunks = [raw_lines[i:i + max_lines] for i in range(0, max(1, len(raw_lines)), max_lines)]
    if not chunks:
        chunks = [[]]
    for page_no, chunk in enumerate(chunks, start=1):
        fig = plt.figure(figsize=(8.5, 11))
        fig.patch.set_facecolor("white")
        fig.text(0.07, 0.965, title if page_no == 1 else f"{title} (continued)", ha="left", va="top", fontsize=15, weight="bold")
        y = 0.925
        for line in chunk:
            fig.text(0.07, y, line, ha="left", va="top", fontsize=8.4, family="monospace")
            y -= 0.019
        if footer:
            fig.text(0.07, 0.035, footer, ha="left", va="bottom", fontsize=7.5, color="0.35")
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def _pdf_image_page(pdf, image_path: Path, caption: str = "") -> None:
    import matplotlib.pyplot as plt
    import matplotlib.image as mpimg

    fig = plt.figure(figsize=(8.5, 11))
    fig.patch.set_facecolor("white")
    try:
        img = mpimg.imread(str(image_path))
        ax = fig.add_axes([0.07, 0.15, 0.86, 0.74])
        ax.imshow(img)
        ax.axis("off")
        fig.text(0.07, 0.94, "Input key comparison plot", ha="left", va="top", fontsize=15, weight="bold")
        fig.text(0.07, 0.105, caption or image_path.name, ha="left", va="top", fontsize=9)
    except Exception as exc:
        fig.text(0.07, 0.94, "Input key comparison plot", ha="left", va="top", fontsize=15, weight="bold")
        fig.text(0.07, 0.88, f"Could not render image: {image_path}\n{exc}", ha="left", va="top", fontsize=9)
    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def make_edison_pdf_report(
    response_text: str,
    response_dir: str | Path,
    *,
    h5_path: str | Path | None = None,
    json_path: str | Path | None = None,
    output_pdf: str | Path | None = None,
    max_images: int = 8,
) -> Path:
    """Create a PDF report from the Edison response plus FaradaIQ inputs."""
    from matplotlib.backends.backend_pdf import PdfPages

    response_dir = Path(response_dir).resolve()
    response_dir.mkdir(parents=True, exist_ok=True)
    if output_pdf is None:
        output_pdf = response_dir / f"FaradaIQ_Edison_Response_Report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
    output_pdf = Path(output_pdf).resolve()

    manifest_summary = ""
    if json_path:
        jp = Path(json_path)
        if jp.exists():
            try:
                data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
                slim = {
                    "base_name": data.get("base_name"),
                    "sample_name": data.get("sample_name"),
                    "techniques_detected": data.get("techniques_detected"),
                    "technique_counts": data.get("technique_counts"),
                    "h5_counts": data.get("h5_counts"),
                    "source_policy": data.get("source_policy"),
                    "gui_metadata": data.get("gui_metadata"),
                }
                manifest_summary = json.dumps(slim, ensure_ascii=False, indent=2)
            except Exception:
                manifest_summary = _safe_read_text(jp, limit=12000)

    artifact_index = response_dir / "input_key_comparison_artifacts" / "input_key_comparison_artifacts_index.json"
    artifact_text = _safe_read_text(artifact_index, limit=15000) if artifact_index.exists() else "No separate comparison-artifact index was found. The canonical H5/JSON remain the primary input."

    images: list[Path] = []
    artifact_root = response_dir / "input_key_comparison_artifacts"
    if artifact_root.exists():
        for pattern in ("**/*.png", "**/*.jpg", "**/*.jpeg"):
            images.extend(sorted(artifact_root.glob(pattern)))
    # Also include plots saved directly in response folder, if any.
    for pattern in ("*.png", "*.jpg", "*.jpeg"):
        images.extend(sorted(response_dir.glob(pattern)))
    seen = set()
    unique_images = []
    for p in images:
        rp = str(p.resolve())
        if rp not in seen:
            seen.add(rp)
            unique_images.append(p)
    unique_images = unique_images[:max_images]

    footer = "FaradaIQ Studio Edison report - generated locally from canonical H5/JSON and Edison response"
    with PdfPages(str(output_pdf)) as pdf:
        cover = (
            f"Created: {datetime.now().isoformat(timespec='seconds')}\n"
            f"Response folder: {response_dir}\n"
            f"Input H5: {h5_path or ''}\n"
            f"Input JSON: {json_path or ''}\n\n"
            "Report contents:\n"
            "1. Edison response text, optionally LLM-reformatted by FaradaIQ.\n"
            "2. Canonical bundle metadata.\n"
            "3. Input key comparison plots/artifacts included in the Edison handoff.\n"
            "4. Experimental-plan content requested from Edison in the prompt.\n"
        )
        _pdf_text_page(pdf, "FaradaIQ Edison Scientific report", cover, footer=footer)
        _pdf_text_page(pdf, "Edison response", response_text, footer=footer)
        if manifest_summary:
            _pdf_text_page(pdf, "Canonical H5/JSON bundle metadata", manifest_summary, footer=footer)
        _pdf_text_page(pdf, "Input key comparison artifacts", artifact_text, footer=footer)
        for img in unique_images:
            try:
                caption = str(img.relative_to(response_dir))
            except Exception:
                caption = img.name
            _pdf_image_page(pdf, img, caption)
    return output_pdf
