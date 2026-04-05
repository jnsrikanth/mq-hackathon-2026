#!/usr/bin/env python3
"""Generate a realistic 4000-row MQ topology CSV dataset.

Simulates an enterprise MQ environment with:
- ~40 queue managers across 4 neighborhoods
- ~120 applications across 6 lines of business
- ~2000 unique queues (local, remote, transmission, alias)
- Realistic naming conventions matching the enterprise format
- Producer/consumer relationships with realistic fan-out patterns
- Brittle apps (connected to multiple QMs) to show transformation value

Output: data/as_is_topology.csv
"""

import csv
import hashlib
import os
import random
import sys

# Seed for reproducibility
random.seed(42)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

NUM_ROWS = 4000

NEIGHBORHOODS = ["Mainframe", "Private PaaS", "Private IaaS", "Public Cloud"]
LOBS = ["TECHCT", "TECHCCIBT", "TECHFIN", "TECHOPS", "TECHSEC", "TECHRISK"]
HOSTING_TYPES = ["Internal", "External"]
DATA_CLASSIFICATIONS = ["Confidential", "Internal Use", "Public", "Restricted"]
TRTC_VALUES = [
    "00= 0-30 Minutes", "01= 30 Minutes to 2 Hours",
    "02= 2 Hours to 4 Hours", "03= 4:01 to 11:59 Hours",
    "04= 12 to 24 Hours", "05= 1 to 3 Days",
]
CLUSTER_NAMES = [
    "TECHCT", "TECHCCIBT", "TECHFIN", "TECHOPS", "TECHSEC",
    "TECHRISK", "TECHPAY", "TECHCORE",
]

# Queue manager naming: WL6XX## or WQ## format
QM_PREFIXES = ["WL6E", "WL6F", "WL6G", "WQ", "WR", "WS"]
QM_SUFFIXES = [
    "R2A", "R2B", "R2C", "R2D", "R2E", "R2F",
    "X2A", "X2B", "X2C", "X2D",
    "26", "27", "28", "29", "30", "31",
]

# Application ID patterns (2-4 char alphanumeric)
APP_ID_POOL = []
for prefix in ["8A", "8B", "8C", "9A", "9B", "PP", "QQ", "RR", "SS", "TT",
               "UV", "WX", "YZ", "AB", "CD", "EF", "GH", "IJ", "KL", "MN"]:
    APP_ID_POOL.append(prefix)
    for suffix in ["FK", "SM", "LT", "RX", "DV", "NP"]:
        APP_ID_POOL.append(f"{prefix}{suffix}")

# Application full names (obfuscated but realistic)
APP_NAMES = [
    "DFCeth - HUVY MIPKNVL ZYU", "OOIKN JIU", "PP Arcne VMU",
    "Hdc - Hdxknvlr Kavk & Hncvympqf", "Krypton Payment Gateway",
    "Mercury Trade Settlement", "Neptune Risk Analytics",
    "Saturn Compliance Monitor", "Jupiter Order Management",
    "Mars Portfolio Tracker", "Venus Client Onboarding",
    "Pluto Fraud Detection", "Orion Data Warehouse",
    "Sirius Notification Hub", "Vega Authentication Service",
    "Altair Reporting Engine", "Rigel Batch Processor",
    "Deneb Message Router", "Polaris Config Manager",
    "Capella Audit Logger", "Arcturus Cache Service",
    "Betelgeuse Event Streamer", "Procyon API Gateway",
    "Aldebaran Rate Engine", "Antares Settlement Bus",
    "Spica Reconciliation", "Regulus Position Keeper",
    "Fomalhaut Market Data", "Canopus Trade Capture",
    "Achernar Limit Monitor",
]

APP_DISPS = [
    "Mainframe", "Private PaaS", "Private IaaS", "Public Cloud",
    "Full App Retire, Mainframe", "Private IaaS, Private PaaS",
]

