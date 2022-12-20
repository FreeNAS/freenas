#!/usr/bin/env python3
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

import libzfs
import netsnmpagent
import pysnmp.hlapi  # noqa
import pysnmp.smi

from middlewared.client import Client


def get_kstat():
    kstat = {}
    try:
        with open("/proc/spl/kstat/zfs/arcstats") as f:
            for lineno, line in enumerate(f, start=1):
                if lineno > 2 and (info := line.strip()):
                    name, _, data = info.split()
                    kstat[f"kstat.zfs.misc.arcstats.{name}"] = Decimal(int(data))
    except Exception:
        return kstat
    else:
        kstat["vfs.zfs.version.spa"] = Decimal(5000)

    return kstat


def get_arc_efficiency(kstat):
    if not kstat.get("vfs.zfs.version.spa"):
        return

    output = {}
    prefix = 'kstat.zfs.misc.arcstats'
    arc_hits = kstat[f"{prefix}.hits"]
    arc_misses = kstat[f"{prefix}.misses"]
    demand_data_hits = kstat[f"{prefix}.demand_data_hits"]
    demand_data_misses = kstat[f"{prefix}.demand_data_misses"]
    demand_metadata_hits = kstat[f"{prefix}.demand_metadata_hits"]
    demand_metadata_misses = kstat[f"{prefix}.demand_metadata_misses"]
    mfu_ghost_hits = kstat[f"{prefix}.mfu_ghost_hits"]
    mfu_hits = kstat[f"{prefix}.mfu_hits"]
    mru_ghost_hits = kstat[f"{prefix}.mru_ghost_hits"]
    mru_hits = kstat[f"{prefix}.mru_hits"]
    prefetch_data_hits = kstat[f"{prefix}.prefetch_data_hits"]
    prefetch_data_misses = kstat[f"{prefix}.prefetch_data_misses"]
    prefetch_metadata_hits = kstat[f"{prefix}.prefetch_metadata_hits"]
    prefetch_metadata_misses = kstat[f"{prefix}.prefetch_metadata_misses"]

    anon_hits = arc_hits - (mfu_hits + mru_hits + mfu_ghost_hits + mru_ghost_hits)
    arc_accesses_total = (arc_hits + arc_misses)
    demand_data_total = (demand_data_hits + demand_data_misses)
    prefetch_data_total = (prefetch_data_hits + prefetch_data_misses)
    real_hits = (mfu_hits + mru_hits)

    output["total_accesses"] = fHits(arc_accesses_total)
    output["cache_hit_ratio"] = {
        'per': fPerc(arc_hits, arc_accesses_total),
        'num': fHits(arc_hits),
    }
    output["cache_miss_ratio"] = {
        'per': fPerc(arc_misses, arc_accesses_total),
        'num': fHits(arc_misses),
    }
    output["actual_hit_ratio"] = {
        'per': fPerc(real_hits, arc_accesses_total),
        'num': fHits(real_hits),
    }
    output["data_demand_efficiency"] = {
        'per': fPerc(demand_data_hits, demand_data_total),
        'num': fHits(demand_data_total),
    }

    if prefetch_data_total > 0:
        output["data_prefetch_efficiency"] = {
            'per': fPerc(prefetch_data_hits, prefetch_data_total),
            'num': fHits(prefetch_data_total),
        }

    if anon_hits > 0:
        output["cache_hits_by_cache_list"] = {}
        output["cache_hits_by_cache_list"]["anonymously_used"] = {
            'per': fPerc(anon_hits, arc_hits),
            'num': fHits(anon_hits),
        }

    output["most_recently_used"] = {
        'per': fPerc(mru_hits, arc_hits),
        'num': fHits(mru_hits),
    }
    output["most_frequently_used"] = {
        'per': fPerc(mfu_hits, arc_hits),
        'num': fHits(mfu_hits),
    }
    output["most_recently_used_ghost"] = {
        'per': fPerc(mru_ghost_hits, arc_hits),
        'num': fHits(mru_ghost_hits),
    }
    output["most_frequently_used_ghost"] = {
        'per': fPerc(mfu_ghost_hits, arc_hits),
        'num': fHits(mfu_ghost_hits),
    }

    output["cache_hits_by_data_type"] = {}
    output["cache_hits_by_data_type"]["demand_data"] = {
        'per': fPerc(demand_data_hits, arc_hits),
        'num': fHits(demand_data_hits),
    }
    output["cache_hits_by_data_type"]["prefetch_data"] = {
        'per': fPerc(prefetch_data_hits, arc_hits),
        'num': fHits(prefetch_data_hits),
    }
    output["cache_hits_by_data_type"]["demand_metadata"] = {
        'per': fPerc(demand_metadata_hits, arc_hits),
        'num': fHits(demand_metadata_hits),
    }
    output["cache_hits_by_data_type"]["prefetch_metadata"] = {
        'per': fPerc(prefetch_metadata_hits, arc_hits),
        'num': fHits(prefetch_metadata_hits),
    }

    output["cache_misses_by_data_type"] = {}
    output["cache_misses_by_data_type"]["demand_data"] = {
        'per': fPerc(demand_data_misses, arc_misses),
        'num': fHits(demand_data_misses),
    }
    output["cache_misses_by_data_type"]["prefetch_data"] = {
        'per': fPerc(prefetch_data_misses, arc_misses),
        'num': fHits(prefetch_data_misses),
    }
    output["cache_misses_by_data_type"]["demand_metadata"] = {
        'per': fPerc(demand_metadata_misses, arc_misses),
        'num': fHits(demand_metadata_misses),
    }
    output["cache_misses_by_data_type"]["prefetch_metadata"] = {
        'per': fPerc(prefetch_metadata_misses, arc_misses),
        'num': fHits(prefetch_metadata_misses),
    }

    return output


