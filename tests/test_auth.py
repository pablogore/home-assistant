"""Tests for the Home Assistant auth module."""
from datetime import timedelta
from unittest.mock import Mock, patch

import pytest

from homeassistant import auth, data_entry_flow
from homeassistant.util import dt as dt_util

from tests.common import MockUser, ensure_auth_manager_loaded


@pytest.fixture
def mock_hass(loop):
    """Hass mock with minimum amount of data set to make it work with auth."""
    hass = Mock()
    hass.config.skip_pip = True
    return hass


async def test_auth_manager_from_config_validates_config_and_id(mock_hass):
    """Test get auth providers."""
    manager = await auth.auth_manager_from_config(mock_hass, [{
        'name': 'Test Name',
        'type': 'insecure_example',
        'users': [],
    }, {
        'name': 'Invalid config because no users',
        'type': 'insecure_example',
        'id': 'invalid_config',
    }, {
        'name': 'Test Name 2',
        'type': 'insecure_example',
        'id': 'another',
        'users': [],
    }, {
        'name': 'Wrong because duplicate ID',
        'type': 'insecure_example',
        'id': 'another',
        'users': [],
    }])

    providers = [{
            'name': provider.name,
            'id': provider.id,
            'type': provider.type,
        } for provider in manager.async_auth_providers]
    assert providers == [{
        'name': 'Test Name',
        'type': 'insecure_example',
        'id': None,
    }, {
        'name': 'Test Name 2',
        'type': 'insecure_example',
        'id': 'another',
    }]


async def test_create_new_user(mock_hass):
    """Test creating new user."""
    manager = await auth.auth_manager_from_config(mock_hass, [{
        'type': 'insecure_example',
        'users': [{
            'username': 'test-user',
            'password': 'test-pass',
            'name': 'Test Name'
        }]
    }])

    step = await manager.login_flow.async_init(('insecure_example', None))
    assert step['type'] == data_entry_flow.RESULT_TYPE_FORM

    step = await manager.login_flow.async_configure(step['flow_id'], {
        'username': 'test-user',
        'password': 'test-pass',
    })
    assert step['type'] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    credentials = step['result']
    user = await manager.async_get_or_create_user(credentials)
    assert user is not None
    assert user.is_owner is True
    assert user.name == 'Test Name'


async def test_login_as_existing_user(mock_hass):
    """Test login as existing user."""
    manager = await auth.auth_manager_from_config(mock_hass, [{
        'type': 'insecure_example',
        'users': [{
            'username': 'test-user',
            'password': 'test-pass',
            'name': 'Test Name'
        }]
    }])
    ensure_auth_manager_loaded(manager)

    # Add fake user with credentials for example auth provider.
    user = MockUser(
        id='mock-user',
        is_owner=False,
        is_active=False,
        name='Paulus',
    ).add_to_auth_manager(manager)
    user.credentials.append(auth.Credentials(
        id='mock-id',
        auth_provider_type='insecure_example',
        auth_provider_id=None,
        data={'username': 'test-user'},
        is_new=False,
    ))

    step = await manager.login_flow.async_init(('insecure_example', None))
    assert step['type'] == data_entry_flow.RESULT_TYPE_FORM

    step = await manager.login_flow.async_configure(step['flow_id'], {
        'username': 'test-user',
        'password': 'test-pass',
    })
    assert step['type'] == data_entry_flow.RESULT_TYPE_CREATE_ENTRY
    credentials = step['result']

    user = await manager.async_get_or_create_user(credentials)
    assert user is not None
    assert user.id == 'mock-user'
    assert user.is_owner is False
    assert user.is_active is False
    assert user.name == 'Paulus'


async def test_linking_user_to_two_auth_providers(mock_hass):
    """Test linking user to two auth providers."""
    manager = await auth.auth_manager_from_config(mock_hass, [{
        'type': 'insecure_example',
        'users': [{
            'username': 'test-user',
            'password': 'test-pass',
        }]
    }, {
        'type': 'insecure_example',
        'id': 'another-provider',
        'users': [{
            'username': 'another-user',
            'password': 'another-password',
        }]
    }])

    step = await manager.login_flow.async_init(('insecure_example', None))
    step = await manager.login_flow.async_configure(step['flow_id'], {
        'username': 'test-user',
        'password': 'test-pass',
    })
    user = await manager.async_get_or_create_user(step['result'])
    assert user is not None

    step = await manager.login_flow.async_init(('insecure_example',
                                                'another-provider'))
    step = await manager.login_flow.async_configure(step['flow_id'], {
        'username': 'another-user',
        'password': 'another-password',
    })
    await manager.async_link_user(user, step['result'])
    assert len(user.credentials) == 2


def test_access_token_expired():
    """Test that the expired property on access tokens work."""
    refresh_token = auth.RefreshToken(
        user=None,
        client_id='bla'
    )

    access_token = auth.AccessToken(
        refresh_token=refresh_token
    )

    assert access_token.expired is False

    with patch('homeassistant.auth.dt_util.utcnow',
               return_value=dt_util.utcnow() + auth.ACCESS_TOKEN_EXPIRATION):
        assert access_token.expired is True

    almost_exp = dt_util.utcnow() + auth.ACCESS_TOKEN_EXPIRATION - timedelta(1)
    with patch('homeassistant.auth.dt_util.utcnow', return_value=almost_exp):
        assert access_token.expired is False


async def test_cannot_retrieve_expired_access_token(mock_hass):
    """Test that we cannot retrieve expired access tokens."""
    manager = await auth.auth_manager_from_config(mock_hass, [])
    user = MockUser(
        id='mock-user',
        is_owner=False,
        is_active=False,
        name='Paulus',
    ).add_to_auth_manager(manager)
    refresh_token = await manager.async_create_refresh_token(user, 'bla')
    access_token = manager.async_create_access_token(refresh_token)

    assert manager.async_get_access_token(access_token.token) is access_token

    with patch('homeassistant.auth.dt_util.utcnow',
               return_value=dt_util.utcnow() + auth.ACCESS_TOKEN_EXPIRATION):
        assert manager.async_get_access_token(access_token.token) is None

    # Even with unpatched time, it should have been removed from manager
    assert manager.async_get_access_token(access_token.token) is None
