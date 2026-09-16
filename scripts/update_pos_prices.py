"""Explicit, price-only catalog update. Dry-run by default; never a migrate hook."""
from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import urllib.parse
import urllib.request


VINEYARD = "Marnisi M'Xlokk"
# Exact existing item codes/names, not fuzzy matches or newly created items.
REQUESTS = (
    ("The Antonin Experience", "The Antonin Experience", "85.00"),
    ("The Terrarosa Experience", "The Terrarosa Experience", "100.00"),
    ("The Marnisi Wine Estate  E xperience", "The Marnisi Wine Estate Experience", "115.00"),
    ("FMWANT002", "Antonin Blanc 0.75L", "20.75"),
    ("FMWANT009", "Antonin Blanc Rizerva", "56.25"),
    ("FMWANT004", "Antonin Noir 0.75L", "20.75"),
    ("FMWANT011", "Antonin Noir Rizerva 0.75L", "56.25"),
    ("FMWCSR006", "Marsovin Brut Rose 0.75L", "30.60"),
    ("FMWCSR001", "Cassar De Malte 0.75L", "29.90"),
)


def gross_price(net, tax_pct):
    return (Decimal(str(net)) * (1 + Decimal(str(tax_pct)) / 100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def sell_price(gross, tax_pct):
    gross, tax_pct = Decimal(str(gross)), Decimal(str(tax_pct))
    if not gross.is_finite() or not tax_pct.is_finite() or gross < 0 or tax_pct < 0:
        raise ValueError("Price and VAT must be finite, non-negative numbers")
    if gross != gross.quantize(Decimal("0.01")):
        raise ValueError("The requested final price must have at most two decimals")
    net = (gross / (1 + tax_pct / 100)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    if gross_price(net, tax_pct) != gross:
        raise ValueError("Three-decimal Sell Price cannot represent the requested final price")
    return net


def build_plan(items, products):
    plan = []
    for code, label, gross in REQUESTS:
        matches = [row for row in items if row["vineyard"] == VINEYARD
                   and row["item_code"].strip() == code]
        if len(matches) != 1 or matches[0]["item_name"].strip() != label:
            raise ValueError(f"Expected exactly one matching item: {code} / {label}")
        item = matches[0]
        scoped_id = f"{VINEYARD}::{code}"
        pos = [row for row in products if row["item_id"] == scoped_id]
        if len(pos) != 1 or pos[0]["item_store"] != VINEYARD:
            raise ValueError(f"Item is not uniquely available to POS: {code}")
        if Decimal(str(pos[0]["item_tax_pct"])) != Decimal("18"):
            raise ValueError(f"Unexpected VAT rate for {code}; review before updating")
        if Decimal(str(pos[0]["item_price"])) != Decimal(str(item["sell_price"])):
            raise ValueError(f"POS and backend price disagree for {code}")
        plan.append({
            "name": item["name"], "item_code": code, "item_name": label,
            "before": str(item["sell_price"]), "modified": item["modified"],
            "sell_price": str(sell_price(gross, 18)), "pos_price": gross,
            "pos_id": scoped_id,
        })
    if len({row["name"] for row in plan}) != len(plan):
        raise ValueError("Multiple price requests resolve to the same document")
    return plan


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("Redirect refused; configure the approved backend HTTPS origin")


class Client:
    def __init__(self, base_url, *, allow_loopback_http=False):
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/"):
            raise ValueError("Supply the backend origin, without /desk or /api")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Credentials/query/fragment are not permitted in the origin")
        if parsed.scheme != "https" and not (allow_loopback_http and parsed.hostname in {"localhost", "127.0.0.1", "::1"}):
            raise ValueError("HTTPS is required; HTTP is restricted to explicit loopback development")
        self.base_url = base_url.rstrip("/")
        self.csrf_token = ""
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), NoRedirect()
        )

    def request(self, path, payload=None, method=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"Content-Type": "application/json", **({"X-Frappe-CSRF-Token": self.csrf_token} if self.csrf_token else {})},
        )
        with self.opener.open(request, timeout=30) as response:
            return json.load(response)

    def call(self, method, args=None):
        payload = None if args is None else {"args": json.dumps(args)}
        return self.request("/api/method/" + method, payload).get("message")

    def login(self, personal_id, password):
        response = self.call("xirja_marnisi.api.auth.login_with_personal_id", {
            "personal_id": personal_id, "password": password,
        })
        if not isinstance(response, dict) or not response.get("csrf_token"):
            raise ValueError("Authenticated session and CSRF token are required")
        self.csrf_token = response["csrf_token"]

    def document(self, name):
        path = "/api/resource/Vineyard%20Item/" + urllib.parse.quote(name, safe="")
        return self.request(path)["data"]

    def set_price(self, row):
        path = "/api/resource/Vineyard%20Item/" + urllib.parse.quote(row["name"], safe="")
        # Passing the previously read timestamp enables Frappe's stale-document check.
        self.request(path, {"sell_price": float(row["sell_price"]), "modified": row["modified"]}, "PUT")


