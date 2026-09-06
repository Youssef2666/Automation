#!/usr/bin/env python
"""generate_seed.py - the ONE generator for every piece of synthetic demo data in the Automation Lab.

Writes (relative to the repo root, override with --out):
  seed/schema.sql              demo database schema (re-runnable: DROP ... CASCADE, then CREATE TABLE IF NOT EXISTS)
  seed/seed.sql                INSERT statements for the seeded tables + sequence resets
  seed/files/*                 customers.csv, orders.xlsx, invoice-locked.pdf, receipt-*.png, meeting-clip.wav,
                               article.md, attendance-week.csv
  seed/payloads/*.json         sample webhook bodies used by the workflows' test/ folders
  docker/mock-api/db.json      json-server dataset for the mock API

Everything is deterministic: random.seed(20260101) and a fixed "now" of 2026-09-01T09:00:00Z. Names, companies and
e-mails come from word lists; no real people, brands, clients or keys. E-mails end in @lab.local, company domains in
.example.com.

Standard library only. Optional extras improve some binary files (each has a pure-stdlib fallback):
  pillow           receipt PNGs (without it: no PNGs are written, a .txt sidecar explains why)
  openpyxl         orders.xlsx (fallback: minimal hand-built xlsx via zipfile)
  reportlab        invoice-locked.pdf (fallback: minimal hand-built text PDF)
  arabic-reshaper + python-bidi   correct glyph shaping for the Arabic receipt (fallback: unshaped text, noted)

Usage:
  python seed/generate_seed.py                 # write everything
  python seed/generate_seed.py --mock-api      # only docker/mock-api/db.json
  python seed/generate_seed.py --only sql      # sql | files | payloads | mock-api (repeatable)
  python seed/generate_seed.py --check         # regenerate in memory, compare text outputs with disk, validate SQL
  python seed/generate_seed.py --no-optional-deps   # force the stdlib fallbacks (for testing them)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import random
import re
import struct
import sys
import wave
import zipfile
from array import array
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEED = 20260101
NOW = datetime(2026, 9, 1, 9, 0, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
AMIRI_FONT = REPO_ROOT / "docker" / "docgen" / "fonts" / "Amiri-Regular.ttf"

# Row counts (the demo-data skill is the contract for these)
N_CUSTOMERS = 60
N_PRODUCTS = 40
N_ORDERS = 200
N_EMPLOYEES = 25
N_TICKETS = 80
N_LEADS = 40
N_BOOKINGS = 30
N_EXPENSES = 20
N_RATES = 30
N_MOCK_ORDERS = 120
N_MOCK_EVENTS = 300
N_POSTS = 25
N_CSV_CUSTOMERS = 60
N_XLSX_ORDERS = 40

# --- word lists (fictional combinations only) -----------------------------------------------------
FIRST_NAMES = [
    "Amal", "Bashir", "Dalia", "Farid", "Hana", "Idris", "Jamila", "Karim", "Lina", "Malik", "Nour", "Omar",
    "Rania", "Sami", "Tariq", "Yara", "Zaid", "Elena", "Marco", "Sofia", "Lukas", "Petra", "Jonas", "Mira",
    "Tomas", "Ines", "Selin", "Emre", "Deniz", "Aylin", "Wael", "Siham", "Nabil", "Rim", "Adel", "Layan",
]
LAST_NAMES = [
    "Haddad", "Mansour", "Saleh", "Nasr", "Farhat", "Barakat", "Khalil", "Zidan", "Rahal", "Sharif", "Weber",
    "Rossi", "Novak", "Fischer", "Costa", "Moreau", "Kaya", "Demir", "Lund", "Berg", "Amari", "Tarhuni",
    "Gharyani", "Bishti", "Zwai", "Ferjani", "Okur", "Vidal",
]
# (english, arabic) pairs for employees
AR_FIRST = [
    ("Ahmed", "أحمد"), ("Fatima", "فاطمة"), ("Omar", "عمر"), ("Layla", "ليلى"), ("Khalid", "خالد"), ("Mariam", "مريم"),
    ("Yusuf", "يوسف"), ("Salma", "سلمى"), ("Ali", "علي"), ("Huda", "هدى"), ("Tariq", "طارق"), ("Nour", "نور"),
    ("Hamza", "حمزة"), ("Rana", "رنا"), ("Bilal", "بلال"), ("Aisha", "عائشة"), ("Idris", "إدريس"), ("Dalia", "داليا"),
    ("Samir", "سمير"), ("Lina", "لينا"), ("Karim", "كريم"), ("Amal", "أمل"), ("Ziad", "زياد"), ("Hana", "هناء"),
    ("Faris", "فارس"),
]
AR_LAST = [
    ("Haddad", "حداد"), ("Mansour", "منصور"), ("Saleh", "صالح"), ("Nasr", "نصر"), ("Farhat", "فرحات"),
    ("Barakat", "بركات"), ("Khalil", "خليل"), ("Zidan", "زيدان"), ("Rahal", "رحال"), ("Sharif", "شريف"),
    ("Amari", "عماري"), ("Gharyani", "غرياني"), ("Tarhuni", "ترهوني"),
]
DEPARTMENTS = [
    ("Engineering", "الهندسة", ["Software Engineer", "Senior Engineer", "QA Analyst", "DevOps Engineer"]),
    ("Sales", "المبيعات", ["Account Executive", "Sales Associate", "Sales Manager"]),
    ("Support", "الدعم الفني", ["Support Agent", "Support Lead", "Technical Support Specialist"]),
    ("Finance", "المالية", ["Accountant", "Financial Analyst", "Payroll Officer"]),
    ("Operations", "العمليات", ["Operations Coordinator", "Logistics Planner", "Office Manager"]),
    ("Human Resources", "الموارد البشرية", ["HR Generalist", "Recruiter"]),
]
COUNTRIES = {
    "Libya": ["Tripoli", "Benghazi", "Misrata"], "Tunisia": ["Tunis", "Sfax"], "Egypt": ["Cairo", "Alexandria"],
    "Morocco": ["Casablanca", "Rabat"], "Turkey": ["Istanbul", "Ankara"], "UAE": ["Dubai", "Abu Dhabi"],
    "Germany": ["Berlin", "Hamburg"], "UK": ["London", "Manchester"], "Spain": ["Madrid", "Valencia"],
    "Italy": ["Rome", "Milan"],
}
PHONE_PREFIX = {
    "Libya": "+218 91", "Tunisia": "+216 20", "Egypt": "+20 10", "Morocco": "+212 6", "Turkey": "+90 53",
    "UAE": "+971 50", "Germany": "+49 151", "UK": "+44 7700", "Spain": "+34 6", "Italy": "+39 3",
}
# (slug, name, industry, size, country) -> domain is "<slug>.example.com"
COMPANIES = [
    ("acme", "Acme Trading", "Retail", "51-200", "Libya"),
    ("blueharbor", "Blue Harbor Logistics", "Logistics", "201-500", "Tunisia"),
    ("saharasolar", "Sahara Solar", "Energy", "11-50", "Libya"),
    ("olivegrove", "Olive Grove Foods", "Food & Beverage", "51-200", "Tunisia"),
    ("cedarsoft", "Cedar Software", "Software", "11-50", "Egypt"),
    ("falconfreight", "Falcon Freight", "Logistics", "201-500", "UAE"),
    ("meridianhealth", "Meridian Health Clinics", "Healthcare", "501-1000", "Egypt"),
    ("copperkettle", "Copper Kettle Cafe", "Hospitality", "1-10", "Libya"),
    ("stonebridge", "Stonebridge Builders", "Construction", "51-200", "Morocco"),
    ("lighthousemedia", "Lighthouse Media", "Media", "11-50", "Turkey"),
    ("pioneertutoring", "Pioneer Tutoring", "Education", "1-10", "Libya"),
    ("crescentpharma", "Crescent Pharmacy Group", "Healthcare", "51-200", "Libya"),
    ("palmcoast", "Palm Coast Travel", "Travel", "11-50", "Egypt"),
    ("ironwood", "Ironwood Furniture", "Manufacturing", "51-200", "Turkey"),
    ("silverline", "Silverline Printing", "Printing", "11-50", "Germany"),
    ("harbortech", "Harbor Tech Services", "IT Services", "11-50", "Spain"),
    ("summitaccounting", "Summit Accounting", "Professional Services", "1-10", "UK"),
    ("oasistextiles", "Oasis Textiles", "Manufacturing", "201-500", "Morocco"),
    ("brightwave", "Brightwave Marine", "Maritime", "51-200", "Italy"),
    ("greenfield", "Greenfield Agritech", "Agriculture", "11-50", "Tunisia"),
    ("nimbuscloud", "Nimbus Cloud Hosting", "Software", "51-200", "Germany"),
    ("quartzlabs", "Quartz Labs", "Research", "11-50", "UK"),
    ("redsand", "Red Sand Tours", "Travel", "1-10", "Libya"),
    ("marbleworks", "Marble Works Co", "Construction", "51-200", "Egypt"),
    ("skylinerealty", "Skyline Realty", "Real Estate", "11-50", "UAE"),
    ("duneelectric", "Dune Electric", "Energy", "201-500", "Libya"),
    ("velvetbakery", "Velvet Bakery", "Food & Beverage", "1-10", "Italy"),
    ("orbitlogistics", "Orbit Logistics", "Logistics", "501-1000", "Spain"),
    ("juniperdesign", "Juniper Design Studio", "Design", "1-10", "Turkey"),
    ("anchorinsurance", "Anchor Insurance", "Insurance", "201-500", "Germany"),
]
PRODUCT_CATALOG = {
    "Office": (["Desk Lamp", "Monitor Stand", "Ergonomic Chair", "Filing Cabinet", "Whiteboard", "Desk Organizer",
                "Standing Desk", "Cable Tray"], (15, 420)),
    "Electronics": (["USB-C Hub", "Wireless Mouse", "Mechanical Keyboard", "Webcam", "Headset", "Power Bank",
                     "Bluetooth Speaker", "Label Printer"], (18, 260)),
    "Kitchen": (["Coffee Grinder", "Electric Kettle", "Water Filter", "Lunch Box", "Thermos Flask", "Cutting Board",
                 "Tea Set", "Air Fryer"], (8, 180)),
    "Stationery": (["Notebook A5", "Gel Pen Set", "Sticky Notes", "Planner 2026", "Marker Set", "Stapler",
                    "Paper Ream", "Envelope Pack"], (2, 40)),
    "Outdoor": (["Camping Lantern", "Folding Chair", "Cooler Box", "Trekking Poles", "Rain Jacket", "Water Bottle",
                 "Picnic Blanket", "Headlamp"], (9, 150)),
}
PRODUCT_ADJ = ["Compact", "Classic", "Pro", "Eco", "Slim", "Deluxe", "Basic", "Studio"]
TICKET_TEMPLATES = {
    "billing": ["Invoice charged twice", "Refund not received for order {order}", "Wrong VAT rate on invoice",
                "Please update our billing address"],
    "technical": ["API returns 500 on /orders", "Cannot log in after password reset", "CSV export fails silently",
                  "Webhook deliveries are delayed"],
    "account": ["Change the account owner e-mail", "Add a second user to our account", "Delete my account data",
                "Two-factor code never arrives"],
    "shipping": ["Order {order} has not been delivered", "Package arrived damaged", "Change delivery address",
                 "Tracking number is missing"],
    "other": ["Feature request: dark mode", "Question about bulk pricing", "Partnership inquiry",
              "Feedback on the onboarding flow"],
}
TICKET_BODIES = [
    "Hello, {subject_lower}. This started on {day}. Could you look into it and let us know the next steps?",
    "Hi team, we noticed that {subject_lower}. It affects our daily work, so a quick update would be appreciated.",
    "Good morning. {subject}. I already checked the documentation and could not find an answer. Thanks in advance.",
    "مرحباً، {subject}. نرجو المتابعة في أقرب وقت ممكن. شكراً لكم.",
]
LEAD_SOURCES = ["website", "referral", "webinar", "linkedin-ad", "trade-show", "cold-outreach"]
BOOKING_SERVICES = ["Onboarding call", "Technical consultation", "Quarterly review", "Product demo", "Training session"]
EXPENSE_CATEGORIES = ["meals", "office", "travel", "software", "transport", "utilities"]
MERCHANTS = ["Copper Kettle Cafe", "Silverline Printing", "Palm Restaurant (مطعم النخلة)", "Nimbus Cloud Hosting",
             "City Taxi Co-op", "Velvet Bakery", "Red Sand Tours", "Dune Electric", "Harbor Tech Services",
             "Orbit Logistics", "Sahara Solar", "Pioneer Tutoring"]
BASE_RATES = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "LYD": 4.85, "EGP": 48.6, "TRY": 34.1, "AED": 3.67, "SAR": 3.75}
POST_TOPICS = [
    ("Why small teams automate first", ["automation", "small-business"]),
    ("Webhooks versus polling: picking the right trigger", ["webhooks", "integration"]),
    ("Idempotency keys explained with a coffee shop", ["patterns", "reliability"]),
    ("A gentle introduction to retries and backoff", ["patterns", "reliability"]),
    ("Turning a locked PDF into a spreadsheet", ["documents", "pdf"]),
    ("Arabic OCR: what actually works offline", ["ocr", "arabic"]),
    ("How to test workflows without hitting production", ["testing", "patterns"]),
    ("The case for a local mock API", ["testing", "integration"]),
    ("From inbox to ticket: parsing e-mail reliably", ["email", "support"]),
    ("Monitoring your monitors", ["monitoring", "observability"]),
    ("Exchange-rate alerts on a budget", ["monitoring", "finance"]),
    ("Attendance reports nobody has to build by hand", ["reports", "hr"]),
    ("A bilingual weekly report in one workflow", ["documents", "arabic"]),
    ("Local LLMs for ticket triage", ["ai", "support"]),
    ("RAG on your own docs with Qdrant and Ollama", ["ai", "rag"]),
    ("Meeting audio to action items", ["ai", "whisper"]),
    ("Repurposing one article into five posts", ["content", "ai"]),
    ("Lead enrichment without buying a data feed", ["sales", "integration"]),
    ("Booking reminders that respect time zones", ["scheduling", "email"]),
    ("Receipt photos to a monthly expense sheet", ["ocr", "finance"]),
    ("Backing up n8n to S3-compatible storage", ["devops", "backup"]),
    ("Export your workflows every night", ["devops", "git"]),
    ("Error handlers: the pattern every workflow needs", ["patterns", "errors"]),
    ("Rate limits are a feature", ["patterns", "api"]),
    ("Six months of an automation lab: lessons", ["automation", "retrospective"]),
]
POST_AUTHORS = ["Lab Editorial", "Nour Haddad", "Karim Rossi", "Mira Farhat"]

ARTICLE_MD = """# Automation in small businesses: where it pays off first

