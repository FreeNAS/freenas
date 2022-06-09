import subprocess
import textwrap
from unittest.mock import call, Mock, patch

import pytest

from middlewared.etc_files.smartd import (
    ensure_smart_enabled, get_smartd_schedule, get_smartd_config
)
from middlewared.pytest.unit.middleware import Middleware


@pytest.mark.asyncio
async def test__ensure_smart_enabled__smart_error():
    with patch("middlewared.etc_files.smartd.smartctl") as run:
        run.return_value = Mock(stdout="S.M.A.R.T. Error")

        assert await ensure_smart_enabled(["/dev/ada0"]) is False

        run.assert_called_once()


@pytest.mark.asyncio
async def test__ensure_smart_enabled__smart_enabled():
    with patch("middlewared.etc_files.smartd.smartctl") as run:
        run.return_value = Mock(stdout="SMART   Enabled")

        assert await ensure_smart_enabled(["/dev/ada0"])

        run.assert_called_once()


@pytest.mark.asyncio
async def test__ensure_smart_enabled__smart_was_disabled():
    with patch("middlewared.etc_files.smartd.smartctl") as run:
        run.return_value = Mock(stdout="SMART   Disabled", returncode=0)

        assert await ensure_smart_enabled(["/dev/ada0"])

        assert run.call_args_list == [
            call(["/dev/ada0", "-i"], check=False, stderr=subprocess.STDOUT,
                 encoding="utf8", errors="ignore"),
            call(["/dev/ada0", "-s", "on"], check=False, stderr=subprocess.STDOUT),
        ]


@pytest.mark.asyncio
async def test__ensure_smart_enabled__enabling_smart_failed():
    with patch("middlewared.etc_files.smartd.smartctl") as run:
        run.return_value = Mock(stdout="SMART   Disabled", returncode=1)

        assert await ensure_smart_enabled(["/dev/ada0"]) is False


@pytest.mark.asyncio
async def test__ensure_smart_enabled__handled_args_properly():
    with patch("middlewared.etc_files.smartd.smartctl") as run:
        run.return_value = Mock(stdout="SMART   Enabled")

        assert await ensure_smart_enabled(["/dev/ada0", "-d", "sat"])

        run.assert_called_once_with(
            ["/dev/ada0", "-d", "sat", "-i"], check=False, stderr=subprocess.STDOUT,
            encoding="utf8", errors="ignore",
        )


@pytest.mark.asyncio
async def test__annotate_disk_for_smart__skips_nvd():
    m = Middleware()
    m['system.is_enterprise_ix_hardware'] = Mock(return_value=False)
    assert await annotate_disk_for_smart(m, {}, "nvd0") is None


@pytest.mark.asyncio
async def test__annotate_disk_for_smart__skips_unknown_device():
    m = Middleware()
    m['system.is_enterprise_ix_hardware'] = Mock(return_value=False)
    assert await annotate_disk_for_smart(m, {"ada0": {}}, "ada1") is None


@pytest.mark.asyncio
async def test__annotate_disk_for_smart__skips_device_without_args():
    m = Middleware()
    m['system.is_enterprise_ix_hardware'] = Mock(return_value=False)
    with patch("middlewared.etc_files.smartd.get_smartctl_args") as get_smartctl_args:
        get_smartctl_args.return_value = None
        assert await annotate_disk_for_smart(m, {"ada1": {"driver": "ata"}}, "ada1") is None


@pytest.mark.asyncio
async def test__annotate_disk_for_smart__skips_device_with_unavailable_smart():
    m = Middleware()
    m['system.is_enterprise_ix_hardware'] = Mock(return_value=False)
    with patch("middlewared.etc_files.smartd.get_smartctl_args") as get_smartctl_args:
        get_smartctl_args.return_value = ["/dev/ada1", "-d", "sat"]
        with patch("middlewared.etc_files.smartd.ensure_smart_enabled") as ensure_smart_enabled:
            ensure_smart_enabled.return_value = False
            assert await annotate_disk_for_smart(m, {"ada1": {"driver": "ata"}}, "ada1") is None


@pytest.mark.asyncio
async def test__annotate_disk_for_smart():
    m = Middleware()
    m['system.is_enterprise_ix_hardware'] = Mock(return_value=False)
    with patch("middlewared.etc_files.smartd.get_smartctl_args") as get_smartctl_args:
        get_smartctl_args.return_value = ["/dev/ada1", "-d", "sat"]
        with patch("middlewared.etc_files.smartd.ensure_smart_enabled") as ensure_smart_enabled:
            ensure_smart_enabled.return_value = True
            assert await annotate_disk_for_smart(m, {"ada1": {"driver": "ata"}}, "ada1") == (
                "ada1",
                {"smartctl_args": ["/dev/ada1", "-d", "sat", "-a", "-d", "removable"]},
            )


def test__get_smartd_schedule__need_mapping():
    assert get_smartd_schedule({
        "smarttest_schedule": {
            "month": "jan,feb,mar,apr,may,jun,jul,aug,sep,oct,nov,dec",
            "dom": "1,hedgehog day,3",
            "dow": "tue,SUN",
            "hour": "*/1",
        }
    }) == "../(01|03)/(2|7)/.."


def test__get_smartd_config():
    assert get_smartd_config({
        "smartctl_args": ["/dev/ada0", "-d", "sat"],
        "smart_powermode": "never",
        "smart_difference": 0,
        "smart_informational": 1,
        "smart_critical": 2,
        "smarttest_type": "S",
        "smarttest_month": "*/1",
        "smarttest_daymonth": "*/1",
        "smarttest_dayweek": "*/1",
        "smarttest_hour": "*/1",
        "disk_smartoptions": "--options",
        "disk_critical": None,
        "disk_difference": None,
        "disk_informational": None,
    }) == textwrap.dedent("""\
        /dev/ada0 -d sat -n never -W 0,1,2 -m root -M exec /usr/local/libexec/smart_alert.py\\
        -s S/../.././..\\
         --options""")


def test__get_smartd_config_without_schedule():
    assert get_smartd_config({
        "smartctl_args": ["/dev/ada0", "-d", "sat"],
        "smart_powermode": "never",
        "smart_difference": 0,
        "smart_informational": 1,
        "smart_critical": 2,
        "disk_smartoptions": "--options",
        "disk_critical": None,
        "disk_difference": None,
        "disk_informational": None,
    }) == textwrap.dedent("""\
        /dev/ada0 -d sat -n never -W 0,1,2 -m root -M exec /usr/local/libexec/smart_alert.py --options""")


def test__get_smartd_config_with_temp():
    assert get_smartd_config({
        "smartctl_args": ["/dev/ada0", "-d", "sat"],
        "smart_powermode": "never",
        "smart_difference": 0,
        "smart_informational": 1,
        "smart_critical": 2,
        "disk_smartoptions": "--options",
        "disk_critical": 50,
        "disk_difference": 10,
        "disk_informational": 40,
    }) == textwrap.dedent("""\
        /dev/ada0 -d sat -n never -W 10,40,50 -m root -M exec /usr/local/libexec/smart_alert.py --options""")
