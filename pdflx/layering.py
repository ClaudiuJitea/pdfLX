"""Stacking order of canvas objects on a page (Bring to Front / Send to Back).

Objects without an explicit ``z`` keep the historical order: table cells, then
text and images, then shapes, then pen strokes. Original page content that was
never edited lives in the page snapshot underneath every canvas object, so it is
redrawn ("lifted") when a shape must go behind it. Edited original shapes without
an explicit ``z`` are drawn beneath the snapshot instead, where they came from.
"""
import pymupdf as fitz

from .models import EditableText, EditableShape, EditableImage, EditableStroke

DEFAULT_RANK = {EditableText: 10.0, EditableImage: 10.0, EditableShape: 20.0, EditableStroke: 30.0}


def layer_key(obj):
    z = getattr(obj, 'z', None)
    if z is not None:
        return float(z)
    if isinstance(obj, EditableShape) and getattr(obj, 'table_id', None):
        return 0.0
    return DEFAULT_RANK.get(type(obj), 10.0)


def is_underlay(obj):
    """Original page shapes (cell backgrounds, panels) and the images beneath them
    stay beneath the untouched page content."""
    original = (getattr(obj, 'from_page', False) if isinstance(obj, EditableShape)
                else isinstance(obj, EditableImage) and getattr(obj, 'underlay', False))
    return original and not getattr(obj, 'is_new', False) and getattr(obj, 'z', None) is None


def draw_order(objects):
    """Bottom-to-top order; ties keep their list order."""
    return sorted(objects, key=layer_key)


def page_objects(window):
    return [*window.editable_texts, *window.editable_images, *window.editable_shapes, *window.editable_strokes]


def lift(window, obj, page_number):
    """Move an untouched original object out of the snapshot so it is drawn as a canvas object."""
    from .undo_manager import _perform_ghost_erasure
    if getattr(obj, 'is_new', False) or getattr(obj, '_ghost_redacted', False):
        return
    _perform_ghost_erasure(window, obj, page_number)
    obj._ghost_redacted = True
    obj.is_baked = True


def set_layer(window, objects, front, page_number):
    """Place objects (in the given order, first lowest) above or below everything else."""
    objects = list(objects)
    others = [obj for obj in page_objects(window) if all(obj is not item for item in objects)]
    keys = [layer_key(obj) for obj in others]
    if front:
        start = (max(keys) if keys else 0.0) + 1
    else:
        start = (min(keys) if keys else 0.0) - len(objects)
    for offset, obj in enumerate(objects):
        obj.z = start + offset
        lift(window, obj, page_number)
    if not front:
        # Original content overlapping a shape sent behind it must be drawn above it.
        for obj in others:
            if not getattr(obj, 'is_new', False) and not getattr(obj, '_ghost_redacted', False):
                if any(fitz.Rect(obj.bbox).intersects(fitz.Rect(item.bbox)) for item in objects):
                    lift(window, obj, page_number)


def restack(window, obj, front):
    """Bring to Front / Send to Back as one undoable step."""
    from . import pdf_handler
    page_number = obj.page_number

    def mutation():
        set_layer(window, [obj], front, page_number)
        success, error = pdf_handler.rebuild_page(window.doc, page_number, window.editable_texts,
                                                  window.editable_shapes, window.editable_images,
                                                  all_strokes=window.editable_strokes)
        if not success:
            raise ValueError(error)
    return window._mutate_document(mutation, page_num=page_number)
