#!/usr/bin/env python3.6

# Author: Eric Turgeon
# License: BSD
# Location for tests into REST API of FreeNAS

import sys
import os

apifolder = os.getcwd()
sys.path.append(apifolder)
from functions import DELETE


# Check destroying a ZFS snapshot
def test_01_Destroying_ZFS_snapshot_IXBUILD_ROOT_ZVOL_test():
    assert DELETE("/storage/snapshot/tank@test/") == 204


# Check destroying a ZVOL 1/2
def test_01_Destroying_ZVOL_01_02():
    assert DELETE("/storage/volume/tank/zvols/testzvol1/") == 204


# Check destroying a ZVOL 2/2
def test_01_Destroying_ZVOL_02_02():
    assert DELETE("/storage/volume/tank/zvols/testzvol2/") == 204
