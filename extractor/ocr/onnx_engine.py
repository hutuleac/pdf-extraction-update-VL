"""PP-OCRv6 detection + recognition on onnxruntime, CPU-only.

Loads the two ``.onnx`` files directly, so nothing downloads anything at run
time and the pipeline keeps its offline guarantee.
"""
from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from extractor.ocr.base import OcrLine, OcrResult, OcrUnavailable, UnavailableReason
from extractor.ocr.ctc import clean_icon_noise, decode_greedy
from extractor.ocr.dbnet_post import boxes_from_bitmap, sort_reading_order
from extractor.ocr.paths import load_labels

logger = logging.getLogger(__name__)

# Detection input is padded to a multiple of this; the network downsamples by 32.
SIZE_DIVISOR = 32
DET_MAX_SIDE = 960
DET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
DET_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

REC_HEIGHT = 48
# Every crop is scaled to REC_HEIGHT and its width follows the aspect ratio.
# This cap only exists to bound memory on a pathological box; it must stay well
# above a real line of text. At 640 a long line of small text was compressed up
# to 3x, merging glyphs and dropping the spaces between words -- whole sentences
# decoded as a few stray letters. Batches group crops of similar width, so a
# generous cap costs nothing on the common case.
REC_MAX_WIDTH = 2048
REC_BATCH = 8

# Below this, a recognized line is noise rather than text.
MIN_LINE_CONFIDENCE = 0.30


def _detection_input(image: np.ndarray) -> tuple[np.ndarray, tuple[int, int]]:
    """Scale an RGB image for detection and return (NCHW tensor, resized size)."""
    height, width = image.shape[:2]
    scale = min(DET_MAX_SIDE / max(height, width), 1.0)
    target_h = max(SIZE_DIVISOR, round(height * scale / SIZE_DIVISOR) * SIZE_DIVISOR)
    target_w = max(SIZE_DIVISOR, round(width * scale / SIZE_DIVISOR) * SIZE_DIVISOR)

    resized = cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    normalized = (resized.astype(np.float32) / 255.0 - DET_MEAN) / DET_STD
    tensor = normalized.transpose(2, 0, 1)[np.newaxis, ...]
    return np.ascontiguousarray(tensor), (target_h, target_w)


def _crop_box(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Perspective-warp one quadrilateral out of the image into an upright crop."""
    width = max(1, round(max(
        np.linalg.norm(box[0] - box[1]), np.linalg.norm(box[2] - box[3]),
    )))
    height = max(1, round(max(
        np.linalg.norm(box[0] - box[3]), np.linalg.norm(box[1] - box[2]),
    )))

    destination = np.array(
        [[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(box.astype(np.float32), destination)
    crop = cv2.warpPerspective(
        image, transform, (width, height), borderMode=cv2.BORDER_REPLICATE,
    )
    # A crop far taller than it is wide is a rotated line; stand it upright.
    if height >= width * 1.5:
        crop = cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE)
    return crop


def _target_width(crop: np.ndarray) -> int:
    """Width this crop needs at REC_HEIGHT to keep its aspect ratio."""
    height, width = crop.shape[:2]
    return max(1, min(REC_MAX_WIDTH, round(width * REC_HEIGHT / max(height, 1))))


def _width_ordered_batches(crops: list[np.ndarray], size: int = REC_BATCH) -> list[list[int]]:
    """Group crop indices into batches of similar width.

    A batch is padded to its widest member, so putting a full-width line beside
    a short label would make the whole batch pay for the long one. Callers must
    restore reading order afterwards.
    """
    order = sorted(range(len(crops)), key=lambda index: _target_width(crops[index]))
    return [order[start:start + size] for start in range(0, len(order), size)]


def _recognition_batch(crops: list[np.ndarray]) -> np.ndarray:
    """Resize crops to a common (3, 48, W) tensor batch, padded to the widest."""
    resized = [
        cv2.resize(crop, (_target_width(crop), REC_HEIGHT), interpolation=cv2.INTER_LINEAR)
        for crop in crops
    ]

    batch_width = max(item.shape[1] for item in resized)
    batch = np.zeros((len(resized), 3, REC_HEIGHT, batch_width), dtype=np.float32)
    for index, item in enumerate(resized):
        normalized = (item.astype(np.float32) / 255.0 - 0.5) / 0.5
        batch[index, :, :, : item.shape[1]] = normalized.transpose(2, 0, 1)
    return batch


class OnnxOcrEngine:
    """Detection + recognition against local PP-OCRv6 ONNX weights."""

    name = "onnx"

    def __init__(self, det_path: Path, rec_path: Path, dict_path: Path) -> None:
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        try:
            self._det = ort.InferenceSession(
                str(det_path), options, providers=["CPUExecutionProvider"],
            )
            self._rec = ort.InferenceSession(
                str(rec_path), options, providers=["CPUExecutionProvider"],
            )
        # Any load failure is surfaced to the user as a typed reason, not a crash.
        except Exception as exc:
            raise OcrUnavailable(UnavailableReason.LOAD_FAILED, str(exc)) from exc

        self._labels = load_labels(dict_path)
        self._det_input = self._det.get_inputs()[0].name
        self._rec_input = self._rec.get_inputs()[0].name

        classes = self._rec.get_outputs()[0].shape[-1]
        if isinstance(classes, int) and classes != len(self._labels):
            raise OcrUnavailable(
                UnavailableReason.MODEL_INCOMPATIBLE,
                f"model emits {classes} classes, dictionary has {len(self._labels)}",
            )

    def recognize(self, image: np.ndarray) -> OcrResult:
        """Detect and read every text line in an RGB image array."""
        if image.ndim != 3 or image.shape[2] != 3:
            raise ValueError(f"expected an (H, W, 3) RGB array, got shape {image.shape}")

        boxes = self._detect(image)
        if not boxes:
            return OcrResult(lines=[], engine=self.name)
        return OcrResult(lines=self._read(image, boxes), engine=self.name)

    def _detect(self, image: np.ndarray) -> list[np.ndarray]:
        """Run detection and return text boxes in reading order."""
        tensor, _ = _detection_input(image)
        prob_map = self._det.run(None, {self._det_input: tensor})[0][0][0]
        boxes = boxes_from_bitmap(prob_map, image.shape[:2])
        return sort_reading_order(boxes)

    def _read(self, image: np.ndarray, boxes: list[np.ndarray]) -> list[OcrLine]:
        """Recognize the text inside each box, dropping empty and noisy results."""
        crops = [_crop_box(image, box) for box in boxes]
        found: list[tuple[int, OcrLine]] = []

        for batch in _width_ordered_batches(crops, REC_BATCH):
            chunk = [crops[index] for index in batch]
            logits = self._rec.run(None, {self._rec_input: _recognition_batch(chunk)})[0]
            for index, row in zip(batch, logits, strict=True):
                text, confidence = decode_greedy(row, self._labels)
                text = clean_icon_noise(text.strip())
                if not text or confidence < MIN_LINE_CONFIDENCE:
                    continue
                box = boxes[index]
                found.append((index, OcrLine(
                    text=text,
                    confidence=confidence,
                    bbox=(
                        float(box[:, 0].min()), float(box[:, 1].min()),
                        float(box[:, 0].max()), float(box[:, 1].max()),
                    ),
                )))

        # Batching reordered the crops by width; the caller expects reading order.
        return [line for _, line in sorted(found, key=lambda pair: pair[0])]