Most small businesses do not have an "automation problem". They have a Tuesday problem. On Tuesday the invoices go
out, the supplier spreadsheet gets reconciled, the same six customer questions arrive by e-mail, and somebody copies
booking details from a form into a calendar. None of these tasks is hard. All of them are boring, all of them are
repeated, and each one is a small opportunity to make a typo that costs an afternoon to untangle.

This article looks at automation from the point of view of a team of three to thirty people. It does not assume a
developer on staff, a cloud budget, or patience for vendor demos. It assumes a laptop, a couple of hours, and a
willingness to write down what actually happens on Tuesday.

## Start with the handoffs, not the tools

The first mistake teams make is to start from a tool. They sign up for a platform, look at the catalogue of
integrations, and try to find something to connect. The better starting point is the handoff: any moment where
information leaves one place and has to be typed into another. An order arriving by e-mail that becomes a row in a
spreadsheet. A receipt photographed on a phone that becomes a line in the expense report. A support request that
becomes a ticket, a ticket that becomes a task, a task that becomes a status update.

Write those handoffs down. For each one, note three things: how often it happens, how long it takes, and how often
it goes wrong. The ranking that falls out of that list is your roadmap. It is almost never the flashy item. It is
usually the one that happens forty times a week and goes wrong twice.

## The three shapes of an automation

Nearly every useful workflow in a small business is one of three shapes.

The first is the trigger and record shape. Something happens outside, a form is submitted, a webhook fires, a file
lands in a folder, and the workflow validates it and stores it somewhere structured. The value here is not speed; it
is that the record is complete and consistent every single time. A validation step that rejects a malformed order
with a clear message is worth more than a fast import that accepts anything.

The second is the schedule and report shape. Every morning, every Friday, or every month, the workflow gathers rows
from one or more sources, does some arithmetic, and produces a document or a message. Attendance summaries, sales
digests, exchange-rate snapshots and uptime reports all live here. These are the automations people notice, because
they replace a document somebody used to build by hand.

The third is the watch and alert shape. The workflow polls or listens, compares the current state with the last one
it saw, and notifies a human only when something changed. A price crossed a threshold, a website stopped answering, a
new release was published. The hard part of this shape is not the check; it is the memory. A watcher that forgets
what it saw last time will alert forever.

## What goes wrong, and how to plan for it

Automations fail in predictable ways, and the predictability is good news, because it means the fixes are reusable.

Duplicates are the most common failure. Webhooks are delivered twice, a poll overlaps with the previous run, a person
clicks submit twice. The fix is an idempotency key: a unique identifier per event, checked before doing any work.
It takes one lookup and saves hours of cleanup.

Transient errors come next. An API times out, a mail server refuses a connection for ten seconds, a rate limit kicks
in. The fix is a retry with a growing delay and a hard cap on attempts. Retrying immediately is not a retry; it is a
second failure.

Silent errors are the most expensive. A workflow stops running and nobody notices for a week. The fix is an error
handler that is shared by every workflow and that records the failure somewhere a human actually looks, plus a
heartbeat that alerts when a scheduled job has not reported in.

Finally, there is drift. The spreadsheet gains a column, the supplier renames a field, a colleague changes the form.
There is no clever fix for drift; there is only a test payload kept next to the workflow and a habit of running it
after every change.

## Keeping it local and honest

A surprising amount of automation does not need to leave the building. A small database, a mail sink, a local
document renderer and an optical character recognition service cover invoices, receipts, reports and inbound mail
without a single external account. Local language models are now good enough to classify a support ticket, summarise
a meeting recording or draft a first reply, and they run on ordinary hardware.

Working locally has a second benefit that is easy to underestimate: it forces you to use synthetic data. When the
demo customers are invented, you can share the workflow, screenshot it, and hand it to a new colleague without a
privacy review. The habits you build with fake data are the habits that protect the real data later.

## A realistic first month

Week one: list the handoffs and pick one. Week two: build the trigger and record version of it with a test payload
and a validation step. Week three: add the error handler and the idempotency check, then let it run unattended.
Week four: build the report that proves it worked, and show it to the people whose Tuesday just got shorter.

That is the whole method. It is not glamorous, and it does not require a platform decision. It requires noticing
where information is copied by hand, and refusing to do that copying more than once.
"""


# --- helpers -------------------------------------------------------------------------------------
def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def q(v: Any) -> str:
    """SQL literal: None -> NULL, numbers as-is, datetimes ISO, bool, dict/list -> jsonb, str quoted with '' escaping."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, datetime):
        return f"'{iso(v)}'"
    if isinstance(v, date):
        return f"'{v.isoformat()}'"
    if isinstance(v, (dict, list)):
        return "'" + json.dumps(v, ensure_ascii=False, separators=(",", ":")).replace("'", "''") + "'::jsonb"
    return "'" + str(v).replace("'", "''") + "'"


def money(x: float) -> float:
    return round(x + 1e-9, 2)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def rand_dt(start_days_ago: int, end_days_ago: int = 0, rng: random.Random | None = None) -> datetime:
    r = rng or random
    secs = r.uniform(end_days_ago * 86400, start_days_ago * 86400)
    return (NOW - timedelta(seconds=secs)).replace(microsecond=0)


def phone(country: str) -> str:
    return f"{PHONE_PREFIX[country]} {random.randint(100, 999)} {random.randint(1000, 9999)}"


