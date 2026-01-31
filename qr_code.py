# -*- coding: utf-8 -*-
########################
# qr_code.py
########################
# Purpose:
# - Generate a QR code image that links to the local control webpage.
# - Returns a QImage suitable for display in the Qt idle overlay.
#
# Test Notes:
# - In this module, qr_code is tested for control URL construction and determinism.
# - In Desktop shell and orchestration, it is tested for idle screen display behavior.
#
########################
# Design notes:
# - QR generation must be deterministic for a given URL.
# - Prefer explicit validation and error reporting in QrResult over silent fallback.
#
########################
# Interfaces:
# Public dataclasses:
# - QrResult(ok: bool, url: str, svg_path: str, png_path: str, fill_color: str, background_color: str, error: Optional[str] = None)
#
# Public functions:
# - build_control_url(app_config: AppConfig) -> str
# - load_qimage_from_png(png_path: pathlib.Path) -> PyQt6.QtGui.QImage
# - generate_control_qr_qimage(*, url: str, fill_color: str = ..., background_color: str = ...) -> tuple[PyQt6.QtGui.QImage, QrResult]
# - main() -> int
#
# Inputs:
# - AppConfig.web_server for host and port.
# - URL string.
#
# Outputs:
# - QImage and a QrResult payload describing saved temp artifacts.
#
########################

from __future__ import annotations

from dataclasses import dataclass
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from PyQt6.QtGui import QImage

import config as config_module


@dataclass(frozen=True)
class QrResult:
    ok: bool
    url: str
    svg_path: str
    png_path: str
    fill_color: str
    background_color: str
    error: Optional[str] = None


def build_control_url(app_config: "config_module.AppConfig") -> str:
    host = str(app_config.web_server.host).strip()
    port = int(app_config.web_server.port)

    # 0.0.0.0 is not routable from a phone QR scan, so normalize to localhost by default.
    if host in {"0.0.0.0", "127.0.0.1", "::"}:
        host = "localhost"

    return f"http://{host}:{port}/"


def load_qimage_from_png(png_path: Path) -> QImage:
    qimage = QImage(str(png_path))
    return qimage


def generate_control_qr_qimage(
    *,
    url: str,
    fill_color: str = "#000000",
    background_color: str = "#ffffff",
) -> Tuple[QImage, QrResult]:
    url_text = str(url or "").strip()
    if not url_text:
        empty_result = QrResult(
            ok=False,
            url="",
            svg_path="",
            png_path="",
            fill_color=str(fill_color),
            background_color=str(background_color),
            error="Empty URL",
        )
        return QImage(), empty_result

    try:
        import qrcode
        import qrcode.image.svg
    except Exception as exc:  # pragma: no cover
        error_result = QrResult(
            ok=False,
            url=url_text,
            svg_path="",
            png_path="",
            fill_color=str(fill_color),
            background_color=str(background_color),
            error=f"qrcode import failed: {exc}",
        )
        return QImage(), error_result

    artifacts_dir = Path(tempfile.mkdtemp(prefix="steppy_qr_"))
    png_path = artifacts_dir / "control_qr.png"
    svg_path = artifacts_dir / "control_qr.svg"

    qr_config = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr_config.add_data(url_text)
    qr_config.make(fit=True)

    # PNG output
    png_image = qr_config.make_image(fill_color=str(fill_color), back_color=str(background_color))
    png_image.save(str(png_path))

    # SVG output (optional, but useful for crisp printing)
    svg_factory = qrcode.image.svg.SvgImage
    svg_image = qr_config.make_image(image_factory=svg_factory)
    svg_image.save(str(svg_path))

    qimage = load_qimage_from_png(png_path)

    result = QrResult(
        ok=not qimage.isNull(),
        url=url_text,
        svg_path=str(svg_path),
        png_path=str(png_path),
        fill_color=str(fill_color),
        background_color=str(background_color),
        error=None if not qimage.isNull() else "Failed to load QImage from PNG",
    )
    return qimage, result


def main() -> int:
    app_config, _config_path = config_module.get_config()
    url = build_control_url(app_config)
    _qimage, result = generate_control_qr_qimage(url=url)
    if not result.ok:
        print(f"QR failed: {result.error}")
        return 1
    print(f"QR URL: {result.url}")
    print(f"PNG: {result.png_path}")
    print(f"SVG: {result.svg_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
