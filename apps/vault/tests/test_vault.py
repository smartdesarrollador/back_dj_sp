"""
Tests for the Vault module — master password, unlock, items, recovery, isolation.
"""
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.vault import crypto
from apps.vault.models import VaultItem, VaultKey, VaultShare
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

    # ── List items (pagination) ──────────────────────────────────────────────

    def _create_items(self, n, **overrides):
        items = []
        for i in range(n):
            defaults = {
                'tenant': self.tenant, 'user': self.alice,
                'title': f'Item {i}', 'data_ciphertext': 'x',
            }
            defaults.update(overrides)
            items.append(VaultItem.objects.create(**defaults))
        return items

    def test_list_items_first_page_default_per_page(self):
        self._auth(self.alice)
        self._create_items(25)
        res = self.client.get(f'{BASE}items/', {'page': 1}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = res.json()
        self.assertEqual(len(body['items']), 20)
        self.assertEqual(body['pagination'], {'page': 1, 'per_page': 20, 'total': 25})

    def test_list_items_second_page(self):
        self._auth(self.alice)
        self._create_items(25)
        res = self.client.get(f'{BASE}items/', {'page': 2}, **self.headers)
        body = res.json()
        self.assertEqual(len(body['items']), 5)
        self.assertEqual(body['pagination']['page'], 2)

    def test_list_items_custom_per_page(self):
        self._auth(self.alice)
        self._create_items(10)
        res = self.client.get(f'{BASE}items/', {'page': 1, 'per_page': 5}, **self.headers)
        body = res.json()
        self.assertEqual(len(body['items']), 5)
        self.assertEqual(body['pagination']['per_page'], 5)

    def test_list_items_per_page_clamped_to_100(self):
        self._auth(self.alice)
        self._create_items(3)
        res = self.client.get(f'{BASE}items/', {'page': 1, 'per_page': 500}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['pagination']['per_page'], 100)

    def test_list_items_page_out_of_range_returns_empty(self):
        self._auth(self.alice)
        self._create_items(3)
        res = self.client.get(f'{BASE}items/', {'page': 999}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        body = res.json()
        self.assertEqual(body['items'], [])
        self.assertEqual(body['pagination']['total'], 3)

    def test_list_items_invalid_page_falls_back_to_default(self):
        self._auth(self.alice)
        self._create_items(3)
        res = self.client.get(f'{BASE}items/', {'page': 'abc'}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['pagination']['page'], 1)

    def test_list_items_invalid_per_page_falls_back_to_default(self):
        self._auth(self.alice)
        self._create_items(3)
        res = self.client.get(f'{BASE}items/', {'page': 1, 'per_page': 'xyz'}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['pagination']['per_page'], 20)

    def test_list_items_negative_page_clamped_to_one(self):
        self._auth(self.alice)
        self._create_items(3)
        res = self.client.get(f'{BASE}items/', {'page': -5}, **self.headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.json()['pagination']['page'], 1)

    def test_list_items_filters_combined_with_pagination(self):
        self._auth(self.alice)
        self._create_items(3, item_type='login')
        self._create_items(4, item_type='api_key')
        res = self.client.get(
            f'{BASE}items/', {'item_type': 'login', 'page': 1, 'per_page': 2}, **self.headers
        )
        body = res.json()
        self.assertEqual(len(body['items']), 2)
        self.assertEqual(body['pagination']['total'], 3)
        self.assertTrue(all(i['item_type'] == 'login' for i in body['items']))

    def test_list_items_cross_user_pagination_isolated(self):
        self._auth(self.alice)
        self._create_items(2)
        self._create_items(5, user=self.bob)
        res = self.client.get(f'{BASE}items/', {'page': 1}, **self.headers)
        self.assertEqual(res.json()['pagination']['total'], 2)

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


class TestVaultCryptoRoundtrip(APITestCase):
    """Pure crypto tests — no HTTP, no DB. A bug here is far more serious than
    anywhere else in this feature: it could silently corrupt or expose secrets."""

    def test_seal_and_unseal_roundtrip(self):
        private_key, public_key = crypto.generate_keypair()
        sealed = crypto.seal_for_recipient('{"password": "s3cr3t"}', public_key)
        opened = crypto.unseal_with_private_key(sealed, private_key)
        self.assertEqual(opened, '{"password": "s3cr3t"}')

    def test_wrong_private_key_cannot_unseal(self):
        _, public_key = crypto.generate_keypair()
        other_private_key, _ = crypto.generate_keypair()
        sealed = crypto.seal_for_recipient('top secret', public_key)
        with self.assertRaises(crypto.VaultCryptoError):
            crypto.unseal_with_private_key(sealed, other_private_key)

    def test_sealing_is_not_deterministic(self):
        """Each seal uses a fresh ephemeral keypair — same plaintext, different output."""
        _, public_key = crypto.generate_keypair()
        sealed_a = crypto.seal_for_recipient('same plaintext', public_key)
        sealed_b = crypto.seal_for_recipient('same plaintext', public_key)
        self.assertNotEqual(sealed_a, sealed_b)


@override_settings(PASSWORD_HASHERS=FAST_HASHERS, CACHES=LOCMEM_CACHE, ENCRYPTION_KEY=ENC_KEY)
class TestVaultSharing(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = create_tenant('vault-share-corp', plan='professional')
        self.alice = create_user(self.tenant, 'alice@vshare.com', 'Alice')
        self.bob = create_user(self.tenant, 'bob@vshare.com', 'Bob')
        self.headers = {'HTTP_X_TENANT_SLUG': 'vault-share-corp'}

    def _auth(self, user):
        self.client.force_authenticate(user=user)

    def _setup_and_unlock(self, user, master=MASTER):
        self._auth(user)
        self.client.post(
            f'{BASE}master-password/', {'master_password': master}, format='json', **self.headers
        )
        token = self.client.post(
            f'{BASE}unlock/', {'master_password': master}, format='json', **self.headers
        ).json()['unlock_token']
        return token

    def _token_headers(self, token):
        return {**self.headers, 'HTTP_X_VAULT_TOKEN': token}

    def _create_item(self, token, title='Shared Login', data=None):
        res = self.client.post(
            f'{BASE}items/',
            {'title': title, 'item_type': 'login', 'data': data or {'username': 'a', 'password': 's3cr3t'}},
            format='json', **self._token_headers(token),
        )
        return res.json()['id']

    def test_share_then_recipient_reveals_with_own_unlock(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token, data={'password': 'hunter2'})
        # Bob must have his own vault configured to receive a share.
        bob_token = self._setup_and_unlock(self.bob)

        self._auth(self.alice)
        share_res = self.client.post(
            f'{BASE}items/{item_id}/share/',
            {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        )
        self.assertEqual(share_res.status_code, status.HTTP_201_CREATED)
        share_id = share_res.json()['id']

        self._auth(self.bob)
        listed = self.client.get(f'{BASE}shared-with-me/', **self.headers)
        self.assertEqual(listed.json()['items'][0]['title'], 'Shared Login')

        revealed = self.client.get(
            f'{BASE}shared-with-me/{share_id}/', **self._token_headers(bob_token)
        )
        self.assertEqual(revealed.status_code, status.HTTP_200_OK)
        self.assertEqual(revealed.json()['data'], {'password': 'hunter2'})
        self.assertEqual(revealed.json()['shared_by_name'], 'Alice')

    def test_recipient_locked_cannot_reveal(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token)
        self._setup_and_unlock(self.bob)

        self._auth(self.alice)
        share_id = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        ).json()['id']

        self._auth(self.bob)
        # Bob is authenticated but has NOT unlocked in this request context.
        res = self.client.get(f'{BASE}shared-with-me/{share_id}/', **self.headers)
        self.assertEqual(res.status_code, status.HTTP_423_LOCKED)

    def test_share_requires_recipient_to_have_configured_vault(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token)
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.json()['error']['code'], 'recipient_no_vault_key')

    def test_share_requires_sharing_feature_gate(self):
        free_tenant = create_tenant('vault-share-free', plan='free')
        owner = create_user(free_tenant, 'owner@vsfree.com', 'Owner')
        recipient = create_user(free_tenant, 'recipient@vsfree.com', 'Recipient')
        free_headers = {'HTTP_X_TENANT_SLUG': 'vault-share-free'}

        self._auth(owner)
        self.client.post(f'{BASE}master-password/', {'master_password': MASTER}, format='json', **free_headers)
        owner_token = self.client.post(
            f'{BASE}unlock/', {'master_password': MASTER}, format='json', **free_headers
        ).json()['unlock_token']
        item_id = self.client.post(
            f'{BASE}items/', {'title': 'X', 'item_type': 'login', 'data': {'p': '1'}},
            format='json', **{**free_headers, 'HTTP_X_VAULT_TOKEN': owner_token},
        ).json()['id']

        self._auth(recipient)
        self.client.post(f'{BASE}master-password/', {'master_password': MASTER}, format='json', **free_headers)

        self._auth(owner)
        res = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'recipient@vsfree.com'},
            format='json', **{**free_headers, 'HTTP_X_VAULT_TOKEN': owner_token},
        )
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    def test_self_share_rejected(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token)
        self._auth(self.alice)
        res = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'alice@vshare.com'},
            format='json', **self._token_headers(alice_token),
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.json()['error']['code'], 'self_share')

    def test_revoke_share_removes_recipient_access(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token)
        bob_token = self._setup_and_unlock(self.bob)

        self._auth(self.alice)
        share_id = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        ).json()['id']
        revoke = self.client.delete(
            f'{BASE}items/{item_id}/share/{share_id}/', **self._token_headers(alice_token)
        )
        self.assertEqual(revoke.status_code, status.HTTP_204_NO_CONTENT)

        self._auth(self.bob)
        listed = self.client.get(f'{BASE}shared-with-me/', **self.headers)
        self.assertEqual(listed.json()['items'], [])
        reveal = self.client.get(
            f'{BASE}shared-with-me/{share_id}/', **self._token_headers(bob_token)
        )
        self.assertEqual(reveal.status_code, status.HTTP_404_NOT_FOUND)

    def test_updating_item_reseals_existing_shares(self):
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token, data={'password': 'old-pass'})
        bob_token = self._setup_and_unlock(self.bob)

        self._auth(self.alice)
        share_id = self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        ).json()['id']

        # Owner edits the secret after sharing it.
        self.client.patch(
            f'{BASE}items/{item_id}/', {'data': {'password': 'new-pass'}},
            format='json', **self._token_headers(alice_token),
        )

        self._auth(self.bob)
        revealed = self.client.get(
            f'{BASE}shared-with-me/{share_id}/', **self._token_headers(bob_token)
        )
        self.assertEqual(revealed.json()['data'], {'password': 'new-pass'})

    def test_other_tenant_user_cannot_be_shared_with_via_wrong_scoping(self):
        """Sanity check: VaultShare rows are tenant-scoped like every other model here."""
        alice_token = self._setup_and_unlock(self.alice)
        item_id = self._create_item(alice_token)
        bob_token = self._setup_and_unlock(self.bob)
        self._auth(self.alice)
        self.client.post(
            f'{BASE}items/{item_id}/share/', {'shared_with_email': 'bob@vshare.com'},
            format='json', **self._token_headers(alice_token),
        )
        self.assertEqual(
            VaultShare.objects.filter(tenant=self.tenant, shared_with=self.bob).count(), 1
        )
