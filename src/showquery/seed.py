"""Generate synthetic daily show-command snapshots and load them into MongoDB.

Each device is simulated day by day with its own seeded RNG, so the data is fully
reproducible and has realistic stories in it: OS upgrades part-way through the window
(with uptime resets), interface flaps, err-disabled ports, BGP sessions dropping and
prefix-count drops. Raw output is rendered per platform (IOS / EOS / NX-OS) and the
structured form is stored alongside it in ``parsed``, the way a TextFSM-at-ingest
pipeline would store it.

    showquery-seed --days 30 --reset
"""

from __future__ import annotations

import argparse
import logging
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, MongoClient

from .catalog import SITES
from .config import get_settings

log = logging.getLogger("showquery.seed")

CMD_VERSION = "show version"
CMD_INTERFACES = "show interfaces status"
CMD_BGP = "show ip bgp summary"

# role -> (platform, count per site)
ROLE_LAYOUT = {
    "core": ("arista_eos", 2),
    "dist": ("cisco_nxos", 2),
    "access": ("cisco_ios", 6),
    "edge": ("cisco_ios", 1),
}
ROLE_ABBREV = {"core": "core", "dist": "dist", "access": "acc", "edge": "edge"}

VERSIONS = {  # platform -> (current, upgrade target)
    "arista_eos": ("4.30.2F", "4.31.1F"),
    "cisco_nxos": ("9.3(10)", "10.2(6)"),
    "cisco_ios": ("16.12.4", "17.6.5"),
}
MODELS = {
    "core": "DCS-7280SR-48C6",
    "dist": "N9K-C93180YC-EX",
    "access": "C9300-48P",
    "edge": "ISR4451-X/K9",
}
LOCAL_AS = 65000
ISPS = ((7018, "att"), (3356, "lumen"), (174, "cogent"))


@dataclass
class Interface:
    name: str
    description: str
    vlan: str
    speed_up: str
    media: str
    status: str = "connected"
    err_days_left: int = 0


@dataclass
class Neighbor:
    neighbor: str
    remote_as: int
    baseline_prefixes: int
    state: str = "Established"
    prefixes_received: int = 0
    established_at: datetime | None = None
    down_days_left: int = 0
    msgs: int = 0


@dataclass
class Device:
    hostname: str
    site: str
    role: str
    platform: str
    mgmt_ip: str
    model: str
    serial: str
    os_version: str
    router_id: str
    interfaces: list[Interface] = field(default_factory=list)
    neighbors: list[Neighbor] = field(default_factory=list)
    booted_at: datetime | None = None
    upgrade_day: int | None = None  # index into the simulated window


# ---- fleet ------------------------------------------------------------------------------

def build_fleet(seed: int) -> list[Device]:
    rng = random.Random(f"{seed}:fleet")
    devices: list[Device] = []
    for site_idx, site in enumerate(SITES, start=1):
        for role, (platform, count) in ROLE_LAYOUT.items():
            for n in range(1, count + 1):
                host_no = len(devices) + 1
                devices.append(Device(
                    hostname=f"{site}-{ROLE_ABBREV[role]}-{n:02d}",
                    site=site,
                    role=role,
                    platform=platform,
                    mgmt_ip=f"10.{site_idx}.0.{host_no}",
                    model=MODELS[role],
                    serial=_serial(rng, platform),
                    os_version=VERSIONS[platform][0],
                    router_id=f"10.255.{site_idx}.{host_no}",
                ))
    by_site: dict[str, list[Device]] = {}
    for d in devices:
        by_site.setdefault(d.site, []).append(d)
    for site_devices in by_site.values():
        _wire_site(rng, site_devices)
    return devices


def _serial(rng: random.Random, platform: str) -> str:
    prefix = {"arista_eos": "JPE", "cisco_nxos": "FDO", "cisco_ios": "FOC"}[platform]
    return prefix + "".join(rng.choice("0123456789ABCDEFGHJKLMNPRSTUVWXYZ") for _ in range(8))


