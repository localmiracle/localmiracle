#!/usr/bin/env python3
"""Render public contribution charts from GitHub GraphQL; no hosted image API."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET

OUT = Path('assets/widgets')
PALETTES = {
    'dark': ('#0d1117', '#e6edf3', '#8b949e', '#6cb6c2', '#21262d'),
    'light': ('#ffffff', '#24292f', '#656d76', '#2b7f8b', '#e6ebef'),
}
QUERY = '''query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      contributionCalendar { weeks { contributionDays { date contributionCount } } }
    }
  }
}'''


def get_days(login: str, token: str, now: datetime) -> list[tuple[date, int]]:
    start = now.date() - timedelta(days=30)
    variables = {'login': login, 'from': f'{start.isoformat()}T00:00:00Z',
                 'to': now.isoformat().replace('+00:00', 'Z')}
    body = json.dumps({'query': QUERY, 'variables': variables}).encode()
    request = Request('https://api.github.com/graphql', data=body, headers={
        'Authorization': f'Bearer {token}', 'Content-Type': 'application/json',
        'User-Agent': 'localmiracle-profile-widgets',
    })
    for attempt in range(3):
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.load(response)
            if payload.get('errors') or not payload.get('data', {}).get('user'):
                raise ValueError('GitHub did not return a valid contribution calendar')
            weeks = payload['data']['user']['contributionsCollection']['contributionCalendar']['weeks']
            counts = {}
            for week in weeks:
                for item in week['contributionDays']:
                    day = date.fromisoformat(item['date'])
                    count = item['contributionCount']
                    if type(count) is not int or count < 0 or day in counts:
                        raise ValueError('Invalid or duplicate contribution day')
                    counts[day] = count
            days = [(start + timedelta(days=i), counts[start + timedelta(days=i)]) for i in range(31)]
            return days
        except Exception:
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))
    raise RuntimeError('Calendar request failed')


def render(days: list[tuple[date, int]], theme: str, width: int) -> str:
    if len(days) != 31 or any(type(n) is not int or n < 0 for _, n in days):
        raise ValueError('Expected 31 non-negative daily counts')
    if any(days[i][0] - days[i-1][0] != timedelta(days=1) for i in range(1, 31)):
        raise ValueError('Expected consecutive dates')
    bg, title, text, accent, rule = PALETTES[theme]
    left, right, top, bottom = 42, width-26, 84, 184
    max_count = max(1, max(count for _, count in days))
    unit = 10 ** math.floor(math.log10(max_count))
    ceiling = max(1, math.ceil(max_count / unit) * unit)
    points = [(left + i*(right-left)/30, bottom-count/ceiling*(bottom-top)) for i, (_, count) in enumerate(days)]
    line = 'M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x, y in points)
    area = line + f' L {right},{bottom} L {left},{bottom} Z'
    total = sum(count for _, count in days)
    parts = [f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="250" viewBox="0 0 {width} 250" role="img" aria-labelledby="title description">
<title id="title">GitHub contribution activity</title>
<desc id="description">{total} contributions from {days[0][0]} to {days[-1][0]}. The current day is incomplete. Daily values come directly from GitHub.</desc>
<rect width="{width}" height="250" rx="12" fill="{bg}"/>
<g font-family="Segoe UI, Arial, sans-serif">
<text x="26" y="32" fill="{title}" font-size="16" font-weight="600">Contribution activity</text>
<text x="26" y="53" fill="{text}" font-size="12">Last 31 days</text>
<text x="{width-26}" y="33" fill="{accent}" font-size="28" font-weight="600" text-anchor="end">{total:,}</text>
<text x="{width-26}" y="53" fill="{text}" font-size="12" text-anchor="end">contributions</text>
''']
    for value, y in ((ceiling, top), (0, bottom)):
        parts.append(f'<path d="M{left},{y} H{right}" stroke="{rule}" stroke-width="1"/>')
        parts.append(f'<text x="{left-10}" y="{y+4}" fill="{text}" font-size="10" text-anchor="end">{value}</text>')
    parts.append(f'<path d="{area}" fill="{accent}" fill-opacity="0.10"/>')
    parts.append(f'<path d="{line}" fill="none" stroke="{accent}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
    for (day, count), (x, y) in zip(days, points):
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="2.3" fill="{accent}"><title>{day}: {count} contributions</title></circle>')
    for i in (0, 7, 15, 23, 30):
        anchor = 'start' if i == 0 else 'end' if i == 30 else 'middle'
        parts.append(f'<text x="{points[i][0]:.2f}" y="208" fill="{text}" font-size="11" text-anchor="{anchor}">{days[i][0]:%d %b}</text>')
    parts.append(f'<text x="{width-26}" y="237" fill="{text}" font-size="10" text-anchor="end">Updated {days[-1][0]:%d %b %Y} · UTC</text>')
    return ''.join(parts) + '</g></svg>\n'


def validate(path: Path) -> None:
    data = path.read_bytes()
    if not 300 < len(data) < 2_000_000:
        raise ValueError(f'Unexpected size: {path}')
    svg = ET.fromstring(data)
    if svg.tag != '{http://www.w3.org/2000/svg}svg':
        raise ValueError(f'Not an SVG: {path}')
    text = ' '.join(svg.itertext()).lower()
    if any(e in text for e in ('something went wrong', 'rate limit exceeded', 'could not fetch',
                              'user not found', 'invalid username', 'unable to fetch')):
        raise ValueError(f'Error card: {path}')
    for node in svg.iter():
        if node.tag.rsplit('}', 1)[-1] in ('script', 'foreignObject'):
            raise ValueError(f'Unexpected active content: {path}')
        if any(key.lower().startswith('on') for key in node.attrib):
            raise ValueError(f'Unexpected event handler: {path}')


def main() -> None:
    token = os.environ.get('GITHUB_TOKEN', '')
    login = os.environ.get('GITHUB_REPOSITORY_OWNER', 'localmiracle')
    if not token or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
        raise ValueError('A valid repository owner and GITHUB_TOKEN are required')
    now = datetime.now(timezone.utc)
    days = get_days(login, token, now)
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in PALETTES:
        for width, suffix in ((840, ''), (400, '-mobile')):
            (OUT / f'activity-{theme}{suffix}.svg').write_text(render(days, theme, width), encoding='utf-8')
    record = {'source': 'GitHub GraphQL contributionCalendar', 'username': login,
              'updated_at': now.isoformat(), 'total': sum(n for _, n in days),
              'days': [{'date': str(d), 'contributions': n} for d, n in days]}
    (OUT / 'activity.json').write_text(json.dumps(record, indent=2)+'\n')
    expected = [OUT/f'{name}-{theme}.svg' for name in ('overview', 'streak', 'activity') for theme in PALETTES]
    expected += [OUT/f'activity-{theme}-mobile.svg' for theme in PALETTES]
    for path in expected:
        validate(path)
        print(f'Validated {path}')
    print(f'{len(days)} real dates; {sum(n for _, n in days)} contributions; all 8 SVGs valid.')


if __name__ == '__main__':
    main()
