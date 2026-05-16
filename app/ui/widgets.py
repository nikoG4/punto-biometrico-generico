from __future__ import annotations

import cv2
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel


class VideoLabel(QLabel):
    def set_frame(
        self,
        frame_bgr,
        bbox: tuple[int, int, int, int] | None = None,
        guide: bool = True,
    ) -> None:
        frame = frame_bgr.copy()
        if guide:
            self._draw_face_guide(frame)
        if bbox:
            x1, y1, x2, y2 = bbox
            cv2.rectangle(frame, (x1, y1), (x2, y2), (52, 211, 153), 3)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        image = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888)
        self.setPixmap(QPixmap.fromImage(image).scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio))

    @staticmethod
    def guide_bbox(frame_shape) -> tuple[int, int, int, int]:
        height, width = frame_shape[:2]
        guide_height = int(height * 0.58)
        guide_width = int(guide_height * 0.72)
        guide_width = min(guide_width, int(width * 0.45))
        guide_height = int(guide_width / 0.72)
        cx = width // 2
        cy = int(height * 0.48)
        x1 = cx - guide_width // 2
        y1 = cy - guide_height // 2
        x2 = cx + guide_width // 2
        y2 = cy + guide_height // 2
        return x1, y1, x2, y2

    def _draw_face_guide(self, frame) -> None:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = self.guide_bbox(frame.shape)
        cx = (x1 + x2) // 2
        cy = (y1 + y2) // 2
        rx = (x2 - x1) // 2
        ry = (y2 - y1) // 2

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (width, height), (0, 0, 0), -1)
        mask = frame.copy()
        mask[:] = 0
        cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, (255, 255, 255), -1)
        cv2.ellipse(
            mask,
            (cx, min(height - 1, y2 + int(ry * 0.42))),
            (int(rx * 1.35), int(ry * 0.42)),
            0,
            200,
            340,
            (255, 255, 255),
            -1,
        )
        dimmed = cv2.addWeighted(overlay, 0.34, frame, 0.66, 0)
        frame[mask[:, :, 0] == 0] = dimmed[mask[:, :, 0] == 0]

        color = (52, 211, 153)
        cv2.ellipse(frame, (cx, cy), (rx, ry), 0, 0, 360, color, 3)
        cv2.ellipse(
            frame,
            (cx, min(height - 1, y2 + int(ry * 0.42))),
            (int(rx * 1.35), int(ry * 0.42)),
            0,
            200,
            340,
            color,
            3,
        )
        cv2.circle(frame, (cx, cy), 3, color, -1)