# Queue name patterns
QUEUE_PURPOSES = [
    "RQST", "RESP", "ACK", "DAT", "ERR", "NTFY", "CMD", "EVT",
    "LOG", "STAT", "CTRL", "SYNC", "BATCH", "ALERT", "RPT",
]


def generate_qm_name() -> str:
    prefix = random.choice(QM_PREFIXES)
    suffix = random.choice(QM_SUFFIXES)
    return f"{prefix}{suffix}"


def generate_queue_managers(count: int = 40) -> list[str]:
    qms = set()
    while len(qms) < count:
        qms.add(generate_qm_name())
    return sorted(qms)


def generate_apps(count: int = 120) -> list[dict]:
    apps = []
    used_ids = set()
    for i in range(count):
        app_id = APP_ID_POOL[i % len(APP_ID_POOL)]
        if app_id in used_ids:
            app_id = f"{app_id}{i % 10}"
        used_ids.add(app_id)

        apps.append({
            "app_id": app_id,
            "full_name": APP_NAMES[i % len(APP_NAMES)],
            "disp": random.choice(APP_DISPS),
            "neighborhood": random.choice(NEIGHBORHOODS),
            "hosting": random.choice(HOSTING_TYPES),
            "data_class": random.choice(DATA_CLASSIFICATIONS),
            "ecpa": random.choice(["Yes", "No", "No", "No"]),
            "pci": random.choice(["Yes", "No", "No", "No", "No"]),
            "public": random.choice(["Yes", "No", "No", "No", "No"]),
            "trtc": random.choice(TRTC_VALUES),
            "lob": random.choice(LOBS),
        })
    return apps


def generate_queue_name(producer_id: str, consumer_id: str, purpose: str) -> str:
    return f"{producer_id}.{consumer_id}.{purpose}"