def fHits(Hits=0, Decimal=2):
    khits = (10 ** 3)
    mhits = (10 ** 6)
    bhits = (10 ** 9)
    thits = (10 ** 12)
    qhits = (10 ** 15)
    Qhits = (10 ** 18)
    shits = (10 ** 21)
    Shits = (10 ** 24)

    if Hits >= Shits:
        return str("%0." + str(Decimal) + "f") % (Hits / Shits) + "S"
    elif Hits >= shits:
        return str("%0." + str(Decimal) + "f") % (Hits / shits) + "s"
    elif Hits >= Qhits:
        return str("%0." + str(Decimal) + "f") % (Hits / Qhits) + "Q"
    elif Hits >= qhits:
        return str("%0." + str(Decimal) + "f") % (Hits / qhits) + "q"
    elif Hits >= thits:
        return str("%0." + str(Decimal) + "f") % (Hits / thits) + "t"
    elif Hits >= bhits:
        return str("%0." + str(Decimal) + "f") % (Hits / bhits) + "b"
    elif Hits >= mhits:
        return str("%0." + str(Decimal) + "f") % (Hits / mhits) + "m"
    elif Hits >= khits:
        return str("%0." + str(Decimal) + "f") % (Hits / khits) + "k"
    elif Hits == 0:
        return str("%d" % 0)
    else:
        return str("%d" % Hits)


def fPerc(lVal=0, rVal=0, Decimal=2):
    if rVal > 0:
        return str("%0." + str(Decimal) + "f") % (100 * (lVal / rVal)) + "%"
    else:
        return str("%0." + str(Decimal) + "f") % 100 + "%"


def calculate_allocation_units(*args):
    allocation_units = 4096
    while True:
        values = tuple(map(lambda arg: int(arg / allocation_units), args))
        if all(v < 2 ** 31 for v in values):
            break

        allocation_units *= 2

    return allocation_units, values