# --- dataset --------------------------------------------------------------------------------------
def build_dataset() -> dict[str, Any]:
    random.seed(SEED)
    ds: dict[str, Any] = {}

    # companies (mock-api) ----------------------------------------------------------------------
    companies = []
    for i, (sl, name, industry, size, country) in enumerate(COMPANIES, 1):
        companies.append({
            "id": i, "domain": f"{sl}.example.com", "name": name, "industry": industry, "size": size,
            "country": country, "city": random.choice(COUNTRIES[country]), "founded": random.randint(1995, 2022),
            "website": f"https://{sl}.example.com", "linkedin_followers": random.randint(50, 20000),
        })
    ds["companies"] = companies

    # customers -----------------------------------------------------------------------------------
    used_emails: set[str] = set()

    def make_person() -> tuple[str, str]:
        fn, ln = random.choice(FIRST_NAMES), random.choice(LAST_NAMES)
        base = f"{fn}.{ln}".lower()
        email = f"{base}@lab.local"
        n = 1
        while email in used_emails:
            n += 1
            email = f"{base}{n}@lab.local"
        used_emails.add(email)
        return f"{fn} {ln}", email

    customers = []
    for i in range(1, N_CUSTOMERS + 1):
        name, email = make_person()
        co = random.choice(companies)
        customers.append({
            "id": i, "external_id": f"CUST-{i:04d}", "name": name, "email": email, "phone": phone(co["country"]),
            "company": co["name"], "country": co["country"], "city": random.choice(COUNTRIES[co["country"]]),
            "segment": random.choices(["smb", "mid", "enterprise"], weights=[55, 30, 15])[0],
            "created_at": rand_dt(400, 5),
        })
    customers.sort(key=lambda c: c["created_at"])
    for i, c in enumerate(customers, 1):
        c["id"], c["external_id"] = i, f"CUST-{i:04d}"
    ds["customers"] = customers

    # products ------------------------------------------------------------------------------------
    products = []
    pid = 0
    for cat, (names, (lo, hi)) in PRODUCT_CATALOG.items():
        for base in names:
            pid += 1
            price = money(random.uniform(lo, hi))
            price = math.floor(price) + random.choice([0.0, 0.5, 0.9, 0.99, 0.25])
            products.append({
                "id": pid, "sku": f"SKU-{pid:04d}", "name": f"{random.choice(PRODUCT_ADJ)} {base}", "category": cat,
                "price": money(price), "currency": "USD",
                "stock": random.choice([0, 0, 0] + list(range(3, 250))), "updated_at": rand_dt(60, 0),
            })
    ds["products"] = products

    # orders + items ------------------------------------------------------------------------------
    orders, items = [], []
    item_id = 0
    for i in range(1, N_ORDERS + 1):
        cust = random.choice(customers)
        ordered_at = rand_dt(120, 0)
        if ordered_at < cust["created_at"]:
            ordered_at = cust["created_at"] + timedelta(hours=random.randint(1, 72))
        status = random.choices(["pending", "paid", "shipped", "cancelled", "refunded"], weights=[15, 30, 40, 10, 5])[0]
        n_items = random.choices([1, 2, 3, 4], weights=[25, 45, 20, 10])[0]
        total = 0.0
        for prod in random.sample(products, n_items):
            item_id += 1
            qty = random.choices([1, 2, 3, 5, 10], weights=[50, 25, 12, 8, 5])[0]
            items.append({"id": item_id, "order_id": i, "product_id": prod["id"], "qty": qty,
                          "unit_price": prod["price"]})
            total += qty * prod["price"]
        shipped_at = None
        if status == "shipped":
            shipped_at = ordered_at + timedelta(hours=random.randint(6, 96))
        orders.append({
            "id": i, "order_number": f"ORD-2026-{i:04d}", "customer_id": cust["id"], "status": status,
            "total": money(total), "currency": "USD", "ordered_at": ordered_at, "shipped_at": shipped_at,
        })
    orders.sort(key=lambda o: o["ordered_at"])
    remap = {}
    for new_id, o in enumerate(orders, 1):
        remap[o["id"]] = new_id
        o["id"], o["order_number"] = new_id, f"ORD-2026-{new_id:04d}"
    for it in items:
        it["order_id"] = remap[it["order_id"]]
    items.sort(key=lambda it: (it["order_id"], it["id"]))
    for new_id, it in enumerate(items, 1):
        it["id"] = new_id
    ds["orders"], ds["order_items"] = orders, items

    # employees -----------------------------------------------------------------------------------
    employees = []
    for i in range(1, N_EMPLOYEES + 1):
        fn_en, fn_ar = AR_FIRST[i - 1]
        ln_en, ln_ar = random.choice(AR_LAST)
        dept_en, dept_ar, titles = random.choice(DEPARTMENTS)
        email = f"{fn_en}.{ln_en}".lower() + "@lab.local"
        n = 1
        while email in used_emails:
            n += 1
            email = f"{fn_en}.{ln_en}{n}".lower() + "@lab.local"
        used_emails.add(email)
        employees.append({
            "id": i, "employee_no": f"EMP-{i:03d}", "full_name": f"{fn_en} {ln_en}", "full_name_ar": f"{fn_ar} {ln_ar}",
            "department": dept_en, "department_ar": dept_ar, "title": random.choice(titles), "email": email,
            "hired_at": (TODAY - timedelta(days=random.randint(60, 2400))),
        })
    ds["employees"] = employees

    # attendance: 12 working days x 25 employees = 300 ---------------------------------------------
    work_days = []
    d = TODAY - timedelta(days=4)  # 2026-08-28 (Friday)
    while len(work_days) < 12:
        if d.weekday() < 5:
            work_days.append(d)
        d -= timedelta(days=1)
    work_days.reverse()
    attendance = []
    aid = 0
    for wd in work_days:
        for emp in employees:
            aid += 1
            status = random.choices(["present", "late", "absent", "leave"], weights=[74, 14, 6, 6])[0]
            check_in = check_out = None
            if status in ("present", "late"):
                start_min = random.randint(0, 25) if status == "present" else random.randint(31, 95)
                check_in = datetime(wd.year, wd.month, wd.day, 8, 30, tzinfo=timezone.utc) + timedelta(minutes=start_min)
                check_out = check_in + timedelta(hours=8, minutes=random.randint(-20, 75))
            attendance.append({"id": aid, "employee_id": emp["id"], "work_date": wd, "check_in": check_in,
                               "check_out": check_out, "status": status})
    ds["attendance"], ds["work_days"] = attendance, work_days

    # tickets -------------------------------------------------------------------------------------
    support_agents = [e for e in employees if e["department"] == "Support"] or employees[:3]
    tickets = []
    sla_hours = {"low": 72, "normal": 24, "high": 8, "urgent": 2}
    for i in range(1, N_TICKETS + 1):
        cust = random.choice(customers)
        cat = random.choice(list(TICKET_TEMPLATES))
        order = random.choice(orders)
        subject = random.choice(TICKET_TEMPLATES[cat]).format(order=order["order_number"])
        prio = random.choices(["low", "normal", "high", "urgent"], weights=[20, 50, 22, 8])[0]
        status = random.choices(["open", "triaged", "in_progress", "resolved", "closed"], weights=[22, 15, 18, 25, 20])[0]
        created_at = rand_dt(45, 0)
        body = random.choice(TICKET_BODIES).format(
            subject=subject, subject_lower=subject[0].lower() + subject[1:], day=created_at.strftime("%A"))
        resolved_at = created_at + timedelta(hours=random.randint(1, 120)) if status in ("resolved", "closed") else None
        assigned = random.choice(support_agents)["email"] if status != "open" else None
        tickets.append({
            "id": i, "ticket_no": f"TCK-{1000 + i}", "customer_id": cust["id"],
            "channel": random.choices(["email", "form", "chat", "telegram"], weights=[45, 30, 15, 10])[0],
            "subject": subject, "body": body, "category": cat, "priority": prio, "status": status,
            "assigned_to": assigned, "sla_due_at": created_at + timedelta(hours=sla_hours[prio]),
            "created_at": created_at, "resolved_at": resolved_at,
        })
    tickets.sort(key=lambda t: t["created_at"])
    for i, t in enumerate(tickets, 1):
        t["id"], t["ticket_no"] = i, f"TCK-{1000 + i}"
    ds["tickets"] = tickets

    # leads ---------------------------------------------------------------------------------------
    leads = []
    for i in range(1, N_LEADS + 1):
        name, email = make_person()
        co = random.choice(companies)
        status = random.choices(["new", "contacted", "qualified", "lost", "won"], weights=[35, 25, 18, 12, 10])[0]
        created_at = rand_dt(60, 0)
        enriched = None
        last_contacted = None
        if status != "new":
            enriched = {"domain": co["domain"], "industry": co["industry"], "size": co["size"],
                        "country": co["country"], "enriched_at": iso(created_at + timedelta(minutes=5))}
            last_contacted = created_at + timedelta(hours=random.randint(2, 200))
        leads.append({
            "id": i, "email": email, "name": name, "company": co["name"], "domain": co["domain"],
            "source": random.choice(LEAD_SOURCES), "score": random.randint(5, 98), "status": status,
            "enriched": enriched, "created_at": created_at, "last_contacted_at": last_contacted,
        })
    leads.sort(key=lambda l: l["created_at"])
    for i, l in enumerate(leads, 1):
        l["id"] = i
    ds["leads"] = leads

    # bookings: 8 starting ~24h from NOW, 6 later, 16 in the past ----------------------------------
    bookings = []
    for i in range(1, N_BOOKINGS + 1):
        cust = random.choice(customers)
        if i <= 8:
            starts = NOW + timedelta(hours=24) + timedelta(minutes=random.randint(-60, 60))
            status, reminder = "confirmed", None
        elif i <= 14:
            starts = NOW + timedelta(days=random.randint(2, 12), hours=random.randint(0, 8))
            status, reminder = "confirmed", None
        else:
            starts = rand_dt(40, 2)
            status = random.choices(["completed", "cancelled"], weights=[75, 25])[0]
            reminder = starts - timedelta(hours=24)
        starts = starts.replace(minute=(starts.minute // 15) * 15, second=0, microsecond=0)
        bookings.append({
            "id": i, "booking_ref": f"BK-{2600 + i}", "customer_id": cust["id"],
            "service": random.choice(BOOKING_SERVICES), "starts_at": starts,
            "ends_at": starts + timedelta(minutes=random.choice([30, 45, 60, 90])), "status": status,
            "reminder_sent_at": reminder, "created_at": min(starts - timedelta(days=random.randint(3, 20)), NOW),
        })
    ds["bookings"] = bookings

    # expenses: rows 1-3 mirror the three receipt images ------------------------------------------
    expenses = [
        {"merchant": "Copper Kettle Cafe", "amount": 23.10, "currency": "LYD",
         "spent_at": datetime(2026, 8, 19, 12, 42, tzinfo=timezone.utc), "category": "meals",
         "receipt_file": "receipt-01.png"},
        {"merchant": "Silverline Printing", "amount": 82.11, "currency": "EUR",
         "spent_at": datetime(2026, 8, 20, 16, 5, tzinfo=timezone.utc), "category": "office",
         "receipt_file": "receipt-02.png"},
        {"merchant": "Palm Restaurant (مطعم النخلة)", "amount": 28.50, "currency": "LYD",
         "spent_at": datetime(2026, 8, 22, 13, 20, tzinfo=timezone.utc), "category": "meals",
         "receipt_file": "receipt-ar-01.png"},
    ]
    while len(expenses) < N_EXPENSES:
        cat = random.choice(EXPENSE_CATEGORIES)
        expenses.append({
            "merchant": random.choice(MERCHANTS), "amount": money(random.uniform(4, 380)),
            "currency": random.choice(["LYD", "USD", "EUR"]), "spent_at": rand_dt(75, 1), "category": cat,
            "receipt_file": None,
        })
    for i, e in enumerate(expenses, 1):
        e.update({"id": i, "extracted_by": "seed", "raw_text": None, "created_at": e["spent_at"] + timedelta(hours=3)})
    ds["expenses"] = expenses

    # rate_history: USD -> EUR/GBP/LYD, 10 daily points each --------------------------------------
    rates = []
    rid = 0
    for quote in ("EUR", "GBP", "LYD"):
        for k in range(10):
            rid += 1
            fetched = (NOW - timedelta(days=10 - k)).replace(hour=6, minute=0, second=0)
            rates.append({"id": rid, "base": "USD", "quote": quote,
                          "rate": round(BASE_RATES[quote] * (1 + random.uniform(-0.02, 0.02)), 6), "fetched_at": fetched})
    ds["rate_history"] = rates

    ds["uptime_state"] = [
        {"target": t, "state": "up", "failures": 0, "since": NOW - timedelta(days=3), "last_alert_at": None}
        for t in ("mock-api", "n8n", "minio")
    ]
    ds["sync_state"] = [
        {"source": "mock-api-events", "cursor": "0", "last_run_at": None, "rows_seen": 0},
        {"source": "mock-api-orders", "cursor": "1970-01-01T00:00:00Z", "last_run_at": None, "rows_seen": 0},
    ]

    # mock-api extras --------------------------------------------------------------------------------
    mock_orders = []
    for o in orders[:N_MOCK_ORDERS]:
        cust = customers[o["customer_id"] - 1]
        mock_orders.append({
            "id": o["id"], "order_number": o["order_number"], "customer_external_id": cust["external_id"],
            "customer_email": cust["email"], "status": o["status"], "total": o["total"], "currency": o["currency"],
            "ordered_at": iso(o["ordered_at"]), "updated_at": iso(rand_dt(30, 0)),
        })
    ds["mock_orders"] = mock_orders

    events = []
    t = NOW - timedelta(days=20)
    for i in range(1, N_MOCK_EVENTS + 1):
        t += timedelta(minutes=random.randint(10, 190))
        typ = random.choices(["order.created", "order.paid", "ticket.opened"], weights=[45, 35, 20])[0]
        if typ.startswith("order"):
            o = random.choice(orders)
            data = {"order_number": o["order_number"], "customer_id": o["customer_id"], "total": o["total"],
                    "currency": o["currency"]}
        else:
            tk = random.choice(tickets)
            data = {"ticket_no": tk["ticket_no"], "customer_id": tk["customer_id"], "priority": tk["priority"],
                    "category": tk["category"]}
        events.append({"id": i, "type": typ, "created_at": iso(t), "data": data})
    ds["events"] = events

    posts = []
    for i, (title, tags) in enumerate(POST_TOPICS[:N_POSTS], 1):
        published = (NOW - timedelta(days=60) + timedelta(days=i * 2.3, hours=random.randint(0, 9))).replace(
            minute=0, second=0, microsecond=0)
        summary = (f"{title}. A short, practical note from the Automation Lab on {tags[0].replace('-', ' ')} "
                   f"and {tags[1].replace('-', ' ')}, written for small teams that run everything locally.")
        body = "\n\n".join([
            f"{title} is one of those topics that sounds bigger than it is. In practice it comes down to a handful of "
            f"decisions that a small team can make in an afternoon.",
            f"This post walks through the version we use in the lab: a local stack, synthetic data, and a workflow "
            f"that can be imported and run without any external account. The keywords to remember are "
            f"{', '.join(tags)}.",
            "If you only take one thing away: keep a test payload next to every workflow and run it after each change. "
            "Everything else follows from that habit.",
        ])
        posts.append({"id": i, "title": title, "slug": slug(title), "summary": summary, "excerpt": summary,
                      "body": body, "tags": tags, "author": random.choice(POST_AUTHORS), "published_at": iso(published)})
    ds["posts"] = posts

    # seed files data -------------------------------------------------------------------------------
    csv_customers = []
    for i in range(1, N_CSV_CUSTOMERS + 1):
        name, email = make_person()
        co = random.choice(companies)
        csv_customers.append({
            "external_id": f"CSV-{i:04d}", "name": name, "email": email, "phone": phone(co["country"]),
            "company": co["name"], "country": co["country"], "city": random.choice(COUNTRIES[co["country"]]),
            "segment": random.choice(["smb", "mid", "enterprise"]),
        })
    csv_customers[11]["email"] = csv_customers[11]["email"].replace("@", ".")   # row 12: bad e-mail (no @)
    csv_customers[26]["name"] = ""                                                # row 27: missing name
    csv_customers[50]["external_id"] = csv_customers[7]["external_id"]           # row 51: duplicate of row 8
    ds["csv_customers"] = csv_customers

    xlsx_orders, xlsx_items = [], []
    for i in range(1, N_XLSX_ORDERS + 1):
        cust = random.choice(customers)
        num = f"ORD-2026-{9000 + i}"
        ordered = rand_dt(20, 0)
        total = 0.0
        for prod in random.sample(products, random.choice([1, 2, 2, 3])):
            qty = random.choice([1, 1, 2, 3, 5])
            xlsx_items.append({"order_number": num, "sku": prod["sku"], "qty": qty, "unit_price": prod["price"]})
            total += qty * prod["price"]
        xlsx_orders.append({"order_number": num, "customer_external_id": cust["external_id"],
                            "status": random.choice(["pending", "paid", "shipped"]), "currency": "USD",
                            "total": money(total), "ordered_at": iso(ordered)})
    # two deliberately broken rows for D01's row-level error report
    xlsx_items.append({"order_number": xlsx_orders[4]["order_number"], "sku": "SKU-9999", "qty": 1, "unit_price": 10.0})
    xlsx_items.append({"order_number": xlsx_orders[9]["order_number"], "sku": "SKU-0002", "qty": -3, "unit_price": 10.0})
    ds["xlsx_orders"], ds["xlsx_items"] = xlsx_orders, xlsx_items

    ds["meta"] = {"seed": SEED, "now": iso(NOW)}
    return ds


# --- SQL -----------------------------------------------------------------------------------------
SCHEMA_TABLES = [
    # (name, body) -- order matters for FKs; DROP runs in reverse
    ("customers", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    external_id  text NOT NULL UNIQUE,
    name         text NOT NULL,
    email        text NOT NULL,
    phone        text,
    company      text,
    country      text,
    city         text,
    segment      text NOT NULL DEFAULT 'smb' CHECK (segment IN ('smb', 'mid', 'enterprise')),
    created_at   timestamptz NOT NULL DEFAULT now()"""),
    ("products", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    sku          text NOT NULL UNIQUE,
    name         text NOT NULL,
    category     text,
    price        numeric(10,2) NOT NULL CHECK (price >= 0),
    currency     char(3) NOT NULL DEFAULT 'USD',
    stock        integer NOT NULL DEFAULT 0,
    updated_at   timestamptz NOT NULL DEFAULT now()"""),
    ("orders", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_number text NOT NULL UNIQUE,
    customer_id  integer NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'paid', 'shipped', 'cancelled', 'refunded')),
    total        numeric(10,2) NOT NULL DEFAULT 0,
    currency     char(3) NOT NULL DEFAULT 'USD',
    ordered_at   timestamptz NOT NULL DEFAULT now(),
    shipped_at   timestamptz"""),
    ("order_items", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    order_id     integer NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id   integer NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
    qty          integer NOT NULL CHECK (qty > 0),
    unit_price   numeric(10,2) NOT NULL"""),
    ("employees", """
    id             integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    employee_no    text NOT NULL UNIQUE,
    full_name      text NOT NULL,
    full_name_ar   text,
    department     text NOT NULL,
    department_ar  text,
    title          text,
    email          text NOT NULL UNIQUE,
    hired_at       date NOT NULL"""),
    ("attendance", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    employee_id  integer NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    work_date    date NOT NULL,
    check_in     timestamptz,
    check_out    timestamptz,
    status       text NOT NULL CHECK (status IN ('present', 'late', 'absent', 'leave')),
    UNIQUE (employee_id, work_date)"""),
    ("tickets", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    ticket_no    text NOT NULL UNIQUE,
    customer_id  integer REFERENCES customers(id) ON DELETE SET NULL,
    channel      text NOT NULL CHECK (channel IN ('email', 'form', 'chat', 'telegram')),
    subject      text NOT NULL,
    body         text,
    category     text,
    priority     text NOT NULL DEFAULT 'normal' CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    status       text NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open', 'triaged', 'in_progress', 'resolved', 'closed')),
    assigned_to  text,
    sla_due_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz"""),
    ("leads", """
    id                 integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    email              text NOT NULL UNIQUE,
    name               text,
    company            text,
    domain             text,
    source             text,
    score              integer NOT NULL DEFAULT 0 CHECK (score BETWEEN 0 AND 100),
    status             text NOT NULL DEFAULT 'new'
                       CHECK (status IN ('new', 'contacted', 'qualified', 'lost', 'won')),
    enriched           jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    last_contacted_at  timestamptz"""),
    ("bookings", """
    id                integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    booking_ref       text NOT NULL UNIQUE,
    customer_id       integer REFERENCES customers(id) ON DELETE SET NULL,
    service           text NOT NULL,
    starts_at         timestamptz NOT NULL,
    ends_at           timestamptz NOT NULL,
    status            text NOT NULL DEFAULT 'confirmed' CHECK (status IN ('confirmed', 'cancelled', 'completed')),
    reminder_sent_at  timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CHECK (ends_at > starts_at)"""),
    ("expenses", """
    id            integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    merchant      text NOT NULL,
    amount        numeric(10,2) NOT NULL,
    currency      char(3) NOT NULL DEFAULT 'LYD',
    spent_at      timestamptz NOT NULL,
    category      text,
    receipt_file  text,
    extracted_by  text,
    raw_text      text,
    created_at    timestamptz NOT NULL DEFAULT now()"""),
    ("rate_history", """
    id          integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    base        char(3) NOT NULL,
    quote       char(3) NOT NULL,
    rate        numeric(14,6) NOT NULL,
    fetched_at  timestamptz NOT NULL DEFAULT now()"""),
    ("uptime_checks", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    target       text NOT NULL,
    url          text NOT NULL,
    status_code  integer,
    latency_ms   integer,
    ok           boolean NOT NULL,
    checked_at   timestamptz NOT NULL DEFAULT now()"""),
    ("uptime_state", """
    target         text PRIMARY KEY,
    state          text NOT NULL DEFAULT 'up' CHECK (state IN ('up', 'warn', 'down')),
    failures       integer NOT NULL DEFAULT 0,
    since          timestamptz NOT NULL DEFAULT now(),
    last_alert_at  timestamptz"""),
    ("webhook_events", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    external_id  text NOT NULL UNIQUE,
    source       text NOT NULL,
    event_type   text,
    payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
    received_at  timestamptz NOT NULL DEFAULT now()"""),
    ("sync_state", """
    source       text PRIMARY KEY,
    cursor       text,
    last_run_at  timestamptz,
    rows_seen    integer NOT NULL DEFAULT 0"""),
    ("products_mirror", """
    id            integer PRIMARY KEY,
    sku           text NOT NULL,
    name          text,
    price         numeric(10,2),
    stock         integer,
    content_hash  text,
    synced_at     timestamptz NOT NULL DEFAULT now()"""),
    ("execution_log", """
    id             integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    execution_id   text,
    workflow_id    text,
    workflow_name  text,
    status         text NOT NULL,
    started_at     timestamptz,
    finished_at    timestamptz,
    duration_ms    integer,
    error_message  text,
    error_node     text,
    logged_at      timestamptz NOT NULL DEFAULT now()"""),
    ("notifications", """
    id        integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    channel   text NOT NULL,
    target    text,
    subject   text,
    body      text,
    severity  text NOT NULL DEFAULT 'info' CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    sent_at   timestamptz NOT NULL DEFAULT now()"""),
    ("content_variants", """
    id            integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source_title  text NOT NULL,
    platform      text NOT NULL,
    content       text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()"""),
    ("tasks", """
    id          integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    source      text,
    title       text NOT NULL,
    owner       text,
    due_date    date,
    done        boolean NOT NULL DEFAULT false,
    created_at  timestamptz NOT NULL DEFAULT now()"""),
    ("documents", """
    id           integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    kind         text NOT NULL,
    file_name    text NOT NULL,
    storage_key  text,
    meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()"""),
]
SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_customers_email ON customers (email);",
    "CREATE INDEX IF NOT EXISTS idx_customers_segment ON customers (segment);",
    "CREATE INDEX IF NOT EXISTS idx_products_category ON products (category);",
    "CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders (customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);",
    "CREATE INDEX IF NOT EXISTS idx_orders_ordered_at ON orders (ordered_at);",
    "CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items (order_id);",
    "CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items (product_id);",
    "CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance (work_date);",
    "CREATE INDEX IF NOT EXISTS idx_tickets_customer ON tickets (customer_id);",
    "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets (status);",
    "CREATE INDEX IF NOT EXISTS idx_tickets_created_at ON tickets (created_at);",
    "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads (status);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_starts_at ON bookings (starts_at);",
    "CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings (status);",
    "CREATE INDEX IF NOT EXISTS idx_expenses_spent_at ON expenses (spent_at);",
    "CREATE INDEX IF NOT EXISTS idx_rate_history_pair ON rate_history (base, quote, fetched_at);",
    "CREATE INDEX IF NOT EXISTS idx_uptime_checks_target ON uptime_checks (target, checked_at);",
    "CREATE INDEX IF NOT EXISTS idx_webhook_events_source ON webhook_events (source, received_at);",
    "CREATE INDEX IF NOT EXISTS idx_execution_log_workflow ON execution_log (workflow_id, started_at);",
    "CREATE INDEX IF NOT EXISTS idx_execution_log_status ON execution_log (status, logged_at);",
    "CREATE INDEX IF NOT EXISTS idx_notifications_sent_at ON notifications (sent_at);",
    "CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks (done, due_date);",
    "CREATE INDEX IF NOT EXISTS idx_documents_kind ON documents (kind, created_at);",
]

