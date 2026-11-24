from collections import Counter
from datetime import date

from showquery.catalog import TABLES
from showquery.seed import build_fleet, date_window, simulate

DAYS = date_window(date(2026, 11, 24), 10)


def generate(seed=7):
    return list(simulate(build_fleet(seed), DAYS, seed))


def test_generation_is_deterministic():
    assert generate() == generate()
    assert generate(1) != generate(2)


def test_every_device_reports_every_day():
    docs = generate()
    per_device_day = Counter((d["device"], d["collected_date"]) for d in docs)
    devices = {d["device"] for d in docs}
    assert len(per_device_day) == len(devices) * len(DAYS)
    assert {d["collected_date"] for d in docs} == {d.isoformat() for d in DAYS}


def test_documents_contain_every_catalog_path():
    """Each catalog column must resolve to a real field, or users get silent NULLs."""
    docs = generate()
    for table in TABLES:
        sample = next(d for d in docs if all(d.get(k) == v for k, v in table.base_filter.items()))
        for column in table.columns:
            if column.name in ("output", "parsed"):
                continue
            value = sample
            for part in column.path.split("."):
                if isinstance(value, list):  # unwound array: check the first element
                    value = value[0]
                value = value[part]
            assert value is not None, (table.name, column.name)


def test_raw_output_mentions_parsed_values():
    for doc in generate():
        if doc["command"] == "show version":
            assert doc["parsed"]["os_version"] in doc["output"]
            assert doc["parsed"]["serial"] in doc["output"]
        if doc["command"] == "show interfaces status":
            assert all(i["name"] in doc["output"] for i in doc["parsed"]["interfaces"])


def test_enum_values_in_catalog_cover_generated_data():
    docs = generate()
    statuses = {i["status"] for d in docs if d["command"] == "show interfaces status"
                for i in d["parsed"]["interfaces"]}
    states = {n["state"] for d in docs if d["command"] == "show ip bgp summary"
              for n in d["parsed"]["neighbors"]}
    by_name = {t.name: t for t in TABLES}
    assert statuses <= set(by_name["interfaces"].column("status").values)
    assert states <= set(by_name["bgp_neighbors"].column("state").values)
