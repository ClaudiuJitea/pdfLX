"""Read published GitHub releases without blocking the GTK main loop."""
import json
import re
from dataclasses import dataclass
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .constants import APP_VERSION

REPOSITORY = 'ClaudiuJitea/pdfLX'
RELEASES_URL = f'https://github.com/{REPOSITORY}/releases'
LATEST_API = f'https://api.github.com/repos/{REPOSITORY}/releases/latest'


class NoReleasesError(ValueError):
    pass


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+(?:\.\d+)*)(?:\+[\w.-]+)?', value.strip())
    if not match:
        raise ValueError('Unsupported release version')
    numbers = [int(part) for part in match[1].split('.')]
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers.pop()
    return tuple(numbers)


def github_release_url(url, kind):
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (parsed.scheme == 'https' and parsed.netloc == 'github.com'
            and parsed.path.lower().startswith(f'/{REPOSITORY}/releases/{kind}/'.lower()))


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    assets: tuple

    def newer_than(self, installed=APP_VERSION):
        return version_tuple(self.version) > version_tuple(installed)


def parse_release(data):
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        raise ValueError('Invalid stable release')
    version = data.get('tag_name')
    if not isinstance(version, str):
        raise ValueError('Missing release version')
    version_tuple(version)
    url = data.get('html_url')
    if not github_release_url(url, 'tag'):
        raise ValueError('Invalid release URL')
    assets = []
    for asset in data.get('assets', []):
        if (isinstance(asset, dict) and isinstance(asset.get('name'), str)
                and asset['name'] and github_release_url(asset.get('browser_download_url'), 'download')):
            assets.append((asset['name'], asset['browser_download_url']))
    return Release(version.removeprefix('v'), url, tuple(assets))


def fetch_latest_release():
    request = Request(LATEST_API, headers={'Accept': 'application/vnd.github+json',
                                         'User-Agent': f'pdfLX/{APP_VERSION}'})
    try:
        with urlopen(request, timeout=10) as response:
            payload = response.read(2_000_001)
    except HTTPError as error:
        if error.code == 404:
            raise NoReleasesError('No published release') from error
        raise
    if len(payload) > 2_000_000:
        raise ValueError('Release response is too large')
    return parse_release(json.loads(payload))
