"""Pure presentation helpers shared by the UI and tests."""
from io import BytesIO
from pathlib import Path

from PIL import Image


STATE_BANNERS = {
    'waiting': ('info', 'Waiting for assembly and inspection'),
    'inspecting': ('info', 'Inspecting…'),
    'blocked': ('error', 'Blocked — correction required'),
    'needs_review': ('warning', 'Needs operator review'),
    'hold': ('error', 'On hold — resolve the inspection failure'),
    'step_complete': ('success', 'Step verified — ready for the next step'),
    'complete': ('success', 'Assembly complete'),
}


def camera_image(path):
    """Return verified image bytes or an honest placeholder explanation."""
    if path is None:
        return None, 'No camera image available yet.'
    path = Path(path)
    if path.suffix.lower() == '.json':
        return None, 'Mock JSON scene snapshot; no camera feed is available.'
    try:
        data = path.read_bytes()
        with Image.open(BytesIO(data)) as image:
            image.verify()
        return data, None
    except (OSError, ValueError, SyntaxError) as exc:
        return None, f'Camera image unavailable: {exc}'


def filter_audit(events, selected=()):
    """Validate data before UI rendering; never hide a malformed entry."""
    if not isinstance(events, list) or any(
        not isinstance(event, dict) or not isinstance(event.get('event_type'), str)
        for event in events
    ):
        raise ValueError('Malformed audit data: expected events with event_type strings')
    return [event for event in events if not selected or event['event_type'] in selected]
