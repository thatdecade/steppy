"""
qr_code.py

Generates QR code images linking to the local control webpage.

Purpose
- Build a URL from config (usually http://<host>:<port>/)
- Generate a QR code as SVG (easy to recolor and scale)
- Render the SVG into a QImage for direct display in Qt overlays
- Save optional outputs to a temp folder for debugging

Integration
- Used by main_window.py now (returns QImage)
- Used later by app_controller.py for idle overlay QR refresh

Standalone usage
python -m qr_code

No command line arguments are required or used.
"""

from __future__ import annotations

import json
import socket
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import qrcode
from qrcode.constants import ERROR_CORRECT_M

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage, QPainter

from config import AppConfig, get_config


_DEFAULT_FILL_COLOR = "#050313"
_DEFAULT_BACKGROUND_COLOR = "#F3F0FC"


@dataclass(frozen=True)
class QrResult:
    ok: bool
    url: str
    svg_path: str
    png_path: str
    fill_color: str
    background_color: str
    warning: Optional[str] = None
    error: Optional[str] = None


def _best_effort_local_ip() -> str:
    """
    Returns a best-effort LAN IP for the current machine without contacting the internet.
    Falls back to 127.0.0.1.
    """
    fallback_ip = "127.0.0.1"
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            udp_socket.connect(("192.0.2.1", 9))  # TEST-NET-1, non-routable
            local_ip = udp_socket.getsockname()[0]
            if local_ip and isinstance(local_ip, str):
                return local_ip
        finally:
            udp_socket.close()
    except Exception:
        return fallback_ip
    return fallback_ip


def build_control_url(app_config: AppConfig) -> str:
    """
    Build the control URL shown as QR code.

    Strategy:
    - Use configured web_server.host + port
    - If host is 0.0.0.0, replace with a best-effort LAN IP so phones can reach it
    - Always include trailing slash
    """
    host_text = (app_config.web_server.host or "").strip() or "127.0.0.1"
    port_number = int(app_config.web_server.port)

    if host_text == "0.0.0.0":
        host_text = _best_effort_local_ip()

    return f"http://{host_text}:{port_number}/"


def _validate_url(url: str) -> str:
    cleaned_url = (url or "").strip()
    if not cleaned_url:
        raise ValueError("URL is empty.")

    parsed = urlparse(cleaned_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("URL must start with http:// or https://")

    return cleaned_url


def _default_output_directory() -> Path:
    return Path(tempfile.gettempdir()) / "steppy"


def _timestamped_output_path(output_directory: Path, prefix: str, extension: str) -> Path:
    timestamp_msec = int(time.time() * 1000)
    return output_directory / f"{prefix}{timestamp_msec}.{extension}"


def generate_qr_svg(
    url: str,
    *,
    output_directory: Optional[Path] = None,
    filename_prefix: str = "steppy_qr_",
    fill_color: str = _DEFAULT_FILL_COLOR,
    background_color: str = _DEFAULT_BACKGROUND_COLOR,
    box_size: int = 10,
    border: int = 2,
) -> Path:
    """
    Generates an SVG QR code file encoding the provided URL.
    """
    cleaned_url = _validate_url(url)

    target_directory = output_directory if output_directory is not None else _default_output_directory()
    target_directory.mkdir(parents=True, exist_ok=True)

    try:
        import qrcode.image.svg  # type: ignore
    except Exception as exception:
        raise RuntimeError(f"SVG support not available in qrcode package: {exception}") from exception

    qr_object = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_M,
        box_size=int(max(1, box_size)),
        border=int(max(0, border)),
    )
    qr_object.add_data(cleaned_url)
    qr_object.make(fit=True)

    output_path = _timestamped_output_path(target_directory, filename_prefix, "svg")

    svg_factory = qrcode.image.svg.SvgPathImage  # type: ignore[attr-defined]
    svg_image = qr_object.make_image(
        image_factory=svg_factory,
        fill_color=str(fill_color),
        back_color=str(background_color),
    )
    svg_image.save(str(output_path))
    return output_path