# insert column lists (order in the VALUES tuples)
SEED_COLUMNS: dict[str, list[str]] = {
    "customers": ["id", "external_id", "name", "email", "phone", "company", "country", "city", "segment", "created_at"],
    "products": ["id", "sku", "name", "category", "price", "currency", "stock", "updated_at"],
    "orders": ["id", "order_number", "customer_id", "status", "total", "currency", "ordered_at", "shipped_at"],
    "order_items": ["id", "order_id", "product_id", "qty", "unit_price"],
    "employees": ["id", "employee_no", "full_name", "full_name_ar", "department", "department_ar", "title", "email",
                  "hired_at"],
    "attendance": ["id", "employee_id", "work_date", "check_in", "check_out", "status"],
    "tickets": ["id", "ticket_no", "customer_id", "channel", "subject", "body", "category", "priority", "status",
                "assigned_to", "sla_due_at", "created_at", "resolved_at"],
    "leads": ["id", "email", "name", "company", "domain", "source", "score", "status", "enriched", "created_at",
              "last_contacted_at"],
    "bookings": ["id", "booking_ref", "customer_id", "service", "starts_at", "ends_at", "status", "reminder_sent_at",
                 "created_at"],
    "expenses": ["id", "merchant", "amount", "currency", "spent_at", "category", "receipt_file", "extracted_by",
                 "raw_text", "created_at"],
    "rate_history": ["id", "base", "quote", "rate", "fetched_at"],
    "uptime_state": ["target", "state", "failures", "since", "last_alert_at"],
    "sync_state": ["source", "cursor", "last_run_at", "rows_seen"],
}
SERIAL_TABLES = ["customers", "products", "orders", "order_items", "employees", "attendance", "tickets", "leads",
                 "bookings", "expenses", "rate_history"]