def generate_rows(
    queue_managers: list[str],
    apps: list[dict],
    target_rows: int = 4000,
) -> list[dict]:
    rows = []
    app_to_qm: dict[str, str] = {}

    # Assign each app to a primary QM
    for app in apps:
        app_to_qm[app["app_id"]] = random.choice(queue_managers)

    # Make ~15% of apps "brittle" (connected to multiple QMs)
    brittle_apps = random.sample(apps, k=int(len(apps) * 0.15))
    for app in brittle_apps:
        # Add a second QM
        second_qm = random.choice(queue_managers)
        while second_qm == app_to_qm[app["app_id"]]:
            second_qm = random.choice(queue_managers)
        app["second_qm"] = second_qm

    # Generate producer-consumer pairs
    pairs_generated = 0
    while pairs_generated < target_rows // 2:
        producer = random.choice(apps)
        consumer = random.choice(apps)
        if producer["app_id"] == consumer["app_id"]:
            continue

        purpose = random.choice(QUEUE_PURPOSES)
        queue_name = generate_queue_name(
            producer["app_id"], consumer["app_id"], purpose
        )

        producer_qm = app_to_qm[producer["app_id"]]
        consumer_qm = app_to_qm[consumer["app_id"]]

        # Determine queue type based on whether QMs are same or different
        if producer_qm == consumer_qm:
            q_type_mq = "Local"
            remote_qm = ""
            remote_q = ""
            xmit_q = ""
        else:
            q_type_mq = "Remote"
            remote_qm = consumer_qm
            remote_q = f"{queue_name}.{consumer_qm[:4]}"
            xmit_q = f"{producer["app_id"]}.{consumer_qm}"

        cluster = random.choice(CLUSTER_NAMES)
        persistence = random.choice(["Yes", "No"])
        put_response = random.choice(["Synchronous", "Asynchronous"])

        # Producer row
        rows.append({
            "Discrete Queue Name": queue_name,
            "ProducerName": producer["full_name"],
            "ConsumerName": consumer["full_name"],
            "Primary App_Full_Name": producer["full_name"],
            "PrimaryAppDisp": producer["disp"],
            "PrimaryAppRole": "Producer",
            "Primary Application Id": producer["app_id"],
            "q_type": q_type_mq,
            "Primary Neighborhood": producer["neighborhood"],
            "Primary Hosting Type": producer["hosting"],
            "Primary Data classification": producer["data_class"],
            "Primary Enterprise Critical Payment Application": producer["ecpa"],
            "Primary PCI": producer["pci"],
            "Primary Publicly Accessible": producer["public"],
            "Primary TRTC": producer["trtc"],
            "queue_manager_name": producer_qm,
            "app_id": producer["app_id"],
            "line_of_business": producer["lob"],
            "cluster_name": cluster,
            "cluster_namelist": cluster,
            "def_persistence": persistence,
            "def_put_response": put_response,
            "inhibit_get": random.choice(["0", "Enabled"]),
            "inhibit_put": random.choice(["0", "Enabled"]),
            "remote_q_mgr_name": remote_qm,
            "remote_q_name": remote_q,
            "usage": random.choice(["0", "Normal"]),
            "xmit_q_name": xmit_q,
            "Neighborhood": producer["neighborhood"],
        })

        # Consumer row
        rows.append({
            "Discrete Queue Name": queue_name,
            "ProducerName": producer["full_name"],
            "ConsumerName": consumer["full_name"],
            "Primary App_Full_Name": consumer["full_name"],
            "PrimaryAppDisp": consumer["disp"],
            "PrimaryAppRole": "Consumer",
            "Primary Application Id": consumer["app_id"],
            "q_type": q_type_mq,
            "Primary Neighborhood": consumer["neighborhood"],
            "Primary Hosting Type": consumer["hosting"],
            "Primary Data classification": consumer["data_class"],
            "Primary Enterprise Critical Payment Application": consumer["ecpa"],
            "Primary PCI": consumer["pci"],
            "Primary Publicly Accessible": consumer["public"],
            "Primary TRTC": consumer["trtc"],
            "queue_manager_name": consumer_qm,
            "app_id": consumer["app_id"],
            "line_of_business": consumer["lob"],
            "cluster_name": cluster,
            "cluster_namelist": cluster,
            "def_persistence": persistence,
            "def_put_response": put_response,
            "inhibit_get": random.choice(["0", "Enabled"]),
            "inhibit_put": random.choice(["0", "Enabled"]),
            "remote_q_mgr_name": remote_qm,
            "remote_q_name": remote_q,
            "usage": random.choice(["0", "Normal"]),
            "xmit_q_name": xmit_q,
            "Neighborhood": consumer["neighborhood"],
        })

        pairs_generated += 1

    return rows[:target_rows]


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "as_is_topology.csv")

    print(f"Generating {NUM_ROWS}-row MQ topology dataset...")

    qms = generate_queue_managers(40)
    apps = generate_apps(120)
    rows = generate_rows(qms, apps, NUM_ROWS)

    # Write CSV
    fieldnames = list(rows[0].keys())
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Stats
    unique_qms = set()
    unique_producers = set()
    unique_consumers = set()
    unique_queues = set()
    for row in rows:
        unique_qms.add(row["queue_manager_name"])
        unique_producers.add(row["ProducerName"])
        unique_consumers.add(row["ConsumerName"])
        unique_queues.add(row["Discrete Queue Name"])

    print(f"✅ Generated {len(rows)} rows → {out_path}")
    print(f"   Queue managers: {len(unique_qms)}")
    print(f"   Unique producers: {len(unique_producers)}")
    print(f"   Unique consumers: {len(unique_consumers)}")
    print(f"   Unique queues: {len(unique_queues)}")
    print(f"   Columns: {len(fieldnames)}")

    return out_path


if __name__ == "__main__":
    path = main()
    print(f"\nRun the demo with this dataset:")
    print(f"  .venv/bin/python demo.py {path}")