def _wire_site(rng: random.Random, devices: list[Device]) -> None:
    cores = [d for d in devices if d.role == "core"]
    dists = [d for d in devices if d.role == "dist"]
    for d in devices:
        if d.role == "access":
            for p in range(1, 25):
                kind = rng.random()
                desc = f"AP-{d.hostname[-2:]}-{p:02d}" if kind < 0.15 else (
                    f"user-{p:02d}" if kind < 0.75 else "")
                iface = Interface(f"Gi1/0/{p}", desc, str(rng.choice([10, 20, 30, 100])),
                                  "a-1000", "10/100/1000BaseTX")
                iface.status = rng.choices(
                    ["connected", "notconnect", "disabled"], weights=[70, 20, 10])[0]
                d.interfaces.append(iface)
            for u, dist in enumerate(dists, start=1):
                d.interfaces.append(Interface(f"Te1/1/{u}", f"uplink-{dist.hostname}", "trunk",
                                              "10G", "SFP-10GBase-SR"))
        elif d.role == "dist":
            for p, core in enumerate(cores, start=1):
                d.interfaces.append(Interface(f"Eth1/{p}", f"to-{core.hostname}", "routed",
                                              "100G", "QSFP-100G-SR4"))
            for p in range(3, 17):
                d.interfaces.append(Interface(f"Eth1/{p}", f"access-{p - 2:02d}", "trunk",
                                              "10G", "10Gbase-SR",
                                              status="connected" if p < 9 else "notconnect"))
        elif d.role == "core":
            others = [x for x in devices if x.role in ("core", "dist", "edge") and x is not d]
            for p, other in enumerate(others, start=1):
                d.interfaces.append(Interface(f"Et{p}", f"to-{other.hostname}", "routed",
                                              "100G", "100GBASE-SR4"))
            for p in range(len(others) + 1, 17):
                d.interfaces.append(Interface(f"Et{p}", "", "routed", "100G", "100GBASE-SR4",
                                              status="notconnect"))
        else:  # edge
            isp_as, isp_name = rng.choice(ISPS)
            d.interfaces.append(Interface("Gi0/0/0", f"isp-{isp_name}", "routed", "1000",
                                          "1000BaseLX"))
            for p, core in enumerate(cores, start=1):
                d.interfaces.append(Interface(f"Gi0/0/{p}", f"to-{core.hostname}", "routed",
                                              "1000", "1000BaseSX"))
            d.neighbors.append(Neighbor(f"203.0.113.{rng.randint(1, 254)}", isp_as,
                                        rng.randint(940_000, 990_000)))

    # iBGP: cores peer with each other, dists and edge; dists and edge peer with cores.
    for core in cores:
        for other in devices:
            if other is core or other.role not in ("core", "dist", "edge"):
                continue
            core.neighbors.append(Neighbor(other.router_id, LOCAL_AS, rng.randint(80, 400)))
            if other.role != "core":
                other.neighbors.append(Neighbor(core.router_id, LOCAL_AS, rng.randint(900, 1200)))


# ---- simulation -------------------------------------------------------------------------

def simulate(devices: list[Device], days: list[date], seed: int) -> Iterator[dict[str, Any]]:
    """Yield one Mongo document per device, per day, per command."""
    for device in devices:
        rng = random.Random(f"{seed}:{device.hostname}")
        start = _collection_time(days[0], rng)
        device.booted_at = start - timedelta(
            days=rng.randint(20, 420), seconds=rng.randint(0, 86_400))
        upgrades = len(days) > 3 and rng.random() < 0.3
        device.upgrade_day = rng.randrange(3, len(days)) if upgrades else None
        for n in device.neighbors:
            n.established_at = device.booted_at + timedelta(minutes=rng.randint(2, 15))
            n.msgs = rng.randint(1_000, 50_000)

        for idx, day in enumerate(days):
            collected_at = _collection_time(day, rng)
            _advance_day(device, idx, collected_at, rng)
            for command, parsed, output in _render_all(device, collected_at):
                yield {
                    "device": device.hostname,
                    "site": device.site,
                    "role": device.role,
                    "platform": device.platform,
                    "mgmt_ip": device.mgmt_ip,
                    "command": command,
                    "collected_at": collected_at,
                    "collected_date": day.isoformat(),
                    "output": output,
                    "parsed": parsed,
                }


def _collection_time(day: date, rng: random.Random) -> datetime:
    return datetime.combine(day, time(2, 0), tzinfo=timezone.utc) + timedelta(
        seconds=rng.randint(0, 3_600))