def header(what: str) -> str:
    return (f"-- {what}\n-- GENERATED by seed/generate_seed.py (seed {SEED}, fixed now {iso(NOW)}). Do not edit by hand:\n"
            f"-- change the generator and run `python seed/generate_seed.py`. All data is synthetic.\n")


def render_schema() -> str:
    out = [header("Automation Lab demo database schema (database `demo`)"),
           "-- Re-runnable: drops every demo table first, then creates them.\n"]
    for name, _ in reversed(SCHEMA_TABLES):
        out.append(f"DROP TABLE IF EXISTS {name} CASCADE;")
    out.append("")
    for name, body in SCHEMA_TABLES:
        out.append(f"CREATE TABLE IF NOT EXISTS {name} ({body}\n);\n")
    out.extend(SCHEMA_INDEXES)
    out.append("")
    return "\n".join(out)


def render_seed(ds: dict[str, Any]) -> str:
    out = [header("Automation Lab demo data (loaded after schema.sql)"), "BEGIN;", ""]
    for table, cols in SEED_COLUMNS.items():
        rows = ds[table]
        out.append(f"-- {table}: {len(rows)} rows")
        out.append(f"INSERT INTO {table} ({', '.join(cols)}) VALUES")
        lines = ["  (" + ", ".join(q(r[c]) for c in cols) + ")" for r in rows]
        out.append(",\n".join(lines) + ";")
        out.append("")
    out.append("-- reset identity sequences so new inserts do not collide with seeded ids (DO block keeps psql quiet)")
    out.append("DO $$ BEGIN")
    for table in SERIAL_TABLES:
        out.append(f"  PERFORM setval(pg_get_serial_sequence('{table}', 'id'), (SELECT max(id) FROM {table}));")
    out.append("END $$;")
    out.append("")
    out.append("COMMIT;")
    out.append("")
    return "\n".join(out)


# --- seed files (text) --------------------------------------------------------------------------
def render_customers_csv(ds: dict[str, Any]) -> str:
    buf = io.StringIO()
    cols = ["external_id", "name", "email", "phone", "company", "country", "city", "segment"]
    w = csv.DictWriter(buf, fieldnames=cols, lineterminator="\n")
    w.writeheader()
    for r in ds["csv_customers"]:
        w.writerow({c: r[c] for c in cols})
    return buf.getvalue()


def render_attendance_csv(ds: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["employee_no", "full_name", "department", "work_date", "check_in", "check_out", "status"])
    week = ds["work_days"][-5:]
    emp = {e["id"]: e for e in ds["employees"]}
    for a in ds["attendance"]:
        if a["work_date"] in week:
            e = emp[a["employee_id"]]
            w.writerow([e["employee_no"], e["full_name"], e["department"], a["work_date"].isoformat(),
                        a["check_in"].strftime("%H:%M") if a["check_in"] else "",
                        a["check_out"].strftime("%H:%M") if a["check_out"] else "", a["status"]])
    return buf.getvalue()


# --- payloads -----------------------------------------------------------------------------------
def build_payloads(ds: dict[str, Any]) -> dict[str, Any]:
    cust = ds["customers"][6]
    prods = ds["products"]
    order_items = [
        {"sku": prods[2]["sku"], "name": prods[2]["name"], "qty": 2, "unit_price": prods[2]["price"]},
        {"sku": prods[17]["sku"], "name": prods[17]["name"], "qty": 1, "unit_price": prods[17]["price"]},
    ]
    total = money(sum(i["qty"] * i["unit_price"] for i in order_items))
    t01_order = {
        "external_id": "evt-order-20260901-0001",
        "order_number": "ORD-2026-9101",
        "source": "webshop",
        "customer": {"external_id": cust["external_id"], "email": cust["email"], "name": cust["name"]},
        "currency": "USD",
        "items": order_items,
        "total": total,
        "ordered_at": "2026-09-01T08:55:00Z",
    }
    t01_invalid = {
        "order_number": 12345,
        "customer": {"email": "not-an-email"},
        "items": [],
        "total": "twenty",
        "ordered_at": "yesterday",
    }
    p03_replay = dict(t01_order)
    p03_replay["replay"] = True
    p03_replay["_note"] = "Same external_id as t01-order.json: the second delivery must be acknowledged but not re-processed."

    repo = {"id": 100001, "name": "demo-repo", "full_name": "lab-org/demo-repo", "private": False,
            "html_url": "https://github.com/lab-org/demo-repo", "default_branch": "main",
            "owner": {"login": "lab-org", "type": "Organization"}}
    sender = {"login": "nour-haddad", "type": "User"}
    github_push = {
        "ref": "refs/heads/main",
        "before": "1f9c2a7d4e3b",
        "after": "8b4d0e6c2a91",
        "created": False, "deleted": False, "forced": False,
        "compare": "https://github.com/lab-org/demo-repo/compare/1f9c2a7d4e3b...8b4d0e6c2a91",
        "repository": repo,
        "pusher": {"name": "nour-haddad", "email": "nour.haddad@lab.local"},
        "sender": sender,
        "commits": [
            {"id": "5a71c3e9d0f2", "message": "feat(T01): validate order payload before insert",
             "timestamp": "2026-09-01T08:41:12Z",
             "author": {"name": "Nour Haddad", "email": "nour.haddad@lab.local", "username": "nour-haddad"},
             "added": ["workflows/T01-webhook-to-database/test/payload.json"],
             "modified": ["workflows/T01-webhook-to-database/workflow.json"], "removed": []},
            {"id": "8b4d0e6c2a91", "message": "docs: note the 400 response on bad schema",
             "timestamp": "2026-09-01T08:52:40Z",
             "author": {"name": "Nour Haddad", "email": "nour.haddad@lab.local", "username": "nour-haddad"},
             "added": [], "modified": ["workflows/T01-webhook-to-database/README.md"], "removed": []},
        ],
        "head_commit": {"id": "8b4d0e6c2a91", "message": "docs: note the 400 response on bad schema",
                        "timestamp": "2026-09-01T08:52:40Z",
                        "author": {"name": "Nour Haddad", "email": "nour.haddad@lab.local", "username": "nour-haddad"}},
    }
    github_issue = {
        "action": "opened",
        "issue": {
            "number": 42, "title": "Webhook returns 500 when items array is empty",
            "body": "Steps to reproduce: POST t01-invalid.json to /webhook/orders. Expected 400, got 500.",
            "state": "open", "labels": [{"name": "bug"}, {"name": "T01"}],
            "user": {"login": "karim-rossi"}, "html_url": "https://github.com/lab-org/demo-repo/issues/42",
            "created_at": "2026-09-01T08:30:00Z",
        },
        "repository": repo, "sender": {"login": "karim-rossi", "type": "User"},
    }
    m02_release = {
        "action": "published",
        "release": {
            "id": 7001, "tag_name": "v1.4.0", "name": "Automation Lab 1.4.0", "draft": False, "prerelease": False,
            "body": "## Highlights\n- T03 polling now stores the cursor in Redis\n- P02 retry with jittered backoff\n"
                    "- Arabic report template for R03",
            "html_url": "https://github.com/lab-org/demo-repo/releases/tag/v1.4.0",
            "published_at": "2026-09-01T09:00:00Z", "author": {"login": "nour-haddad"},
        },
        "repository": repo, "sender": sender,
    }
    lead_co = ds["companies"][0]
    b01_lead = {
        "email": "rania.khalil@lab.local", "name": "Rania Khalil", "company": lead_co["name"],
        "company_domain": lead_co["domain"], "source": "website", "phone": "+218 91 555 0142",
        "message": "We run a small retail team and want to automate order confirmations and weekly reports.",
        "submitted_at": "2026-09-01T08:58:00Z",
    }
    bcust = ds["customers"][12]
    b02_booking = {
        "booking_ref": "BK-2701", "customer_external_id": bcust["external_id"], "customer_email": bcust["email"],
        "customer_name": bcust["name"], "service": "Product demo",
        "starts_at": iso(NOW + timedelta(hours=24)), "ends_at": iso(NOW + timedelta(hours=25)),
        "timezone": "Africa/Tripoli", "notes": "Prefers a call in Arabic. Two attendees.",
    }
    return {
        "t01-order.json": t01_order, "t01-invalid.json": t01_invalid, "github-push.json": github_push,
        "github-issue.json": github_issue, "b01-lead.json": b01_lead, "b02-booking.json": b02_booking,
        "m02-release.json": m02_release, "p03-replay.json": p03_replay,
    }