def get_zfs_arc_miss_percent(kstat):
    arc_hits = kstat["kstat.zfs.misc.arcstats.hits"]
    arc_misses = kstat["kstat.zfs.misc.arcstats.misses"]
    arc_read = arc_hits + arc_misses
    if arc_read > 0:
        hit_percent = float(100 * arc_hits / arc_read)
        miss_percent = 100 - hit_percent
        return miss_percent
    return 0


mib_builder = pysnmp.smi.builder.MibBuilder()
mib_sources = mib_builder.getMibSources() + (pysnmp.smi.builder.DirMibSource("/usr/local/share/pysnmp/mibs"),)
mib_builder.setMibSources(*mib_sources)
mib_builder.loadModules("FREENAS-MIB")
mib_builder.loadModules("LM-SENSORS-MIB")

agent = netsnmpagent.netsnmpAgent(
    AgentName="FreeNASAgent",
    MIBFiles=[
        "/usr/local/share/snmp/mibs/FREENAS-MIB.txt",
        "/usr/local/share/snmp/mibs/LM-SENSORS-MIB.txt"
    ],
)

zpool_table = agent.Table(
    oidstr="FREENAS-MIB::zpoolTable",
    indexes=[agent.Integer32()],
    columns=[
        (1, agent.Integer32()),
        (2, agent.DisplayString()),
        (3, agent.DisplayString()),
        (4, agent.Counter64()),
        (5, agent.Counter64()),
        (6, agent.Counter64()),
        (7, agent.Counter64()),
        (8, agent.Counter64()),
        (9, agent.Counter64()),
        (10, agent.Counter64()),
        (11, agent.Counter64()),
        (12, agent.Counter64()),
        (13, agent.Counter64()),
        (14, agent.Counter64()),
    ],
)

dataset_table = agent.Table(
    oidstr="FREENAS-MIB::datasetTable",
    indexes=[
        agent.Integer32()
    ],
    columns=[
        (1, agent.Integer32()),
        (2, agent.DisplayString()),
        (3, agent.Integer32()),
        (4, agent.Integer32()),
        (5, agent.Integer32()),
        (6, agent.Integer32()),
    ],
)

zvol_table = agent.Table(
    oidstr="FREENAS-MIB::zvolTable",
    indexes=[agent.Integer32()],
    columns=[
        (1, agent.Integer32()),
        (2, agent.DisplayString()),
        (3, agent.Counter64()),
        (4, agent.Counter64()),
        (5, agent.Counter64()),
        (6, agent.Counter64()),
    ],
)

lm_sensors_table = None

hdd_temp_table = agent.Table(
    oidstr="FREENAS-MIB::hddTempTable",
    indexes=[
        agent.Integer32(),
    ],
    columns=[
        (2, agent.DisplayString()),
        (3, agent.Unsigned32()),
    ]
)

zfs_arc_size = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcSize")
zfs_arc_meta = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcMeta")
zfs_arc_data = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcData")
zfs_arc_hits = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcHits")
zfs_arc_misses = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcMisses")
zfs_arc_c = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcC")
zfs_arc_p = agent.Unsigned32(oidstr="FREENAS-MIB::zfsArcP")
zfs_arc_miss_percent = agent.DisplayString(oidstr="FREENAS-MIB::zfsArcMissPercent")
zfs_arc_cache_hit_ratio = agent.DisplayString(oidstr="FREENAS-MIB::zfsArcCacheHitRatio")
zfs_arc_cache_miss_ratio = agent.DisplayString(oidstr="FREENAS-MIB::zfsArcCacheMissRatio")

zfs_l2arc_hits = agent.Counter32(oidstr="FREENAS-MIB::zfsL2ArcHits")
zfs_l2arc_misses = agent.Counter32(oidstr="FREENAS-MIB::zfsL2ArcMisses")
zfs_l2arc_read = agent.Counter32(oidstr="FREENAS-MIB::zfsL2ArcRead")
zfs_l2arc_write = agent.Counter32(oidstr="FREENAS-MIB::zfsL2ArcWrite")
zfs_l2arc_size = agent.Unsigned32(oidstr="FREENAS-MIB::zfsL2ArcSize")

