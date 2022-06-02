import pytest

from middlewared.test.integration.utils import call, client, mock, ssh

from auto_config import dev_test
pytestmark = pytest.mark.skipif(dev_test, reason='Skipping for test development testing')


def test_private_params_do_not_leak_to_logs():
    with mock("test.test1", """    
        from middlewared.service import accepts
        from middlewared.schema import Dict, Str

        @accepts(Dict("test", Str("password", private=True)))
        async def mock(self, args):
            raise Exception()
    """):
        log_before = ssh("cat /var/log/middlewared.log")

        with client(py_exceptions=False) as c:
            with pytest.raises(Exception):
                c.call("test.test1", {"password": "secret"})

        log = ssh("cat /var/log/middlewared.log")[len(log_before):]
        assert "Exception while calling test.test1(*[{'password': '********'}])" in log


def test_private_params_do_not_leak_to_core_get_jobs():
    with mock("test.test1", """    
        from middlewared.service import accepts, job
        from middlewared.schema import Dict, Str

        @accepts(Dict("test", Str("password", private=True)))
        @job()
        async def mock(self, job, args):
            return 42
    """):
        job_id = call("test.test1", {"password": "secret"})

        job_descr = call("core.get_jobs", [["id", "=", job_id]], {"get": True})
        assert job_descr["arguments"] == [{"password": "********"}]
