"""
OpenAPI schema tests — verify that the drf-spectacular schema endpoint works
and includes the expected tags.
"""
from django.test import TestCase, override_settings

_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


@override_settings(CACHES=_LOCMEM_CACHE)
class TestOpenAPISchema(TestCase):
    def test_schema_endpoint_returns_200(self):
        response = self.client.get('/api/schema/?format=json')
        self.assertEqual(response.status_code, 200)

    def test_schema_contains_auth_tag(self):
        response = self.client.get('/api/schema/?format=json')
        data = response.json()
        all_tags = set()
        for path_data in data.get('paths', {}).values():
            for method_data in path_data.values():
                if isinstance(method_data, dict):
                    all_tags.update(method_data.get('tags', []))
        self.assertIn('auth', all_tags)

    def test_swagger_ui_loads(self):
        response = self.client.get('/api/docs/')
        self.assertEqual(response.status_code, 200)

    def _get_all_tags(self) -> set:
        response = self.client.get('/api/schema/?format=json')
        data = response.json()
        tags = set()
        for path_data in data.get('paths', {}).values():
            for method_data in path_data.values():
                if isinstance(method_data, dict):
                    tags.update(method_data.get('tags', []))
        return tags

    def test_schema_contains_hub_sso_tag(self):
        self.assertIn('hub-sso', self._get_all_tags())

    def test_schema_contains_hub_services_tag(self):
        self.assertIn('hub-services', self._get_all_tags())

    def test_schema_contains_hub_referrals_tag(self):
        self.assertIn('hub-referrals', self._get_all_tags())

    def test_schema_contains_hub_notifications_tag(self):
        self.assertIn('hub-notifications', self._get_all_tags())
