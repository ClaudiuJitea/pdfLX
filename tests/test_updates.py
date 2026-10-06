"""Version comparisons and GitHub release handling."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from pdflx.updates import (LATEST_API, NoReleasesError, fetch_latest_release,
                          parse_release, version_tuple)


def release(tag='v0.5'):
    return {'tag_name': tag, 'html_url': f'https://github.com/ClaudiuJitea/pdfLX/releases/tag/{tag}',
            'assets': [{'name': 'pdflx-x86_64.AppImage', 'browser_download_url':
                        f'https://github.com/ClaudiuJitea/pdfLX/releases/download/{tag}/pdflx-x86_64.AppImage'}]}


class Updates(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertTrue(parse_release(release('v0.10')).newer_than('0.9'))
        self.assertFalse(parse_release(release('v0.4.0')).newer_than('0.4'))
        self.assertFalse(parse_release(release('v0.4')).newer_than('0.5'))
        self.assertEqual(version_tuple('v1.2.0+build.1'), (1, 2))

    def test_rejects_prereleases_and_bad_tags(self):
        for data in (release('nightly'), release('v0.5-rc1'), dict(release(), prerelease=True),
                     dict(release(), draft=True), None):
            with self.assertRaises(ValueError):
                parse_release(data)

    def test_assets_only_use_official_github_downloads(self):
        data = release()
        for url in ('https://evil.example/file.deb', 'file:///tmp/file.deb',
                    'https://github.com/other/repo/releases/download/v0.5/file.deb'):
            data['assets'].append({'name': 'file.deb', 'browser_download_url': url})
        result = parse_release(data)
        self.assertEqual(len(result.assets), 1)
        self.assertEqual(result.assets[0][0], 'pdflx-x86_64.AppImage')

    def test_release_without_assets(self):
        self.assertEqual(parse_release(dict(release(), assets=[])).assets, ())

    @patch('pdflx.updates.urlopen')
    def test_fetch_uses_timeout_and_parses_response(self, open_url):
        open_url.return_value = io.BytesIO(json.dumps(release()).encode())
        self.assertEqual(fetch_latest_release().version, '0.5')
        args, kwargs = open_url.call_args
        self.assertEqual(args[0].full_url, LATEST_API)
        self.assertEqual(kwargs['timeout'], 10)

    @patch('pdflx.updates.urlopen')
    def test_no_release_and_network_error_are_distinct(self, open_url):
        open_url.side_effect = HTTPError(LATEST_API, 404, 'Not found', {}, None)
        with self.assertRaises(NoReleasesError):
            fetch_latest_release()
        open_url.side_effect = URLError('offline')
        with self.assertRaises(URLError):
            fetch_latest_release()

    @patch('pdflx.updates.urlopen')
    def test_invalid_response_is_rejected(self, open_url):
        open_url.return_value = io.BytesIO(b'not json')
        with self.assertRaises(ValueError):
            fetch_latest_release()


if __name__ == '__main__':
    unittest.main()
