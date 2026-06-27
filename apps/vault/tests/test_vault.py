"""
Tests for the Vault module — master password, unlock, items, recovery, isolation.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.vault.models import VaultItem, VaultKey
from apps.vault.tests.conftest_helpers import (
    ENC_KEY,
    FAST_HASHERS,
    LOCMEM_CACHE,
    create_tenant,
    create_user,
)

BASE = '/api/v1/app/vault/'
MASTER = 'SuperSecret123'


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE, ENCRYPTION_KEY=ENC_KEY)
class TestVault(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('vault-corp', plan='professional')
        self.alice = create_user(self.tenant, 'alice@vault.com', 'Alice')
        self.bob = create_user(self.tenant, 'bob@vault.com', 'Bob')
        self.headers = {'HTTP_X_TENANT_SLUG': 'vault-corp'}

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _setup_master(self, user=None):
        self._auth(user or self.alice)
        res = self.client.post(
            f'{BASE}master-password/', {'master_password': MASTER}, format='json', **self.headers
        )
        return res

    def _unlock(self, master=MASTER):
        res = self.client.post(
            f'{BASE}unlock/', {'master_password': master}, format='json', **self.headers
        )
        return res

    def _token_headers(self, token):
        return {**self.headers, 'HTTP_X_VAULT_TOKEN': token}

    # ── Master password setup ────────────────────────────────────────────────

    def test_setup_returns_recovery_code(self):
        res = self._setup_master()
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIn('recovery_code', res.json())
        self.assertTrue(VaultKey.objects.filter(user=self.alice).exists())

    def test_setup_twice_conflicts(self):
        self._setup_master()
        res = self.client.post(
            f'{BASE}master-password/', {'master_password': MASTER}, format='json', **self.headers
        )
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)

    def test_status_reflects_configuration(self):
        self._auth(self.alice)
        res = self.client.get(f'{BASE}master-password/', **self.headers)
        self.assertFalse(res.json()['is_configured'])
        self._setup_master()
        res = self.client.get(f'{BASE}master-password/', **self.headers)
        self.assertTrue(res.json()['is_configured'])
        self.assertFalse(res.json()['is_unlocked'])

    # ── Unlock ───────────────────────────────────────────────────────────────

    def test_unlock_correct_returns_token(self):
        self._setup_master()
        res = self._unlock()
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('unlock_token', res.json())

    def test_unlock_wrong_password_401(self):
        self._setup_master()
        res = self._unlock(master='wrongpass')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unlock_lockout_after_5_fails(self):
        self._setup_master()
        for _ in range(5):
            self._unlock(master='nope')
        res = self._unlock(master=MASTER)  # correct, but locked out
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    # ── Items ────────────────────────────────────────────────────────────────

    def _create_item(self, token, title='GitHub', item_type='login', data=None):
        return self.client.post(
            f'{BASE}items/',
            {'title': title, 'item_type': item_type, 'data': data or {'username': 'a', 'password': 's3cr3t'}},
            format='json', **self._token_headers(token),
        )

    def test_create_requires_unlock(self):
        self._setup_master()
        res = self.client.post(
            f'{BASE}items/',
            {'title': 'X', 'item_type': 'login', 'data': {'password': 'p'}},
            format='json', **self.headers,  # no token
        )
        self.assertEqual(res.status_code, status.HTTP_423_LOCKED)

    def test_create_and_reveal_roundtrip(self):
        self._setup_master()
        token = self._unlock().json()['unlock_token']
        created = self._create_item(token, data={'username': 'me', 'password': 'p@ss'})
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        item_id = created.json()['id']
        # Reveal returns the decrypted data.
        res = self.client.get(f'{BASE}items/{item_id}/', **self._token_headers(token))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['data'], {'username': 'me', 'password': 'p@ss'})

    def test_reveal_without_token_locked(self):
        self._setup_master()
        token = self._unlock().json()['unlock_token']
        item_id = self._create_item(token).json()['id']
        res = self.client.get(f'{BASE}items/{item_id}/', **self.headers)  # no token
        self.assertEqual(res.status_code, status.HTTP_423_LOCKED)

    def test_list_visible_while_locked_without_secret(self):
        self._setup_master()
        token = self._unlock().json()['unlock_token']
        self._create_item(token, title='My Login')
        # List without a token: titles visible, no secret payload.
        res = self.client.get(f'{BASE}items/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['count'], 1)
        item = res.json()['items'][0]
        self.assertEqual(item['title'], 'My Login')
        self.assertNotIn('data', item)
        self.assertNotIn('data_ciphertext', item)

    # ── Change master password ───────────────────────────────────────────────

    def test_change_master_keeps_items_decryptable(self):
        self._setup_master()
        token = self._unlock().json()['unlock_token']
        item_id = self._create_item(token, data={'k': 'v'}).json()['id']
        res = self.client.put(
            f'{BASE}master-password/',
            {'current_master_password': MASTER, 'new_master_password': 'BrandNew456'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Unlock with the new password and reveal the old item.
        token2 = self._unlock(master='BrandNew456').json()['unlock_token']
        reveal = self.client.get(f'{BASE}items/{item_id}/', **self._token_headers(token2))
        self.assertEqual(reveal.json()['data'], {'k': 'v'})

    def test_change_master_wrong_current_400(self):
        self._setup_master()
        res = self.client.put(
            f'{BASE}master-password/',
            {'current_master_password': 'bad', 'new_master_password': 'BrandNew456'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Recovery ─────────────────────────────────────────────────────────────

    def test_recover_with_code_keeps_data(self):
        recovery_code = self._setup_master().json()['recovery_code']
        token = self._unlock().json()['unlock_token']
        item_id = self._create_item(token, data={'secret': '42'}).json()['id']
        res = self.client.post(
            f'{BASE}recover/',
            {'recovery_code': recovery_code, 'new_master_password': 'Recovered789'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('recovery_code', res.json())  # rotated
        token2 = self._unlock(master='Recovered789').json()['unlock_token']
        reveal = self.client.get(f'{BASE}items/{item_id}/', **self._token_headers(token2))
        self.assertEqual(reveal.json()['data'], {'secret': '42'})

    def test_recover_invalid_code_400(self):
        self._setup_master()
        res = self.client.post(
            f'{BASE}recover/',
            {'recovery_code': 'not-the-code', 'new_master_password': 'Recovered789'},
            format='json', **self.headers,
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Isolation & plan limit ───────────────────────────────────────────────

    def test_other_user_cannot_see_items(self):
        self._setup_master()
        token = self._unlock().json()['unlock_token']
        self._create_item(token)
        self._auth(self.bob)
        res = self.client.get(f'{BASE}items/', **self.headers)
        self.assertEqual(res.json()['count'], 0)

    def test_other_user_token_does_not_decrypt(self):
        # Alice sets up + creates an item.
        self._setup_master(self.alice)
        alice_token = self._unlock().json()['unlock_token']
        item_id = self._create_item(alice_token).json()['id']
        # Bob sets up his own vault and unlocks it.
        self._setup_master(self.bob)
        bob_token = self._unlock().json()['unlock_token']
        # Bob's token cannot reach Alice's item (per-user scoping → 404).
        res = self.client.get(f'{BASE}items/{item_id}/', **self._token_headers(bob_token))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_plan_limit_402(self):
        free_tenant = create_tenant('free-vault', plan='free')  # max_vault_items = 10
        u = create_user(free_tenant, 'u@free.com', 'Free User')
        self._auth(u)
        self.client.post(
            f'{BASE}master-password/', {'master_password': MASTER}, format='json',
            HTTP_X_TENANT_SLUG='free-vault',
        )
        token = self.client.post(
            f'{BASE}unlock/', {'master_password': MASTER}, format='json',
            HTTP_X_TENANT_SLUG='free-vault',
        ).json()['unlock_token']
        th = {'HTTP_X_TENANT_SLUG': 'free-vault', 'HTTP_X_VAULT_TOKEN': token}
        for i in range(10):
            r = self.client.post(
                f'{BASE}items/',
                {'title': f'i{i}', 'item_type': 'login', 'data': {'p': 'x'}},
                format='json', **th,
            )
            self.assertEqual(r.status_code, status.HTTP_201_CREATED)
        over = self.client.post(
            f'{BASE}items/',
            {'title': 'over', 'item_type': 'login', 'data': {'p': 'x'}},
            format='json', **th,
        )
        self.assertEqual(over.status_code, status.HTTP_402_PAYMENT_REQUIRED)
