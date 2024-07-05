import errno

import pytest
from pytest_dependency import depends

from middlewared.test.integration.utils import call, client

NOAUTH_METHOD = 'system.boot_id'
SEP = '_##_'


@pytest.mark.dependency(name='rate_limited')
def test_unauth_requests_are_rate_limited():
    """Test that the truenas server rate limits a caller
    that is hammering an endpoint that requires no authentication."""
    with client(auth=None) as c:
        for i in range(1, 22):
            if i <= 20:
                # default is 20 calls within 60 second timeframe
                assert c.call(NOAUTH_METHOD)
            else:
                with pytest.raises(Exception) as ve:
                    # on 21st call within 60 seconds, rate limit kicks in
                    c.call(NOAUTH_METHOD)
                assert ve.value.errno == errno.EBUSY


def test_rate_limit_global_cache_entries(request):
    """Test that middleware's rate limit plugin for interacting
    with the global cache behaves as intended."""
    depends(request, ['rate_limited'])
    cache = call('rate.limit.cache_get')
    # the mechanism by which the rate limit chooses a unique key
    # for inserting into the dictionary is by using the api endpoint
    # name as part of the string
    assert any((NOAUTH_METHOD in i for i in cache)), cache

    # now let's pop the last entry of the cache
    len_cache_before_pop = len(cache)
    popped_method, popped_ip = list(cache)[-1].split(SEP)
    call('rate.limit.cache_pop', popped_method, popped_ip)
    new_cache = call('rate.limit.cache_get')
    assert len(new_cache) != len_cache_before_pop, new_cache

    # finally, let's clear the cache
    call('rate.limit.cache_clear')
    new_new_cache = call('rate.limit.cache_get')
    assert len(new_new_cache) == 0, new_new_cache


def test_auth_requests_are_not_rate_limited():
    """Test that the truenas server does NOT rate limit a caller
    that hammers an endpoint when said caller has been authenticated
    and that method requires authentication."""
    for i in range(1, 22):
        assert call('system.host_id')