zfs_zilstat_ops1 = agent.Counter64(oidstr="FREENAS-MIB::zfsZilstatOps1sec")
zfs_zilstat_ops5 = agent.Counter64(oidstr="FREENAS-MIB::zfsZilstatOps5sec")
zfs_zilstat_ops10 = agent.Counter64(oidstr="FREENAS-MIB::zfsZilstatOps10sec")


def readZilOpsCount() -> int:
    total = 0
    with open("/proc/spl/kstat/zfs/zil") as f:
        for line in f:
            var, _size, val, *_ = line.split()
            if var in ("zil_itx_metaslab_normal_count", "zil_itx_metaslab_slog_count"):
                total += int(val)
    return total


class ZilstatThread(threading.Thread):
    def __init__(self, interval):
        super().__init__()

        self.daemon = True

        self.interval = interval
        self.value = 0

    def run(self):
        previous = readZilOpsCount()
        while True:
            time.sleep(self.interval)
            current = readZilOpsCount()
            self.value = current - previous
            previous = current


class CpuTempThread(threading.Thread):
    # TODO: Linux implementation
    pass


class DiskTempThread(threading.Thread):
    def __init__(self, interval):
        super().__init__()

        self.daemon = True

        self.interval = interval
        self.temperatures = {}

        self.initialized = False
        self.disks = []
        self.powermode = None

    def run(self):
        while True:
            if not self.initialized:
                try:
                    with Client() as c:
                        self.disks = c.call("disk.disks_for_temperature_monitoring")
                        self.powermode = c.call("smart.config")["powermode"]
                except Exception as e:
                    print(f"Failed to query disks for temperature monitoring: {e!r}")
                else:
                    self.initialized = True

            if not self.initialized:
                time.sleep(self.interval)
                continue

            if not self.disks:
                return

            try:
                with Client() as c:
                    self.temperatures = {
                        disk: temperature * 1000
                        for disk, temperature in c.call("disk.temperatures", self.disks, self.powermode).items()
                        if temperature is not None
                    }
            except Exception as e:
                print(f"Failed to collect disks temperatures: {e!r}")
                self.temperatures = {}

            time.sleep(self.interval)


def gather_zpool_iostat_info(prev_data, name, zfsobj):
    r_ops = zfsobj.root_vdev.stats.ops[libzfs.ZIOType.READ]
    w_ops = zfsobj.root_vdev.stats.ops[libzfs.ZIOType.WRITE]
    r_bytes = zfsobj.root_vdev.stats.bytes[libzfs.ZIOType.READ]
    w_bytes = zfsobj.root_vdev.stats.bytes[libzfs.ZIOType.WRITE]

    # the current values as reported by libzfs
    values_overall = {name: {
        "read_ops": r_ops,
        "write_ops": w_ops,
        "read_bytes": r_bytes,
        "write_bytes": w_bytes,
    }}

    values_1s = {name: {"read_ops": 0, "write_ops": 0, "read_bytes": 0, "write_bytes": 0}}
    for key in values_overall.get(name, ()):
        values_1s[name][key] = (values_overall[name][key] - prev_data[name][key])

    return values_overall, values_1s