def _render_svg_bytes_to_qimage(svg_bytes: bytes, target_size_px: int) -> QImage:
    try:
        from PyQt6.QtSvg import QSvgRenderer
    except Exception as exception:
        raise RuntimeError(f"QtSvg is required to render SVG QR codes: {exception}") from exception

    renderer = QSvgRenderer(svg_bytes)
    if not renderer.isValid():
        raise RuntimeError("QSvgRenderer could not parse the SVG data")

    image = QImage(target_size_px, target_size_px, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()

    return image


def generate_qr_qimage(
    url: str,
    *,
    target_size_px: int,
    fill_color: str = _DEFAULT_FILL_COLOR,
    background_color: str = _DEFAULT_BACKGROUND_COLOR,
    output_directory: Optional[Path] = None,
) -> QImage:
    """
    Generate a QR code as a QImage directly, using the requested colors.

    Implementation:
    - Generate an SVG QR (with the final colors baked in)
    - Render SVG into a QImage via QtSvg

    main_window.py should use this and never touch SVG parsing.
    """
    cleaned_url = _validate_url(url)

    svg_path = generate_qr_svg(
        cleaned_url,
        output_directory=output_directory,
        fill_color=fill_color,
        background_color=background_color,
    )

    svg_bytes = Path(svg_path).read_bytes()
    return _render_svg_bytes_to_qimage(svg_bytes, int(max(32, target_size_px)))

  

def load_qimage_from_png(png_path: Path) -> QImage:
    """
    Convenience helper for Qt modules. Loads a QImage from a PNG on disk.
    """
    image = QImage(str(png_path))
    if image.isNull():
        raise ValueError("Failed to load QImage from PNG: " + str(png_path))
    return image

def generate_control_qr_qimage(
    app_config: AppConfig,
    *,
    target_size_px: int,
    fill_color: str = _DEFAULT_FILL_COLOR,
    background_color: str = _DEFAULT_BACKGROUND_COLOR,
    output_directory: Optional[Path] = None,
) -> Tuple[str, QImage]:
    """
    Generate the control URL QR as (url, qimage).
    """
    url = build_control_url(app_config)
    image = generate_qr_qimage(
        url,
        target_size_px=target_size_px,
        fill_color=fill_color,
        background_color=background_color,
        output_directory=output_directory,
    )
    return url, image


def main() -> int:
    try:
        app_config, _config_path = get_config()
    except Exception as exception:
        payload = QrResult(
            ok=False,
            url="",
            svg_path="",
            png_path="",
            fill_color=_DEFAULT_FILL_COLOR,
            background_color=_DEFAULT_BACKGROUND_COLOR,
            error=str(exception),
        )
        print(json.dumps(payload.__dict__, ensure_ascii=False, indent=2))
        return 2

    try:
        url = build_control_url(app_config)
        output_directory = _default_output_directory()
        svg_path = generate_qr_svg(
            url,
            output_directory=output_directory,
            fill_color=_DEFAULT_FILL_COLOR,
            background_color=_DEFAULT_BACKGROUND_COLOR,
        )

        # This proves Qt can render it even if the app never saves PNGs.
        _ = generate_qr_qimage(
            url,
            target_size_px=256,
            fill_color=_DEFAULT_FILL_COLOR,
            background_color=_DEFAULT_BACKGROUND_COLOR,
            output_directory=output_directory,
        )

        payload = QrResult(
            ok=True,
            url=url,
            svg_path=str(svg_path),
            png_path="",
            fill_color=_DEFAULT_FILL_COLOR,
            background_color=_DEFAULT_BACKGROUND_COLOR,
            warning=None,
            error=None,
        )
        print(json.dumps(payload.__dict__, ensure_ascii=False, indent=2))
        return 0
    except Exception as exception:
        payload = QrResult(
            ok=False,
            url="",
            svg_path="",
            png_path="",
            fill_color=_DEFAULT_FILL_COLOR,
            background_color=_DEFAULT_BACKGROUND_COLOR,
            error=str(exception),
        )
        print(json.dumps(payload.__dict__, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