def _advance_day(device: Device, idx: int, now: datetime, rng: random.Random) -> None:
    rebooted = False
    if device.upgrade_day == idx:
        device.os_version = VERSIONS[device.platform][1]
        rebooted = True
    elif rng.random() < 0.01:  # unplanned reboot
        rebooted = True
    if rebooted:
        device.booted_at = now - timedelta(hours=rng.randint(1, 20))
        for n in device.neighbors:
            n.established_at = device.booted_at + timedelta(minutes=rng.randint(2, 10))
            n.down_days_left = 0

    for iface in device.interfaces:
        if iface.err_days_left > 0:
            iface.err_days_left -= 1
            iface.status = "err-disabled" if iface.err_days_left else "connected"
            continue
        if device.role != "access" or iface.vlan == "trunk" or iface.status == "disabled":
            continue
        roll = rng.random()
        if roll < 0.004:
            iface.status, iface.err_days_left = "err-disabled", rng.randint(1, 4)
        elif roll < 0.04:
            iface.status = "notconnect" if iface.status == "connected" else "connected"

    for n in device.neighbors:
        n.msgs += rng.randint(2_800, 3_100)
        if n.down_days_left > 0:
            n.down_days_left -= 1
            if n.down_days_left == 0:
                n.state, n.established_at = "Established", now - timedelta(hours=rng.randint(1, 23))
        elif rng.random() < 0.012:
            n.state = rng.choice(["Idle", "Active", "Connect"])
            n.down_days_left = rng.randint(1, 3)
            n.established_at = now - timedelta(hours=rng.randint(1, 20))  # time since down

        if n.state == "Established":
            drift = rng.uniform(-0.01, 0.01)
            if rng.random() < 0.01:
                drift = -rng.uniform(0.3, 0.6)  # upstream withdrew routes
            n.prefixes_received = max(0, int(n.baseline_prefixes * (1 + drift)))
        else:
            n.prefixes_received = 0


# ---- rendering --------------------------------------------------------------------------

def _render_all(device: Device, now: datetime) -> Iterator[tuple[str, dict[str, Any], str]]:
    uptime = int((now - device.booted_at).total_seconds())
    version = {
        "os_version": device.os_version,
        "model": device.model,
        "serial": device.serial,
        "uptime_seconds": uptime,
    }
    yield CMD_VERSION, version, RENDER_VERSION[device.platform](device, uptime)

    interfaces = [{
        "name": i.name,
        "description": i.description,
        "status": i.status,
        "vlan": i.vlan,
        "duplex": ("a-full" if device.platform == "cisco_ios" else "full")
        if i.status == "connected" else "auto",
        "speed": i.speed_up if i.status == "connected" else "auto",
        "type": i.media,
    } for i in device.interfaces]
    yield CMD_INTERFACES, {"interfaces": interfaces}, RENDER_INTERFACES[device.platform](interfaces)

    if device.neighbors:
        neighbors = [{
            "neighbor": n.neighbor,
            "remote_as": n.remote_as,
            "state": n.state,
            "prefixes_received": n.prefixes_received,
            "uptime_seconds": max(0, int((now - n.established_at).total_seconds())),
            "msg_rcvd": n.msgs,
            "msg_sent": n.msgs + 7,
        } for n in device.neighbors]
        yield CMD_BGP, {"router_id": device.router_id, "local_as": LOCAL_AS,
                        "neighbors": neighbors}, RENDER_BGP[device.platform](device, neighbors)


def _dhm(seconds: int) -> tuple[int, int, int, int]:
    weeks, rem = divmod(seconds, 7 * 86_400)
    days, rem = divmod(rem, 86_400)
    hours, rem = divmod(rem, 3_600)
    return weeks, days, hours, rem // 60


def _ios_uptime(seconds: int) -> str:
    w, d, h, m = _dhm(seconds)
    parts = [(w, "week"), (d, "day"), (h, "hour"), (m, "minute")]
    return ", ".join(f"{v} {u}{'s' if v != 1 else ''}" for v, u in parts if v) or "0 minutes"


def _bgp_updown(seconds: int) -> str:
    w, d, h, m = _dhm(seconds)
    if w:
        return f"{w}w{d}d"
    if d:
        return f"{d}d{h:02d}h"
    return f"{h:02d}:{m:02d}:{seconds % 60:02d}"