def fill_in_zpool_snmp_row_info(idx, name, io_overall, io_1s, zpoolobj):
    row = zpool_table.addRow([agent.Integer32(idx)])
    row.setRowCell(1, agent.Integer32(idx))
    row.setRowCell(2, agent.DisplayString(name))  # zpool name
    row.setRowCell(3, agent.DisplayString(zpoolobj.properties["health"].value))  # pool health status
    row.setRowCell(4, agent.Counter64(int(zpoolobj.properties["size"].rawvalue)))
    row.setRowCell(5, agent.Counter64(int(zpoolobj.properties["allocated"].rawvalue)))
    row.setRowCell(6, agent.Counter64(int(zpoolobj.properties["free"].rawvalue)))
    row.setRowCell(7, agent.Counter64(io_overall[name]["read_ops"]))
    row.setRowCell(8, agent.Counter64(io_overall[name]["write_ops"]))
    row.setRowCell(9, agent.Counter64(io_overall[name]["read_bytes"]))
    row.setRowCell(10, agent.Counter64(io_overall[name]["write_bytes"]))
    row.setRowCell(11, agent.Counter64(io_1s[name]["read_ops"]))
    row.setRowCell(12, agent.Counter64(io_1s[name]["write_ops"]))
    row.setRowCell(13, agent.Counter64(io_1s[name]["read_bytes"]))
    row.setRowCell(14, agent.Counter64(io_1s[name]["write_bytes"]))


def fill_in_zvol_snmp_table_info(idx, zvolobj):
    row = zvol_table.addRow([agent.Integer32(idx)])
    row.setRowCell(1, agent.Integer32(idx))
    row.setRowCell(2, agent.DisplayString(zvolobj.properties["name"].value))
    row.setRowCell(3, int(zvolobj.properties["volsize"].rawvalue))
    row.setRowCell(4, int(zvolobj.properties["used"].rawvalue))
    row.setRowCell(5, int(zvolobj.properties["available"].rawvalue))
    row.setRowCell(6, int(zvolobj.properties["referenced"].rawvalue))


def report_zpool_and_zvol_info(prev_zpool_info, zfsobj):
    zpool_table.clear()
    zvol_table.clear()
    for idx, zpool in enumerate(zfsobj.pools, start=1):
        name = zpool.name

        # zpool related information
        io_overall, io_1s = gather_zpool_iostat_info(prev_zpool_info, name, zfsobj)
        fill_in_zpool_snmp_row_info(idx, name, io_overall, io_1s, zpool)

        # be sure and update our zpool io data so next time it's called
        # we calculate the 1sec values properly
        prev_zpool_info[name].update(io_overall[name])

        # zvol related information
        zvol_type = libzfs.DatasetType.VOLUME
        for zvol in filter(lambda x: x.type == zvol_type, zpool.root_dataset.children_recursive):
            # TODO: going through libzfs to get properties is expensive but there isn't a better
            # way (currently) to get the relevant zvol information that we're looking for
            fill_in_zvol_snmp_table_info(idx, zvol)