def build_mock_db(ds: dict[str, Any]) -> dict[str, Any]:
    customers = [{
        "id": c["id"], "external_id": c["external_id"], "name": c["name"], "email": c["email"], "phone": c["phone"],
        "company": c["company"], "country": c["country"], "city": c["city"], "segment": c["segment"],
        "created_at": iso(c["created_at"]),
    } for c in ds["customers"]]
    products = [{**p, "updated_at": iso(p["updated_at"])} for p in ds["products"]]
    return {"products": products, "customers": customers, "orders": ds["mock_orders"], "events": ds["events"],
            "companies": ds["companies"], "posts": ds["posts"]}


def dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


# --- binary seed files -----------------------------------------------------------------------------
class Optional:
    """Lazy optional imports, all disabled with --no-optional-deps."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.notes: list[str] = []

    def mod(self, name: str):
        if not self.enabled:
            return None
        try:
            return __import__(name)
        except Exception:  # noqa: BLE001 - any import problem means "use the fallback"
            return None


def _rezip_fixed_time(data: bytes, path: Path) -> None:
    """Rewrite a zip (xlsx) with a constant entry timestamp so regenerating gives identical bytes."""
    stamp = (2026, 9, 1, 9, 0, 0)
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            blob = src.read(info.filename)
            if info.filename == "docProps/core.xml":  # openpyxl stamps save time here
                text = blob.decode("utf-8")
                text = re.sub(r"(<dcterms:(created|modified)[^>]*>)[^<]*(?=</dcterms:)", lambda m: m.group(1) + "2026-09-01T09:00:00Z", text)
                blob = text.encode("utf-8")
            dst.writestr(zi, blob)


def write_xlsx(path: Path, ds: dict[str, Any], opt: Optional,
               sheets: list[tuple[str, list[str], list[dict[str, Any]]]] | None = None) -> str:
    orders_cols = ["order_number", "customer_external_id", "status", "currency", "total", "ordered_at"]
    items_cols = ["order_number", "sku", "qty", "unit_price"]
    if sheets is None:
        sheets = [("Orders", orders_cols, ds["xlsx_orders"]), ("Items", items_cols, ds["xlsx_items"])]
    if opt.mod("openpyxl"):
        from openpyxl import Workbook  # type: ignore
        wb = Workbook()
        wb.properties.creator = "seed/generate_seed.py"
        wb.properties.created = wb.properties.modified = NOW.replace(tzinfo=None)
        first = True
        for title, cols, rows in sheets:
            ws = wb.active if first else wb.create_sheet()
            first = False
            ws.title = title
            ws.append(cols)
            for r in rows:
                ws.append([r[c] for c in cols])
        buf = io.BytesIO()
        wb.save(buf)
        _rezip_fixed_time(buf.getvalue(), path)
        return "openpyxl"
    # Fallback: minimal OOXML with inline strings - readable by every spreadsheet parser.
    def col_letter(i: int) -> str:
        s = ""
        i += 1
        while i:
            i, rem = divmod(i - 1, 26)
            s = chr(65 + rem) + s
        return s

    def xml_escape(s: Any) -> str:
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def sheet_xml(cols: list[str], rows: list[dict[str, Any]]) -> str:
        parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                 '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
        all_rows = [cols] + [[r[c] for c in cols] for r in rows]
        for ri, row in enumerate(all_rows, 1):
            cells = []
            for ci, v in enumerate(row):
                ref = f"{col_letter(ci)}{ri}"
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    cells.append(f'<c r="{ref}"><v>{v}</v></c>')
                else:
                    cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(v)}</t></is></c>')
            parts.append(f'<row r="{ri}">{"".join(cells)}</row>')
        parts.append("</sheetData></worksheet>")
        return "".join(parts)

    content_types = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                     + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                               for i in range(1, len(sheets) + 1))
                     + "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>")
    workbook = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                + "".join(f'<sheet name="{t}" sheetId="{i}" r:id="rId{i}"/>' for i, (t, _, _) in enumerate(sheets, 1))
                + "</sheets></workbook>")
    wb_rels = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
               + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                         for i in range(1, len(sheets) + 1))
               + "</Relationships>")
    stamp = (2026, 9, 1, 9, 0, 0)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        def add(name: str, data: str) -> None:
            zi = zipfile.ZipInfo(name, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data.encode("utf-8"))
        add("[Content_Types].xml", content_types)
        add("_rels/.rels", rels)
        add("xl/workbook.xml", workbook)
        add("xl/_rels/workbook.xml.rels", wb_rels)
        for i, (_, cols, rows) in enumerate(sheets, 1):
            add(f"xl/worksheets/sheet{i}.xml", sheet_xml(cols, rows))
    return "stdlib zipfile fallback"


INVOICE = {
    "number": "INV-2026-0142", "issued": "2026-08-25", "due": "2026-09-24",
    "seller": ("Cedar Software", "12 Nile Corniche, Cairo", "billing@lab.local"),
    "buyer": ("Acme Trading", "Gargaresh Road, Tripoli", "accounts@lab.local"),
    "rows": [
        ("Workflow automation setup", 1, 1200.00), ("Monthly support retainer (Aug)", 1, 450.00),
        ("Custom PDF report template", 2, 180.00), ("Arabic OCR calibration", 3, 95.00),
        ("Training session (remote)", 2, 150.00), ("Server hosting (3 months)", 3, 40.00),
    ],
    "vat_rate": 0.14,
}


def invoice_totals() -> tuple[float, float, float]:
    sub = money(sum(qty * price for _, qty, price in INVOICE["rows"]))
    vat = money(sub * INVOICE["vat_rate"])
    return sub, vat, money(sub + vat)


def write_pdf(path: Path, opt: Optional) -> str:
    sub, vat, total = invoice_totals()
    table = [["Description", "Qty", "Unit price", "Amount"]] + [
        [d, str(qty), f"{p:.2f}", f"{qty * p:.2f}"] for d, qty, p in INVOICE["rows"]]
    if opt.mod("reportlab"):
        from reportlab.lib import colors  # type: ignore
        from reportlab.lib.pagesizes import A4  # type: ignore
        from reportlab.lib.units import mm  # type: ignore
        from reportlab.pdfgen import canvas  # type: ignore
        from reportlab.platypus import Table, TableStyle  # type: ignore

        c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
        c.setTitle(f"Invoice {INVOICE['number']}")
        c.setAuthor(INVOICE["seller"][0])
        w, h = A4
        y = h - 25 * mm
        c.setFont("Helvetica-Bold", 20)
        c.drawString(20 * mm, y, "INVOICE")
        c.setFont("Helvetica", 11)
        c.drawRightString(w - 20 * mm, y, f"Invoice number: {INVOICE['number']}")
        y -= 7 * mm
        c.drawRightString(w - 20 * mm, y, f"Issue date: {INVOICE['issued']}")
        y -= 6 * mm
        c.drawRightString(w - 20 * mm, y, f"Due date: {INVOICE['due']}")
        y -= 10 * mm
        c.setFont("Helvetica-Bold", 11)
        c.drawString(20 * mm, y, "From")
        c.drawString(105 * mm, y, "Bill to")
        c.setFont("Helvetica", 10)
        for k, (s, b) in enumerate(zip(INVOICE["seller"], INVOICE["buyer"])):
            c.drawString(20 * mm, y - (k + 1) * 5 * mm, s)
            c.drawString(105 * mm, y - (k + 1) * 5 * mm, b)
        y -= 30 * mm
        t = Table(table + [["", "", "Subtotal", f"{sub:.2f}"], ["", "", f"VAT {int(INVOICE['vat_rate'] * 100)}%", f"{vat:.2f}"],
                           ["", "", "Total (USD)", f"{total:.2f}"]],
                  colWidths=[95 * mm, 20 * mm, 27 * mm, 28 * mm])
        n = len(table)
        t.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 10), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, n - 1), 0.5, colors.black), ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("LINEABOVE", (2, n), (-1, n), 0.5, colors.black), ("FONTNAME", (2, n + 2), (-1, n + 2), "Helvetica-Bold"),
        ]))
        tw, th = t.wrapOn(c, w, h)
        t.drawOn(c, 20 * mm, y - th)
        y -= th + 15 * mm
        c.setFont("Helvetica", 9)
        c.drawString(20 * mm, y, "Payment terms: 30 days. Bank transfer reference: the invoice number.")
        c.drawString(20 * mm, y - 5 * mm, "This document is synthetic demo data generated by seed/generate_seed.py.")
        c.showPage()
        c.save()
        return "reportlab"
    # Fallback: hand-built single-page PDF with real text objects (Helvetica), monospace-ish table via columns.
    lines: list[tuple[float, float, str, str, int]] = []  # (x, y, text, font, size)
    y = 800.0
    lines.append((60, y, "INVOICE", "F2", 20)); lines.append((360, y, f"Invoice number: {INVOICE['number']}", "F1", 11))
    y -= 20; lines.append((360, y, f"Issue date: {INVOICE['issued']}", "F1", 11))
    y -= 16; lines.append((360, y, f"Due date: {INVOICE['due']}", "F1", 11))
    y -= 30; lines.append((60, y, "From", "F2", 11)); lines.append((300, y, "Bill to", "F2", 11))
    for k, (s, b) in enumerate(zip(INVOICE["seller"], INVOICE["buyer"])):
        lines.append((60, y - (k + 1) * 14, s, "F1", 10)); lines.append((300, y - (k + 1) * 14, b, "F1", 10))
    y -= 80
    xs = [60, 330, 390, 470]
    for ri, row in enumerate(table):
        for x, cell in zip(xs, row):
            lines.append((x, y, cell, "F2" if ri == 0 else "F1", 10))
        y -= 16
    for label, val in (("Subtotal", sub), (f"VAT {int(INVOICE['vat_rate'] * 100)}%", vat), ("Total (USD)", total)):
        lines.append((390, y, label, "F2", 10)); lines.append((470, y, f"{val:.2f}", "F2", 10)); y -= 16
    y -= 20
    lines.append((60, y, "Payment terms: 30 days. Bank transfer reference: the invoice number.", "F1", 9))
    lines.append((60, y - 14, "This document is synthetic demo data generated by seed/generate_seed.py.", "F1", 9))

    def pdf_str(s: str) -> str:
        return s.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "\n".join(f"BT /{f} {sz} Tf {x:.1f} {yy:.1f} Td ({pdf_str(t)}) Tj ET" for x, yy, t, f, sz in lines)
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
        f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1"))
    path.write_bytes(out.getvalue())
    return "stdlib fallback"


RECEIPTS = {
    "receipt-01.png": {
        "title": "COPPER KETTLE CAFE", "sub": ["Gargaresh Road, Tripoli", "Receipt R-10231", "Date 2026-08-19 12:42"],
        "items": [("Cappuccino x2", "7.00"), ("Club Sandwich", "12.50"), ("Mineral Water", "1.50")],
        "totals": [("Subtotal", "21.00"), ("Service 10%", "2.10"), ("TOTAL LYD", "23.10")],
        "footer": ["Paid by card", "Thank you for your visit"],
    },
    "receipt-02.png": {
        "title": "SILVERLINE PRINTING", "sub": ["Hafenstrasse 4, Hamburg", "Receipt 2026-08-20-0077", "Date 2026-08-20 16:05"],
        "items": [("Business cards (250)", "35.00"), ("A4 flyers x100", "28.00"), ("Lamination", "6.00")],
        "totals": [("Subtotal", "69.00"), ("VAT 19%", "13.11"), ("TOTAL EUR", "82.11")],
        "footer": ["Paid in cash", "Keep this receipt for returns"],
    },
}
RECEIPT_AR = {
    "title": "مطعم النخلة", "sub": ["طرابلس - شارع الجمهورية", "رقم الإيصال 4471", "التاريخ 2026-08-22"],
    "items": [("شاورما دجاج x2", "18.00"), ("عصير برتقال", "4.50"), ("سلطة خضراء", "6.00")],
    "totals": [("المجموع", "28.50"), ("الضريبة", "0.00"), ("الإجمالي د.ل", "28.50")],
    "footer": ["الدفع نقداً", "شكراً لزيارتكم"],
}


def _latin_font(ImageFont, size: int):
    """Prefer Pillow's bundled scalable default (same on every machine), then DejaVu/Consolas, then bitmap."""
    try:
        return ImageFont.load_default(size=size), "pillow-default"
    except TypeError:
        pass
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                 "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Menlo.ttc"):
        if Path(cand).exists():
            return ImageFont.truetype(cand, size), Path(cand).name
    return ImageFont.load_default(), "pillow-bitmap"