def _version_ios(dev: Device, uptime: int) -> str:
    train = "Amsterdam" if dev.os_version.startswith("17") else "Gibraltar"
    return (
        f"Cisco IOS XE Software, Version {dev.os_version}\n"
        f"Cisco IOS Software [{train}], Catalyst L3 Switch Software (CAT9K_IOSXE), "
        f"Version {dev.os_version}, RELEASE SOFTWARE (fc2)\n"
        "Technical Support: http://www.cisco.com/techsupport\n"
        "Copyright (c) 1986-2024 by Cisco Systems, Inc.\n\n"
        "ROM: IOS-XE ROMMON\n"
        f"{dev.hostname} uptime is {_ios_uptime(uptime)}\n"
        "System returned to ROM by Reload Command\n"
        f'System image file is "flash:packages.conf"\n\n'
        f"cisco {dev.model} (X86) processor with 1863083K/6147K bytes of memory.\n"
        f"Processor board ID {dev.serial}\n"
        "8388608K bytes of physical memory.\n\n"
        f"Model Number                       : {dev.model}\n"
        f"System Serial Number               : {dev.serial}\n"
    )


def _version_eos(dev: Device, uptime: int) -> str:
    w, d, h, m = _dhm(uptime)
    return (
        f"Arista {dev.model}\n"
        "Hardware version: 11.00\n"
        f"Serial number: {dev.serial}\n"
        f"System MAC address: 2899.3a{dev.serial[-4:].lower()}.0001\n\n"
        f"Software image version: {dev.os_version}\n"
        "Architecture: x86_64\n"
        f"Internal build version: {dev.os_version}-3532817.{dev.os_version.replace('.', '')}\n\n"
        f"Uptime: {w} weeks, {d} days, {h} hours and {m} minutes\n"
        "Total memory: 32458796 kB\n"
        "Free memory: 25101248 kB\n"
    )


def _version_nxos(dev: Device, uptime: int) -> str:
    days, rem = divmod(uptime, 86_400)
    hours, rem = divmod(rem, 3_600)
    return (
        "Cisco Nexus Operating System (NX-OS) Software\n"
        "TAC support: http://www.cisco.com/tac\n\n"
        "Software\n"
        "  BIOS: version 07.69\n"
        f"  NXOS: version {dev.os_version}\n"
        f"  NXOS image file is: bootflash:///nxos64-cs.{dev.os_version}.M.bin\n\n"
        "Hardware\n"
        f"  cisco Nexus9000 {dev.model.replace('N9K-', '')} chassis\n"
        f"  Processor Board ID {dev.serial}\n\n"
        f"  Device name: {dev.hostname}\n\n"
        f"Kernel uptime is {days} day(s), {hours} hour(s), {rem // 60} minute(s), "
        f"{rem % 60} second(s)\n"
    )


def _ifaces_ios(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'Port':<9} {'Name':<18} {'Status':<12} {'Vlan':<10} {'Duplex':>6} "
             f"{'Speed':>6} Type"]
    lines += [f"{r['name']:<9} {r['description'][:18]:<18} {r['status']:<12} {r['vlan']:<10} "
              f"{r['duplex']:>6} {r['speed']:>6} {r['type']}" for r in rows]
    return "\n".join(lines) + "\n"


def _ifaces_eos(rows: list[dict[str, Any]]) -> str:
    lines = [f"{'Port':<10} {'Name':<22} {'Status':<12} {'Vlan':<8} {'Duplex':<6} "
             f"{'Speed':<6} Type"]
    lines += [f"{r['name']:<10} {r['description'][:22]:<22} {r['status']:<12} {r['vlan']:<8} "
              f"{r['duplex']:<6} {r['speed']:<6} {r['type']}" for r in rows]
    return "\n".join(lines) + "\n"


def _ifaces_nxos(rows: list[dict[str, Any]]) -> str:
    rule = "-" * 80
    lines = [rule, f"{'Port':<13} {'Name':<18} {'Status':<9} {'Vlan':<9} {'Duplex':<7} "
             f"{'Speed':<7} Type", rule]
    lines += [f"{r['name']:<13} {r['description'][:18]:<18} {r['status'][:9]:<9} {r['vlan']:<9} "
              f"{r['duplex']:<7} {r['speed']:<7} {r['type']}" for r in rows]
    return "\n".join(lines) + "\n"


def _state_col(n: dict[str, Any]) -> str:
    return str(n["prefixes_received"]) if n["state"] == "Established" else n["state"]


def _bgp_ios(dev: Device, rows: list[dict[str, Any]]) -> str:
    lines = [f"BGP router identifier {dev.router_id}, local AS number {LOCAL_AS}",
             "BGP table version is 48211, main routing table version 48211", "",
             "Neighbor        V           AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  "
             "State/PfxRcd"]
    lines += [f"{n['neighbor']:<15} 4 {n['remote_as']:>12} {n['msg_rcvd']:>7} {n['msg_sent']:>7} "
              f"{48211:>8} {0:>4} {0:>4} {_bgp_updown(n['uptime_seconds']):<8} {_state_col(n)}"
              for n in rows]
    return "\n".join(lines) + "\n"


