#!/usr/bin/env python3
import errno
import pytest
import secrets
import string

from middlewared.service_exception import CallError, ValidationErrors
from middlewared.test.integration.assets.account import user
from middlewared.test.integration.assets.account import unprivileged_user
from middlewared.test.integration.utils import call, client


TEST_USERNAME = 'testpasswduser'
TEST_USERNAME_2 = 'testpasswduser2'
TEST_GROUPNAME = 'testpasswdgroup'
TEST_PASSWORD = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(10))
TEST_PASSWORD_2 = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(10))
TEST_PASSWORD2 = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(10))
TEST_PASSWORD2_2 = ''.join(secrets.choice(string.ascii_letters + string.digits) for i in range(10))


def test_restricted_user_password_reset():
    with unprivileged_user(
        username=TEST_USERNAME,
        group_name=TEST_GROUPNAME,
        privilege_name='TEST_PASSWD_RESET_PRIVILEGE',
        allowlist=[],
        web_shell=False,
        roles=['READONLY']
    ) as acct:
        with client(auth=(acct.username, acct.password)) as c:
            # Password reset using existing password and current user should work
            c.call('user.password_reset', {
                'username': acct.username,
                'old_password': acct.password,
                'new_password': TEST_PASSWORD
            })

            # Should be able to create new client session with new password
            with client(auth=(acct.username, TEST_PASSWORD)) as c2:
                c2.call('auth.me')

        # FULL_ADMIN privileges should also allow password reset:
        call('user.password_reset', {
            'username': acct.username,
            'old_password': TEST_PASSWORD,
            'new_password': TEST_PASSWORD_2
        })

        # FULL_ADMIN should also be able to skip password checks
        call('user.password_reset', {
            'username': acct.username,
            'new_password': TEST_PASSWORD_2,
            'options': {'skip_password_check': True},
        })

        group_id = call('group.query', [['group', '=', TEST_GROUPNAME]], {'get': True})['id']

        # Create additional user with READONLY privilege
        with user({
           'username': TEST_USERNAME_2,
           'full_name': TEST_USERNAME_2,
           'group_create': True,
           'groups': [group_id],
           'smb': False,
           'password': TEST_PASSWORD2
        }) as u:
            with client(auth=(TEST_USERNAME_2, TEST_PASSWORD2)) as c2:
                # Limited users should not be able to change other
                # passwords of other users
                with pytest.raises(CallError) as ve:
                    c2.call('user.password_reset', {
                        'username': acct.username,
                        'old_password': TEST_PASSWORD_2,
                        'new_password': 'CANARY'
                    })

                assert ve.value.errno == errno.EPERM

                with pytest.raises(ValidationErrors) as ve:
                    # Limited users should not be able to skip password checks
                    c2.call('user.password_reset', {
                        'username': TEST_USERNAME_2,
                        'new_password': 'CANARY',
                        'options': {'skip_password_check': True}
                    })

            call("user.update", u['id'], {'password_disabled': True})
            with pytest.raises(ValidationErrors) as ve:
                # This should fail because we've disabled password auth
                call('user.password_reset', {
                    'username': TEST_USERNAME_2,
                    'old_password': TEST_PASSWORD2,
                    'new_password': 'CANARY'
                })

            call("user.update", u['id'], {
                'password_disabled': False,
                'locked': True
            })

            with pytest.raises(ValidationErrors) as ve:
                # This should fail because we've locked account
                call('user.password_reset', {
                    'username': TEST_USERNAME_2,
                    'old_password': TEST_PASSWORD2,
                    'new_password': 'CANARY'
                })

            call("user.update", u['id'], {
                'password_disabled': False,
                'locked': False
            })

            # Unlocking user should allow password reset to succeed
            with client(auth=(TEST_USERNAME_2, TEST_PASSWORD2)) as c2:
                c2.call('user.password_reset', {
                    'username': TEST_USERNAME_2,
                    'old_password': TEST_PASSWORD2,
                    'new_password': TEST_PASSWORD2_2
                })
