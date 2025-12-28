"""Geometry utilities for bounding box operations."""
from typing import Tuple


def bbox_center(bbox: Tuple[int, int, int, int]) -> Tuple[int, int]:
    """Get center point of bounding box.

    Args:
        bbox: (x1, y1, x2, y2) bounding box coordinates

    Returns:
        (cx, cy) center point
    """
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, (y1 + y2) // 2)


def bbox_area(bbox: Tuple[int, int, int, int]) -> int:
    """Calculate area of bounding box.

    Args:
        bbox: (x1, y1, x2, y2) bounding box coordinates

    Returns:
        Area in pixels
    """
    x1, y1, x2, y2 = bbox
    return (x2 - x1) * (y2 - y1)


def iou(bbox_a: Tuple[int, int, int, int], bbox_b: Tuple[int, int, int, int]) -> float:
    """Calculate Intersection over Union between two bounding boxes.

    Args:
        bbox_a: First bounding box (x1, y1, x2, y2)
        bbox_b: Second bounding box (x1, y1, x2, y2)

    Returns:
        IoU value between 0 and 1
    """
    x1 = max(bbox_a[0], bbox_b[0])
    y1 = max(bbox_a[1], bbox_b[1])
    x2 = min(bbox_a[2], bbox_b[2])
    y2 = min(bbox_a[3], bbox_b[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (bbox_a[2] - bbox_a[0]) * (bbox_a[3] - bbox_a[1])
    area_b = (bbox_b[2] - bbox_b[0]) * (bbox_b[3] - bbox_b[1])
    union = area_a + area_b - inter

    return inter / union if union > 0 else 0


def distance(p1: Tuple[int, int], p2: Tuple[int, int]) -> float:
    """Calculate Euclidean distance between two points.

    Args:
        p1: First point (x, y)
        p2: Second point (x, y)

    Returns:
        Distance in pixels
    """
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


def point_in_bbox(point: Tuple[int, int], bbox: Tuple[int, int, int, int]) -> bool:
    """Check if a point is inside a bounding box.

    Args:
        point: (x, y) point coordinates
        bbox: (x1, y1, x2, y2) bounding box

    Returns:
        True if point is inside bbox
    """
    x, y = point
    x1, y1, x2, y2 = bbox
    return x1 <= x <= x2 and y1 <= y <= y2


def bbox_from_center(center: Tuple[int, int], width: int, height: int) -> Tuple[int, int, int, int]:
    """Create bounding box from center point and dimensions.

    Args:
        center: (cx, cy) center point
        width: Box width
        height: Box height

    Returns:
        (x1, y1, x2, y2) bounding box
    """
    cx, cy = center
    x1 = cx - width // 2
    y1 = cy - height // 2
    x2 = cx + width // 2
    y2 = cy + height // 2
    return (x1, y1, x2, y2)