def _bgp_eos(dev: Device, rows: list[dict[str, Any]]) -> str:
    lines = ["BGP summary information for VRF default",
             f"Router identifier {dev.router_id}, local AS number {LOCAL_AS}",
             "Neighbor Status Codes: m - Under maintenance",
             "  Neighbor         V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   "
             "PfxRcd PfxAcc"]
    for n in rows:
        state = "Estab" if n["state"] == "Established" else n["state"]
        lines.append(f"  {n['neighbor']:<16} 4 {n['remote_as']:<12} {n['msg_rcvd']:>9} "
                     f"{n['msg_sent']:>9} {0:>4} {0:>4} {_bgp_updown(n['uptime_seconds']):>8} "
                     f"{state:<7} {n['prefixes_received']:>6} {n['prefixes_received']:>6}")
    return "\n".join(lines) + "\n"


def _bgp_nxos(dev: Device, rows: list[dict[str, Any]]) -> str:
    up = sum(1 for n in rows if n["state"] == "Established")
    lines = ["BGP summary information for VRF default, address family IPv4 Unicast",
             f"BGP router identifier {dev.router_id}, local AS number {LOCAL_AS}",
             f"BGP table version is 9921, IPv4 Unicast config peers {len(rows)}, "
             f"capable peers {up}", "",
             "Neighbor        V    AS MsgRcvd MsgSent   TblVer  InQ OutQ Up/Down  State/PfxRcd"]
    lines += [f"{n['neighbor']:<15} 4 {n['remote_as']:>5} {n['msg_rcvd']:>7} {n['msg_sent']:>7} "
              f"{9921:>8} {0:>4} {0:>4} {_bgp_updown(n['uptime_seconds']):<8} {_state_col(n)}"
              for n in rows]
    return "\n".join(lines) + "\n"


RENDER_VERSION = {"cisco_ios": _version_ios, "arista_eos": _version_eos,
                  "cisco_nxos": _version_nxos}
RENDER_INTERFACES = {"cisco_ios": _ifaces_ios, "arista_eos": _ifaces_eos,
                     "cisco_nxos": _ifaces_nxos}
RENDER_BGP = {"cisco_ios": _bgp_ios, "arista_eos": _bgp_eos, "cisco_nxos": _bgp_nxos}


# ---- loading ----------------------------------------------------------------------------

INDEXES = (
    [("command", ASCENDING), ("collected_date", ASCENDING), ("device", ASCENDING)],
    [("device", ASCENDING), ("collected_date", ASCENDING)],
    [("site", ASCENDING), ("command", ASCENDING), ("collected_date", ASCENDING)],
)


def date_window(end: date, days: int) -> list[date]:
    return [end - timedelta(days=offset) for offset in range(days - 1, -1, -1)]


def main(argv: list[str] | None = None) -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--days", type=int, default=30, help="days of history (default 30)")
    parser.add_argument("--end-date", type=date.fromisoformat, default=None,
                        help="last collection day, YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reset", action="store_true", help="drop the collection first")
    parser.add_argument("--if-empty", action="store_true",
                        help="do nothing if the collection already has documents")
    parser.add_argument("--mongo-uri", default=settings.mongo_uri)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    end = args.end_date or datetime.now(timezone.utc).date()
    days = date_window(end, args.days)
    collection = MongoClient(args.mongo_uri)[settings.mongo_db][settings.mongo_collection]
    if args.reset:
        collection.drop()
    elif args.if_empty and collection.estimated_document_count() > 0:
        log.info("%s.%s already has data; not seeding", settings.mongo_db,
                 settings.mongo_collection)
        return

    fleet = build_fleet(args.seed)
    batch: list[dict[str, Any]] = []
    total = 0
    for doc in simulate(fleet, days, args.seed):
        batch.append(doc)
        if len(batch) >= 2_000:
            collection.insert_many(batch, ordered=False)
            total += len(batch)
            batch.clear()
    if batch:
        collection.insert_many(batch, ordered=False)
        total += len(batch)
    for keys in INDEXES:
        collection.create_index(keys)
    log.info("inserted %d documents for %d devices over %d days (%s..%s) into %s.%s",
             total, len(fleet), len(days), days[0], days[-1], settings.mongo_db,
             settings.mongo_collection)


if __name__ == "__main__":
    main()
