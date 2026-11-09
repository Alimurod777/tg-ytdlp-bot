"""Resolution bounds shared by preset downloads and their format fallback."""
import re


def group_quality_key(quality_key, chat_id):
    """Group video downloads are capped at 720p; private choices are preserved."""
    if chat_id >= 0:
        return quality_key
    match = re.fullmatch(r'(\d+)p', str(quality_key or ''))
    if match and int(match[1]) <= 720:
        return quality_key
    return '720p'


def selected_quality_format(quality_key, original_format='', *, any_codec=False):
    """Keep a numeric preset in its resolution tier, for either orientation.

    Prefer the chosen codec within the requested tier, then try other codecs
    before accepting lower resolution. Never remove the upper bound on a retry.
    Explicit format IDs and best/audio selections are left alone.
    """
    match = re.fullmatch(r'(\d+)p', str(quality_key or ''))
    if not match:
        return original_format
    quality = int(match[1])
    if quality <= 0:
        return original_format
    previous = max((q for q in (144, 240, 360, 480, 540, 576, 720, 1080,
                                1440, 2160, 4320) if q < quality), default=0)
    codec = re.search(r'\[vcodec\*=([^\]]+)\]', original_format)
    language = re.search(r'\[language\^=([^\]]+)\]', original_format)
    audio = 'ba' + (language.group(0) if language else '')
    bounds = [f'[{side}<={quality}][height>{previous}][width>{previous}]'
              for side in ('height', 'width')]
    preferences = []
    if not any_codec and '[dynamic_range=hdr]' in original_format:
        preferences.append('[dynamic_range=hdr]')
    if not any_codec and codec:
        preferences.append(codec.group(0))
    preferences.append('')
    selectors = [f'bv*{pref}{bound}+{audio}'
                 for pref in preferences for bound in bounds]
    # Sources with muxed audio need no separate audio stream.
    selectors.extend(f'b{bound}' for bound in bounds)
    # Only fall back to lower resolution if the requested tier is unavailable
    # in every codec. The upper bound applies to these alternatives too.
    for pref in preferences:
        for side in ('height', 'width'):
            selectors.append(f'bv*{pref}[{side}<={quality}]+{audio}')
    selectors.extend(f'b[{side}<={quality}]' for side in ('height', 'width'))
    return '/'.join(selectors)