def apply_plan(client, plan, backup_file):
    documents = {row["name"]: client.document(row["name"]) for row in plan}
    for row in plan:
        doc = documents[row["name"]]
        if (doc["modified"] != row["modified"] or doc["vineyard"] != VINEYARD
                or doc["item_code"].strip() != row["item_code"]
                or doc["item_name"].strip() != row["item_name"]
                or Decimal(str(doc["sell_price"])) != Decimal(row["before"])):
            raise ValueError(f"Item changed since preview: {row['name']}")

    # Exclusive creation prevents a rerun from overwriting the original prices.
    with Path(backup_file).open("x", encoding="utf-8") as handle:
        json.dump({"backend": client.base_url, "plan": plan, "documents": documents}, handle, indent=2)
    for row in plan:
        if Decimal(row["before"]) != Decimal(row["sell_price"]):
            client.set_price(row)
        saved = client.document(row["name"])
        before = documents[row["name"]]
        changed = {key for key in before.keys() | saved.keys() if before.get(key) != saved.get(key)}
        if changed - {"sell_price", "modified", "modified_by"}:
            raise ValueError(f"Unexpected non-price changes on {row['name']}: {changed}")
        if Decimal(str(saved["sell_price"])) != Decimal(row["sell_price"]):
            raise ValueError(f"Sell Price did not persist for {row['name']}")
        print(f"Verified {row['name']}: Sell Price {row['sell_price']} -> EUR {row['pos_price']}", flush=True)


def verify_pos(plan, before_products, after_products):
    expected = {row["pos_id"]: row for row in plan}
    before = {row["item_id"]: row for row in before_products}
    after = {row["item_id"]: row for row in after_products}
    if before.keys() != after.keys() or len(after) != len(after_products):
        raise ValueError("POS catalog membership changed during update")
    for item_id, row in after.items():
        allowed = {"item_price"} if item_id in expected else set()
        changed = {key for key in row.keys() | before[item_id].keys()
                   if row.get(key) != before[item_id].get(key)}
        if changed - allowed:
            raise ValueError(f"Unexpected POS catalog changes: {item_id}: {changed}")
        if item_id in expected:
            if gross_price(row["item_price"], row["item_tax_pct"]) != Decimal(expected[item_id]["pos_price"]):
                raise ValueError(f"Final POS price mismatch: {item_id}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--personal-id", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-loopback-http", action="store_true")
    parser.add_argument("--backup-file")
    args = parser.parse_args()
    if args.apply and not args.backup_file:
        parser.error("--apply requires --backup-file (a new file)")
    client = Client(args.base_url, allow_loopback_http=args.allow_loopback_http)
    client.login(args.personal_id, getpass.getpass("Your Frappe password: "))
    try:
        items = client.call("xirja_marnisi.api.item.list", {"vineyard": VINEYARD})["items"]
        products = client.call("xirja_marnisi.api.bridge.get_all_products")
        plan = build_plan(items, products)
        print(json.dumps(plan, indent=2), flush=True)
        if args.apply:
            apply_plan(client, plan, args.backup_file)
            verify_pos(plan, products, client.call("xirja_marnisi.api.bridge.get_all_products"))
            print("All nine POS prices verified. Other catalog fields/items unchanged.")
        else:
            print("DRY RUN: no prices changed. Use --apply --backup-file <new-file> to update.")
    finally:
        client.request("/api/method/logout", {})


if __name__ == "__main__":
    main()