def write_receipts(files_dir: Path, opt: Optional) -> str:
    pil = opt.mod("PIL")
    if not pil:
        for name in list(RECEIPTS) + ["receipt-ar-01.png"]:
            (files_dir / f"{name}.missing.txt").write_text(
                "Pillow is not installed, so this receipt image was not generated.\n"
                "Run: python -m pip install pillow && python seed/generate_seed.py --only files\n", encoding="utf-8")
        return "skipped (Pillow missing)"
    from PIL import Image, ImageDraw, ImageFont  # type: ignore

    W, PAD, LH = 720, 40, 46
    font, font_name = _latin_font(ImageFont, 30)
    big, _ = _latin_font(ImageFont, 40)
    for name, r in RECEIPTS.items():
        n_lines = 1 + len(r["sub"]) + 1 + len(r["items"]) + 1 + len(r["totals"]) + 1 + len(r["footer"]) + 1
        img = Image.new("RGB", (W, PAD * 2 + n_lines * LH + 30), "white")
        d = ImageDraw.Draw(img)
        y = PAD
        d.text((W // 2, y), r["title"], fill="black", font=big, anchor="ma"); y += LH + 10
        for s in r["sub"]:
            d.text((W // 2, y), s, fill="black", font=font, anchor="ma"); y += LH
        d.line((PAD, y + 8, W - PAD, y + 8), fill="black", width=2); y += LH // 2 + 8
        for label, amt in r["items"]:
            d.text((PAD, y), label, fill="black", font=font); d.text((W - PAD, y), amt, fill="black", font=font, anchor="ra")
            y += LH
        d.line((PAD, y + 8, W - PAD, y + 8), fill="black", width=2); y += LH // 2 + 8
        for label, amt in r["totals"]:
            f = big if label.startswith("TOTAL") else font
            d.text((PAD, y), label, fill="black", font=f); d.text((W - PAD, y), amt, fill="black", font=f, anchor="ra")
            y += LH
        y += LH // 2
        for s in r["footer"]:
            d.text((W // 2, y), s, fill="black", font=font, anchor="ma"); y += LH
        img.crop((0, 0, W, y + PAD)).save(files_dir / name, optimize=True)

    # Arabic receipt with Amiri. If Pillow was built with libraqm it shapes + reorders Arabic itself (pass the raw
    # string with direction="rtl"); otherwise pre-shape with arabic_reshaper + python-bidi; otherwise draw unshaped.
    from PIL import features  # type: ignore
    raqm = bool(features.check("raqm"))
    reshaper, bidi = (None, None) if raqm else (opt.mod("arabic_reshaper"), None)
    if reshaper:
        try:
            from bidi.algorithm import get_display  # type: ignore
            bidi = get_display
        except Exception:  # noqa: BLE001
            reshaper = None
    if raqm:
        shaped_note = "shaped by libraqm"
    elif reshaper and bidi:
        shaped_note = "shaped by arabic-reshaper+python-bidi"
    else:
        shaped_note = "UNSHAPED (no libraqm; install arabic-reshaper python-bidi for correct glyph joining)"
    ar_kw: dict[str, Any] = {"direction": "rtl", "language": "ar"} if raqm else {}

    def shape(s: str) -> str:
        if reshaper and bidi:
            return bidi(reshaper.reshape(s))
        return s

    if AMIRI_FONT.exists():
        ar_font, ar_big = ImageFont.truetype(str(AMIRI_FONT), 40), ImageFont.truetype(str(AMIRI_FONT), 54)
        ar_name = AMIRI_FONT.name
    else:
        ar_font, ar_big, ar_name = font, big, font_name + " (Amiri missing)"
    r = RECEIPT_AR
    LH2 = 64
    n_lines = 1 + len(r["sub"]) + 1 + len(r["items"]) + 1 + len(r["totals"]) + 1 + len(r["footer"]) + 1
    img = Image.new("RGB", (W, PAD * 2 + n_lines * LH2 + 30), "white")
    d = ImageDraw.Draw(img)
    y = PAD
    d.text((W // 2, y), shape(r["title"]), fill="black", font=ar_big, anchor="ma", **ar_kw); y += LH2 + 16
    for s in r["sub"]:
        d.text((W // 2, y), shape(s), fill="black", font=ar_font, anchor="ma", **ar_kw); y += LH2
    d.line((PAD, y + 8, W - PAD, y + 8), fill="black", width=2); y += LH2 // 2 + 8
    for label, amt in r["items"]:
        d.text((W - PAD, y), shape(label), fill="black", font=ar_font, anchor="ra", **ar_kw)
        d.text((PAD, y), amt, fill="black", font=font); y += LH2
    d.line((PAD, y + 8, W - PAD, y + 8), fill="black", width=2); y += LH2 // 2 + 8
    for label, amt in r["totals"]:
        f = ar_big if label.startswith("الإجمالي") else ar_font
        d.text((W - PAD, y), shape(label), fill="black", font=f, anchor="ra", **ar_kw)
        d.text((PAD, y), amt, fill="black", font=big if f is ar_big else font); y += LH2
    y += LH2 // 2
    for s in r["footer"]:
        d.text((W // 2, y), shape(s), fill="black", font=ar_font, anchor="ma", **ar_kw); y += LH2
    img.crop((0, 0, W, y + PAD)).save(files_dir / "receipt-ar-01.png", optimize=True)
    return f"Pillow ({font_name}; Arabic: {ar_name}, {shaped_note})"


def write_wav(path: Path) -> str:
    """~30 s of speech-like synthetic audio (voiced syllables with pitch contour, unvoiced bursts, word and sentence
    pauses). It is NOT real speech: transcription workflows only need a valid, plausibly-sized 16 kHz mono WAV."""
    rng = random.Random(SEED + 1)
    sr, total_s = 16000, 30.0
    samples = array("h")
    two_pi = 2 * math.pi
    t_pos = 0.0

    def silence(sec: float) -> None:
        samples.extend([0] * int(sec * sr))

    def syllable(f0_start: float, f0_end: float, dur: float, amp: float) -> None:
        n = int(dur * sr)
        attack, release = int(0.02 * sr), int(0.045 * sr)
        # formant-like emphasis around 600 Hz and 1500 Hz
        phases = [0.0] * 9
        for i in range(n):
            f0 = f0_start + (f0_end - f0_start) * i / max(n - 1, 1)
            env = 1.0
            if i < attack:
                env = i / attack
            elif i > n - release:
                env = max(0.0, (n - i) / release)
            v = 0.0
            for h in range(1, 9):
                fh = f0 * h
                w = (1.0 / h) * (1 + 2.2 * math.exp(-((fh - 600) / 220) ** 2) + 1.3 * math.exp(-((fh - 1500) / 320) ** 2))
                phases[h] += two_pi * fh / sr
                v += w * math.sin(phases[h])
            v = v / 4.2 * env * amp
            samples.append(int(max(-1.0, min(1.0, v)) * 32767))

    def burst(dur: float, amp: float) -> None:
        n = int(dur * sr)
        prev = 0.0
        for i in range(n):
            env = math.sin(math.pi * i / n)
            prev = 0.6 * prev + rng.uniform(-1, 1)  # slightly coloured noise
            samples.append(int(max(-1.0, min(1.0, prev * 0.35 * env * amp)) * 32767))

    silence(0.4)
    while len(samples) / sr < total_s - 0.6:
        base_f0 = rng.uniform(105, 175)
        n_words = rng.randint(4, 9)
        for wi in range(n_words):
            decl = 1.0 - 0.18 * wi / n_words  # pitch declination over the sentence
            for _ in range(rng.randint(1, 4)):
                if len(samples) / sr >= total_s - 0.6:
                    break
                if rng.random() < 0.35:
                    burst(rng.uniform(0.03, 0.07), rng.uniform(0.5, 0.9))
                f0 = base_f0 * decl * rng.uniform(0.92, 1.12)
                syllable(f0, f0 * rng.uniform(0.85, 1.15), rng.uniform(0.09, 0.2), rng.uniform(0.5, 0.85))
                silence(rng.uniform(0.01, 0.03))
            silence(rng.uniform(0.07, 0.16))
        silence(rng.uniform(0.35, 0.7))
    silence(max(0.0, total_s - len(samples) / sr))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())
    return f"{len(samples) / sr:.1f} s, 16 kHz mono 16-bit"


# --- orchestration --------------------------------------------------------------------------------
def text_outputs(ds: dict[str, Any]) -> dict[str, str]:
    """Every deterministic text artifact, keyed by repo-relative path."""
    out = {
        "seed/schema.sql": render_schema(),
        "seed/seed.sql": render_seed(ds),
        "seed/files/customers.csv": render_customers_csv(ds),
        "seed/files/attendance-week.csv": render_attendance_csv(ds),
        "seed/files/article.md": ARTICLE_MD,
        "docker/mock-api/db.json": dump_json(build_mock_db(ds)),
    }
    for name, payload in build_payloads(ds).items():
        out[f"seed/payloads/{name}"] = dump_json(payload)
    return out


def select(path: str, only: set[str]) -> bool:
    if path.startswith("docker/mock-api/"):
        return "mock-api" in only
    if path.endswith(".sql"):
        return "sql" in only
    if path.startswith("seed/payloads/"):
        return "payloads" in only
    return "files" in only


def check_sql(text: str, where: str) -> list[str]:
    """Cheap parse: single quotes must balance ('' is an escape), every statement ends with ';', no NUL bytes."""
    problems = []
    if "\x00" in text:
        problems.append(f"{where}: contains NUL byte")
    in_str = False
    stmts = 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if in_str:
            if ch == "'":
                if i + 1 < n and text[i + 1] == "'":
                    i += 2
                    continue
                in_str = False
        else:
            if ch == "'":
                in_str = True
            elif ch == "-" and text.startswith("--", i):
                j = text.find("\n", i)
                i = n if j < 0 else j
                continue
            elif ch == ";":
                stmts += 1
        i += 1
    if in_str:
        problems.append(f"{where}: unbalanced single quote (unescaped ' inside a literal?)")
    body = re.sub(r"--[^\n]*", "", text).strip()
    if body and not body.endswith(";"):
        problems.append(f"{where}: last statement is not terminated with ';'")
    return problems


def run_check(root: Path, ds: dict[str, Any]) -> int:
    problems: list[str] = []
    outputs = text_outputs(ds)
    for relpath, content in outputs.items():
        p = root / relpath
        if not p.exists():
            problems.append(f"missing: {relpath}")
            continue
        on_disk = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        if on_disk != content:
            problems.append(f"drift: {relpath} differs from generator output (run python seed/generate_seed.py)")
    for relpath in ("seed/schema.sql", "seed/seed.sql"):
        problems.extend(check_sql(outputs[relpath], relpath))
    # statement / row expectations
    seed_sql = outputs["seed/seed.sql"]
    inserts = seed_sql.count("\nINSERT INTO ")
    setvals = seed_sql.count("PERFORM setval(")
    if inserts != len(SEED_COLUMNS):
        problems.append(f"seed.sql: expected {len(SEED_COLUMNS)} INSERT statements, found {inserts}")
    if setvals != len(SERIAL_TABLES):
        problems.append(f"seed.sql: expected {len(SERIAL_TABLES)} setval() calls, found {setvals}")
    expected = {"customers": N_CUSTOMERS, "products": N_PRODUCTS, "orders": N_ORDERS, "employees": N_EMPLOYEES,
                "attendance": 300, "tickets": N_TICKETS, "leads": N_LEADS, "bookings": N_BOOKINGS,
                "expenses": N_EXPENSES, "rate_history": N_RATES, "uptime_state": 3, "sync_state": 2}
    for table, n in expected.items():
        if len(ds[table]) != n:
            problems.append(f"{table}: expected {n} rows, generated {len(ds[table])}")
    if not 380 <= len(ds["order_items"]) <= 470:
        problems.append(f"order_items: expected ~420 rows, generated {len(ds['order_items'])}")
    # referential integrity inside the dataset
    cust_ids = {c["id"] for c in ds["customers"]}
    prod_ids = {p["id"] for p in ds["products"]}
    order_ids = {o["id"] for o in ds["orders"]}
    emp_ids = {e["id"] for e in ds["employees"]}
    if any(o["customer_id"] not in cust_ids for o in ds["orders"]):
        problems.append("orders: dangling customer_id")
    if any(i["order_id"] not in order_ids or i["product_id"] not in prod_ids for i in ds["order_items"]):
        problems.append("order_items: dangling order_id/product_id")
    if any(a["employee_id"] not in emp_ids for a in ds["attendance"]):
        problems.append("attendance: dangling employee_id")
    if any(t["customer_id"] not in cust_ids for t in ds["tickets"]):
        problems.append("tickets: dangling customer_id")
    if any(b["customer_id"] not in cust_ids for b in ds["bookings"]):
        problems.append("bookings: dangling customer_id")
    # e-mail convention
    for relpath, content in outputs.items():
        for m in re.finditer(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", content):
            if m.group(1).lower() != "lab.local":
                problems.append(f"{relpath}: e-mail outside @lab.local: {m.group(0)}")
                break
    # mock db
    db = json.loads(outputs["docker/mock-api/db.json"])
    for coll, n in (("products", N_PRODUCTS), ("customers", N_CUSTOMERS), ("orders", N_MOCK_ORDERS),
                    ("events", N_MOCK_EVENTS), ("companies", len(COMPANIES)), ("posts", N_POSTS)):
        if len(db.get(coll, [])) != n:
            problems.append(f"db.json: {coll} expected {n}, found {len(db.get(coll, []))}")
    ev_ids = [e["id"] for e in db["events"]]
    if ev_ids != list(range(1, N_MOCK_EVENTS + 1)):
        problems.append("db.json: events ids are not 1..N incremental")
    # binary files present and non-trivial
    for name, min_size in (("orders.xlsx", 2000), ("invoice-locked.pdf", 1500), ("receipt-01.png", 5000),
                           ("receipt-02.png", 5000), ("receipt-ar-01.png", 5000), ("meeting-clip.wav", 900000)):
        p = root / "seed" / "files" / name
        if not p.exists():
            problems.append(f"missing binary: seed/files/{name}")
        elif p.stat().st_size < min_size:
            problems.append(f"seed/files/{name} is suspiciously small ({p.stat().st_size} bytes)")
    if problems:
        for pr in problems:
            print(f"CHECK FAIL  {pr}")
        return 1
    print(f"check ok: {len(outputs)} text artifacts match, SQL balanced, "
          f"rows customers={len(ds['customers'])} products={len(ds['products'])} orders={len(ds['orders'])} "
          f"order_items={len(ds['order_items'])} employees={len(ds['employees'])} attendance={len(ds['attendance'])} "
          f"tickets={len(ds['tickets'])} leads={len(ds['leads'])} bookings={len(ds['bookings'])} "
          f"expenses={len(ds['expenses'])} rate_history={len(ds['rate_history'])}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT, help="repo root to write into (default: this repo)")
    ap.add_argument("--only", action="append", choices=["sql", "files", "payloads", "mock-api"],
                    help="write only these groups (repeatable)")
    ap.add_argument("--mock-api", action="store_true", help="shorthand for --only mock-api")
    ap.add_argument("--check", action="store_true", help="validate the generated files on disk instead of writing")
    ap.add_argument("--no-optional-deps", action="store_true", help="ignore Pillow/openpyxl/reportlab/reshaper")
    args = ap.parse_args(argv)
    root: Path = args.out.resolve()
    ds = build_dataset()

    if args.check:
        return run_check(root, ds)

    only = set(args.only or [])
    if args.mock_api:
        only.add("mock-api")
    if not only:
        only = {"sql", "files", "payloads", "mock-api"}
    opt = Optional(not args.no_optional_deps)
    written: list[tuple[str, int]] = []

    for relpath, content in text_outputs(ds).items():
        if not select(relpath, only):
            continue
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
        written.append((relpath, p.stat().st_size))

    if "files" in only:
        files_dir = root / "seed" / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        notes = []
        notes.append(("orders.xlsx", write_xlsx(files_dir / "orders.xlsx", ds, opt)))
        # Same rows (and the same 3 broken ones) as customers.csv, for the XLSX branch of D01.
        cust_cols = ["external_id", "name", "email", "phone", "company", "country", "city", "segment"]
        notes.append(("customers.xlsx", write_xlsx(files_dir / "customers.xlsx", ds, opt,
                                                   sheets=[("Customers", cust_cols, ds["csv_customers"])])))
        notes.append(("invoice-locked.pdf", write_pdf(files_dir / "invoice-locked.pdf", opt)))
        notes.append(("receipt-*.png", write_receipts(files_dir, opt)))
        notes.append(("meeting-clip.wav", write_wav(files_dir / "meeting-clip.wav")))
        for name in ("orders.xlsx", "invoice-locked.pdf", "receipt-01.png", "receipt-02.png", "receipt-ar-01.png",
                     "meeting-clip.wav"):
            p = files_dir / name
            if p.exists():
                written.append((f"seed/files/{name}", p.stat().st_size))
        for name, how in notes:
            print(f"  {name:<22} {how}")

    for relpath, size in written:
        print(f"wrote {relpath} ({size:,} bytes)")
    print(f"rows: customers={len(ds['customers'])} products={len(ds['products'])} orders={len(ds['orders'])} "
          f"order_items={len(ds['order_items'])} employees={len(ds['employees'])} attendance={len(ds['attendance'])} "
          f"tickets={len(ds['tickets'])} leads={len(ds['leads'])} bookings={len(ds['bookings'])} "
          f"expenses={len(ds['expenses'])} rate_history={len(ds['rate_history'])} | mock: orders={len(ds['mock_orders'])} "
          f"events={len(ds['events'])} companies={len(ds['companies'])} posts={len(ds['posts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
