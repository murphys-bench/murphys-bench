#!/usr/bin/env python3
"""Build MB's icon sprite: the subset of Tabler Icons the app actually uses.

The full Tabler sprite is ~2 MB (5,000+ icons). MB references a few dozen, so
the shipped file is a subset. This script downloads the PINNED upstream sprite,
verifies its SHA-256 against static/vendor/VERSIONS, extracts exactly the
symbols named in core/templatetags/mb_tabler.py, and writes the result.

Run from anywhere after adding an icon name:

    python3 scripts/build_icon_sprite.py

It fails loud if the download does not match the recorded hash, or if any
named icon is missing from the upstream sprite. No Node, no npm.
"""
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from core.templatetags.mb_tabler import ICONS, SPRITE_VERSION  # noqa: E402

URL = (f'https://cdn.jsdelivr.net/npm/@tabler/icons-sprite@{SPRITE_VERSION}'
       '/dist/tabler-sprite.svg')
OUT = REPO / 'static' / 'vendor' / 'tabler-icons' / SPRITE_VERSION / 'mb-sprite.svg'
VERSIONS = REPO / 'static' / 'vendor' / 'VERSIONS'


def recorded_sha():
    for line in VERSIONS.read_text().splitlines():
        if line.startswith('@tabler/icons-sprite ') and f' {SPRITE_VERSION} ' in line:
            m = re.search(r'sha256=([0-9a-f]{64})', line)
            if m:
                return m.group(1)
    sys.exit(f'build_icon_sprite: no sha256 recorded for @tabler/icons-sprite '
             f'{SPRITE_VERSION} in {VERSIONS}')


def main():
    want = recorded_sha()
    print(f'fetching {URL}')
    data = urllib.request.urlopen(URL, timeout=60).read()
    got = hashlib.sha256(data).hexdigest()
    if got != want:
        sys.exit(f'build_icon_sprite: SHA-256 mismatch\n  recorded {want}\n  fetched  {got}')
    text = data.decode('utf-8')
    by_name = {name: whole for whole, name in re.findall(
        r'(<symbol id="tabler-([a-z0-9-]+)".*?</symbol>)', text, re.S)}
    missing = [n for n in ICONS if n not in by_name]
    if missing:
        sys.exit(f'build_icon_sprite: not in upstream sprite: {", ".join(missing)}')
    body = '\n'.join(by_name[n] for n in sorted(set(ICONS)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" style="display:none">\n'
        f'<!-- Subset of @tabler/icons-sprite {SPRITE_VERSION} (MIT). '
        'Built by scripts/build_icon_sprite.py; edit the list in '
        'core/templatetags/mb_tabler.py, not this file. -->\n'
        f'{body}\n</svg>\n'
    )
    print(f'wrote {OUT.relative_to(REPO)}: {len(set(ICONS))} icons, {OUT.stat().st_size} bytes')


if __name__ == '__main__':
    main()
