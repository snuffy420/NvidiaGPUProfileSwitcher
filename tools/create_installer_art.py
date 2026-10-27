"""Code-drawn installer branding using the existing app icon."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication
from PyQt6.QtSvg import QSvgRenderer


def main():
    application = QApplication.instance() or QApplication(sys.argv[:1])
    output = ROOT / "tools/installer_art"
    output.mkdir(parents=True, exist_ok=True)
    # The application's own artwork is a GPU card with switching arrows, not a vendor logo.
    source = QSvgRenderer(str(ROOT / "assets/app-icon.svg"))
    rendered = QImage(1024, 1024, QImage.Format.Format_ARGB32)
    rendered.fill(Qt.GlobalColor.transparent)
    vector_painter = QPainter(rendered)
    source.render(vector_painter)
    vector_painter.end()
    assert rendered.save(str(ROOT / "assets/app-icon.png"), "PNG")
    from PIL import Image
    Image.open(ROOT / "assets/app-icon.png").save(ROOT / "assets/app-icon.ico", format="ICO", sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    image = QImage(492, 942, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 492, 942)
    gradient.setColorAt(0, QColor("#171e28"))
    gradient.setColorAt(1, QColor("#0b1016"))
    painter.fillRect(image.rect(), gradient)
    painter.fillRect(0, 0, 10, 942, QColor("#76b900"))
    painter.setPen(QPen(QColor("#243628"), 2))
    for offset in range(-400, 900, 90):
        painter.drawLine(offset, 760, offset + 350, 1110)
    icon = QPixmap(str(ROOT / "assets/app-icon.ico"))
    painter.drawPixmap(48, 70, 174, 174, icon)
    def text(y, content, size, color, bold=False):
        painter.setFont(QFont("Segoe UI", size, QFont.Weight.Bold if bold else QFont.Weight.Normal))
        painter.setPen(QColor(color))
        painter.drawText(QRectF(48, y, 400, 90), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, content)
    text(294, "GPU DRIVER", 24, "#eef2f7", True)
    text(358, "PROFILE", 24, "#eef2f7", True)
    text(422, "SWITCHER", 24, "#eef2f7", True)
    painter.fillRect(48, 548, 74, 5, QColor("#76b900"))
    text(578, "VERSION 2.0", 14, "#99e51a", True)
    text(642, "WINDOWS 10 / 11", 11, "#9ba9ba")
    text(690, "64-BIT", 11, "#9ba9ba")
    text(820, "zerØ software", 12, "#9ba9ba")
    painter.end()
    assert image.save(str(output / "wizard.bmp"), "BMP")
    small = QImage(192, 192, QImage.Format.Format_RGB32)
    small.fill(QColor("#0f1217"))
    painter = QPainter(small)
    painter.drawPixmap(12, 12, 168, 168, icon)
    painter.end()
    assert small.save(str(output / "wizard-small.bmp"), "BMP")
    print("Created original GPU/profile icon, multi-size ICO, and matching installer artwork.")


if __name__ == "__main__":
    main()