if __name__ == "__main__":
    with Client() as c:
        config = c.call("snmp.config")

    zfsobj = libzfs.ZFS()

    zilstat_1_thread = None
    zilstat_5_thread = None
    zilstat_10_thread = None
    if config["zilstat"]:
        zilstat_1_thread = ZilstatThread(1)
        zilstat_5_thread = ZilstatThread(5)
        zilstat_10_thread = ZilstatThread(10)

        zilstat_1_thread.start()
        zilstat_5_thread.start()
        zilstat_10_thread.start()

    # TODO: Linux implementation
    cpu_temp_thread = None

    disk_temp_thread = DiskTempThread(300)
    disk_temp_thread.start()

    agent.start()

    prev_zpool_info = {}
    last_update_at = datetime.min
    while True:
        agent.check_and_process()

        if datetime.utcnow() - last_update_at > timedelta(seconds=1):
            report_zpool_and_zvol_info(prev_zpool_info, zfsobj)

            datasets = []
            for zpool in zfsobj.pools:
                for dataset in zpool.root_dataset.children_recursive:
                    if dataset.type == libzfs.DatasetType.FILESYSTEM:
                        datasets.append(dataset)

            dataset_table.clear()
            for i, dataset in enumerate(datasets):
                row = dataset_table.addRow([agent.Integer32(i + 1)])
                row.setRowCell(1, agent.Integer32(i + 1))
                row.setRowCell(2, agent.DisplayString(dataset.properties["name"].value))
                allocation_units, (
                    size,
                    used,
                    available
                ) = calculate_allocation_units(
                    int(dataset.properties["used"].rawvalue) + int(dataset.properties["available"].rawvalue),
                    int(dataset.properties["used"].rawvalue),
                    int(dataset.properties["available"].rawvalue),
                )
                row.setRowCell(3, agent.Integer32(allocation_units))
                row.setRowCell(4, agent.Integer32(size))
                row.setRowCell(5, agent.Integer32(used))
                row.setRowCell(6, agent.Integer32(available))

            if lm_sensors_table:
                lm_sensors_table.clear()
                temperatures = []
                if cpu_temp_thread:
                    for i, temp in enumerate(cpu_temp_thread.temperatures.copy()):
                        temperatures.append((f"CPU{i}", temp))
                if disk_temp_thread:
                    temperatures.extend(list(disk_temp_thread.temperatures.items()))
                for i, (name, temp) in enumerate(temperatures):
                    row = lm_sensors_table.addRow([agent.Integer32(i + 1)])
                    row.setRowCell(1, agent.Integer32(i + 1))
                    row.setRowCell(2, agent.DisplayString(name))
                    row.setRowCell(3, agent.Unsigned32(temp))

            if hdd_temp_table:
                hdd_temp_table.clear()
                if disk_temp_thread:
                    for i, (name, temp) in enumerate(list(disk_temp_thread.temperatures.items())):
                        row = hdd_temp_table.addRow([agent.Integer32(i + 1)])
                        row.setRowCell(2, agent.DisplayString(name))
                        row.setRowCell(3, agent.Unsigned32(temp))

            kstat = get_kstat()
            arc_efficiency = get_arc_efficiency(kstat)

            prefix = "kstat.zfs.misc.arcstats"
            zfs_arc_size.update(kstat[f"{prefix}.size"] / 1024)
            zfs_arc_meta.update(kstat[f"{prefix}.arc_meta_used"] / 1024)
            zfs_arc_data.update(kstat[f"{prefix}.data_size"] / 1024)
            zfs_arc_hits.update(kstat[f"{prefix}.hits"] % 2 ** 32)
            zfs_arc_misses.update(kstat[f"{prefix}.misses"] % 2 ** 32)
            zfs_arc_c.update(kstat[f"{prefix}.c"] / 1024)
            zfs_arc_p.update(kstat[f"{prefix}.p"] / 1024)
            zfs_arc_miss_percent.update(str(get_zfs_arc_miss_percent(kstat)).encode("ascii"))
            zfs_arc_cache_hit_ratio.update(str(arc_efficiency["cache_hit_ratio"]["per"][:-1]).encode("ascii"))
            zfs_arc_cache_miss_ratio.update(str(arc_efficiency["cache_miss_ratio"]["per"][:-1]).encode("ascii"))

            zfs_l2arc_hits.update(int(kstat[f"{prefix}.l2_hits"] % 2 ** 32))
            zfs_l2arc_misses.update(int(kstat[f"{prefix}.l2_misses"] % 2 ** 32))
            zfs_l2arc_read.update(int(kstat[f"{prefix}.l2_read_bytes"] / 1024 % 2 ** 32))
            zfs_l2arc_write.update(int(kstat[f"{prefix}.l2_write_bytes"] / 1024 % 2 ** 32))
            zfs_l2arc_size.update(int(kstat[f"{prefix}.l2_asize"] / 1024))

            if zilstat_1_thread:
                zfs_zilstat_ops1.update(zilstat_1_thread.value)
            if zilstat_5_thread:
                zfs_zilstat_ops5.update(zilstat_5_thread.value)
            if zilstat_10_thread:
                zfs_zilstat_ops10.update(zilstat_10_thread.value)

            last_update_at = datetime.utcnow()
