"""Create the Guardian GPU droplet + cloud firewall on DigitalOcean (idempotent).

Runs on the control machine (your laptop); nothing is hosted locally.

    DO_TOKEN=... python infra/do_create_droplet.py [--size gpu-4000adax1-20gb]
        [--region tor1] [--name guardian-gpu] [--ssh-key ~/.ssh/id_ed25519.pub]
        [--list]          only list available GPU sizes/regions and AI/ML images

What it does:
  1. uploads your SSH public key (if DO does not know it yet)
  2. picks a GPU size (default RTX 4000 Ada, 20 GB: medgemma:4b + gemma4:12b)
     in a region where it is available, and the NVIDIA "AI/ML ready" image
  3. creates the droplet (or reuses one with the same name) and waits for its IP
  4. creates/attaches a cloud firewall: inbound 22, 80, 443 only (never 11434/8000)
Prints the IP plus the PUBLIC_HOST (<ip-with-dashes>.sslip.io) to deploy with.

GPU droplets bill per hour until DESTROYED (a powered-off droplet still bills).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

API = "https://api.digitalocean.com/v2"
PREFERRED_SIZES = [
    "gpu-4000adax1-20gb",  # RTX 4000 Ada 20 GB (~$0.76/h) - recommended start
    "gpu-6000adax1-48gb",  # RTX 6000 Ada 48 GB
    "gpu-l40sx1-48gb",     # L40S 48 GB (~$1.57/h)
    "gpu-h100x1-80gb",     # H100 80 GB (~$3.39/h) - overkill
]
FIREWALL_NAME = "guardian-fw"
TAG = "guardian"


def client(token: str) -> httpx.Client:
    return httpx.Client(base_url=API, timeout=60,
                        headers={"Authorization": f"Bearer {token}",
                                 "Content-Type": "application/json"})


def check(resp: httpx.Response) -> dict:
    if resp.status_code >= 400:
        raise SystemExit(f"DigitalOcean API {resp.request.method} {resp.request.url.path} "
                         f"-> HTTP {resp.status_code}: {resp.text[:600]}")
    return resp.json() if resp.content else {}


def gpu_sizes(c: httpx.Client) -> list[dict]:
    sizes = check(c.get("/sizes", params={"per_page": 200}))["sizes"]
    return [s for s in sizes if s["slug"].startswith("gpu-") and s.get("available")]


def nvidia_images(c: httpx.Client) -> list[dict]:
    images = check(c.get("/images", params={"type": "distribution", "per_page": 200}))["images"]
    out = []
    for img in images:
        slug, name = (img.get("slug") or ""), (img.get("name") or "")
        if slug.startswith("gpu-") and "amd" not in slug.lower():
            out.append(img)
        elif "ai/ml" in name.lower() and "amd" not in name.lower():
            out.append(img)
    return out


def ensure_ssh_key(c: httpx.Client, pub_path: Path) -> int:
    public_key = pub_path.read_text(encoding="utf-8").strip()
    keys = check(c.get("/account/keys", params={"per_page": 200}))["ssh_keys"]
    for k in keys:
        if k["public_key"].split()[:2] == public_key.split()[:2]:
            print(f" - ssh key already on DO: {k['name']} ({k['fingerprint']})")
            return k["id"]
    created = check(c.post("/account/keys", json={"name": f"guardian-{pub_path.stem}",
                                                   "public_key": public_key}))["ssh_key"]
    print(f" - ssh key uploaded: {created['fingerprint']}")
    return created["id"]


def find_droplet(c: httpx.Client, name: str) -> dict | None:
    droplets = check(c.get("/droplets", params={"tag_name": TAG, "per_page": 200}))["droplets"]
    return next((d for d in droplets if d["name"] == name), None)


def public_ipv4(droplet: dict) -> str | None:
    for net in droplet.get("networks", {}).get("v4", []):
        if net.get("type") == "public":
            return net["ip_address"]
    return None


def wait_active(c: httpx.Client, droplet_id: int, timeout_s: int = 900) -> dict:
    deadline = time.monotonic() + timeout_s
    while True:
        d = check(c.get(f"/droplets/{droplet_id}"))["droplet"]
        if d["status"] == "active" and public_ipv4(d):
            return d
        if time.monotonic() > deadline:
            raise SystemExit(f"droplet {droplet_id} not active after {timeout_s}s (status={d['status']})")
        print(f"   waiting for droplet (status={d['status']}) ...")
        time.sleep(10)


def ensure_firewall(c: httpx.Client, droplet_id: int) -> None:
    rules_in = [
        {"protocol": "tcp", "ports": str(p), "sources": {"addresses": ["0.0.0.0/0", "::/0"]}}
        for p in (22, 80, 443)
    ]
    rules_out = [
        {"protocol": proto, "ports": "all" if proto != "icmp" else None,
         "destinations": {"addresses": ["0.0.0.0/0", "::/0"]}}
        for proto in ("tcp", "udp", "icmp")
    ]
    for r in rules_out:
        if r["ports"] is None:
            del r["ports"]
    firewalls = check(c.get("/firewalls", params={"per_page": 200}))["firewalls"]
    fw = next((f for f in firewalls if f["name"] == FIREWALL_NAME), None)
    body = {"name": FIREWALL_NAME, "inbound_rules": rules_in, "outbound_rules": rules_out,
            "droplet_ids": sorted({droplet_id, *(fw["droplet_ids"] if fw else [])})}
    if fw:
        check(c.put(f"/firewalls/{fw['id']}", json=body))
        print(f" - firewall {FIREWALL_NAME}: updated (inbound 22/80/443 only)")
    else:
        check(c.post("/firewalls", json=body))
        print(f" - firewall {FIREWALL_NAME}: created (inbound 22/80/443 only)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="guardian-gpu")
    ap.add_argument("--size", default=None, help="GPU size slug (default: first available of the preferred list)")
    ap.add_argument("--region", default=None)
    ap.add_argument("--image", default=None, help="image slug/id (default: NVIDIA AI/ML ready image)")
    ap.add_argument("--ssh-key", default=str(Path.home() / ".ssh" / "id_ed25519.pub"))
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    token = os.environ.get("DO_TOKEN") or os.environ.get("DIGITALOCEAN_TOKEN")
    if not token:
        print("Set DO_TOKEN (DigitalOcean API token with write scope).", file=sys.stderr)
        return 2
    c = client(token)
    acct = check(c.get("/account"))["account"]
    print(f"DigitalOcean account: {acct.get('email')} (droplet limit {acct.get('droplet_limit')})")

    sizes = gpu_sizes(c)
    images = nvidia_images(c)
    if args.list or not sizes:
        print("GPU sizes available to this account:")
        for s in sizes:
            print(f"   {s['slug']:24} ${s['price_hourly']}/h  regions={s['regions']}")
        print("NVIDIA AI/ML images:")
        for i in images:
            print(f"   {i.get('slug') or i['id']}: {i['name']} ({i.get('distribution')})")
        if not sizes:
            print("No GPU sizes available - GPU droplets may need to be enabled for this account.",
                  file=sys.stderr)
            return 1
        return 0

    existing = find_droplet(c, args.name)
    if existing:
        print(f" - droplet {args.name} exists (id {existing['id']}), reusing")
        droplet = wait_active(c, existing["id"])
    else:
        by_slug = {s["slug"]: s for s in sizes}
        size = by_slug.get(args.size) if args.size else next(
            (by_slug[s] for s in PREFERRED_SIZES if s in by_slug), sizes[0])
        if size is None:
            raise SystemExit(f"size {args.size} not available; run with --list")
        region = args.region or size["regions"][0]
        if region not in size["regions"]:
            raise SystemExit(f"{size['slug']} not available in {region}; regions={size['regions']}")
        image = args.image or next(
            ((i.get("slug") or i["id"]) for i in images if region in (i.get("regions") or [region])), None)
        if image is None:
            raise SystemExit("no NVIDIA AI/ML image found; pass --image (run with --list)")
        key_id = ensure_ssh_key(c, Path(os.path.expanduser(args.ssh_key)))
        print(f" - creating {args.name}: size={size['slug']} (${size['price_hourly']}/h) "
              f"region={region} image={image}")
        created = check(c.post("/droplets", json={
            "name": args.name, "region": region, "size": size["slug"], "image": image,
            "ssh_keys": [key_id], "tags": [TAG], "monitoring": True,
        }))["droplet"]
        droplet = wait_active(c, created["id"])

    ip = public_ipv4(droplet)
    ensure_firewall(c, droplet["id"])
    host = ip.replace(".", "-") + ".sslip.io"
    print()
    print(f"Droplet ready: {droplet['name']}  ip={ip}  size={droplet['size_slug']}  region={droplet['region']['slug']}")
    print(f"PUBLIC_HOST={host}")
    print(f"Next: bash infra/deploy.sh {ip}")
    print("Remember: destroy the droplet when the hackathon ends (powered-off droplets still bill).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
