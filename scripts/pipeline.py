#!/usr/bin/env python3
"""Etsy (official Open API v3) -> full-size images -> Pillow re-encode -> listings/<id>/NN.jpg

Env: ETSY_API_KEY (keystring, or "keystring:shared_secret"), GITHUB_REPOSITORY, GITHUB_REF_NAME (optional)
"""
import argparse, json, os, shutil, sys, tempfile, time, datetime as dt
import requests
sys.path.insert(0, os.path.dirname(__file__))
from imgproc import process, validate_jpeg, sha256

API = "https://openapi.etsy.com/v3/application"
UA = "img2-shop-backup/1.0 (owner-authorized backup)"
API_DELAY, IMG_DELAY, RETRIES = 0.5, 1.5, 3
REPO_SOFT_LIMIT = 900 * 1024 * 1024  # warn near GitHub's ~1 GB recommendation


def get(url, *, headers=None, params=None, stream=False, timeout=60):
    """GET with max 3 attempts, honouring Retry-After / 429 / 5xx."""
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, headers=headers, params=params, stream=stream, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 5 * attempt))
                last = f"HTTP {r.status_code}"
                time.sleep(min(wait, 60)); continue
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            last = str(e)
            if getattr(getattr(e, "response", None), "status_code", 0) in (400, 401, 403, 404):
                break  # not transient
            time.sleep(3 * attempt)
    raise RuntimeError(f"{url}: {last}")


def etsy_listings(shop, key):
    h = {"x-api-key": key, "User-Agent": UA}
    shop_id = get(f"{API}/shops", headers=h, params={"shop_name": shop}).json()["results"][0]["shop_id"]
    offset, out = 0, []
    while True:
        time.sleep(API_DELAY)
        d = get(f"{API}/shops/{shop_id}/listings", headers=h,
                params={"state": "active", "limit": 100, "offset": offset, "includes": "Images"}).json()
        out += d["results"]
        offset += 100
        if offset >= d["count"]:
            break
    for l in out:
        imgs = l.get("images")
        if imgs is None:
            time.sleep(API_DELAY)
            imgs = get(f"{API}/listings/{l['listing_id']}/images", headers=h).json()["results"]
        l["images"] = sorted(imgs, key=lambda i: i.get("rank", 0))
    return out


def dir_size(p):
    return sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(p) for f in fs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--shop", default="EnjoyingCraft")
    ap.add_argument("--out", default="listings")
    ap.add_argument("--manifest", default="manifest.json")
    ap.add_argument("--backup", default="backup")
    ap.add_argument("--report-dir", default="reports")
    ap.add_argument("--max-images", type=int, default=0, help="test mode: stop after N images (0 = all)")
    ap.add_argument("--local-dir", help="offline test: use <dir>/<listing>/<n>.jpg instead of Etsy")
    a = ap.parse_args()

    repo = os.environ.get("GITHUB_REPOSITORY", "ainhutruongwork-cpu/img2")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    raw = f"https://raw.githubusercontent.com/{repo}/{branch}"
    manifest = json.load(open(a.manifest)) if os.path.exists(a.manifest) else {}
    os.makedirs(a.report_dir, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if a.local_dir:
        listings = [{"listing_id": d, "title": f"Local test {d}", "images": [
            {"url_fullxfull": os.path.join(a.local_dir, d, f)} for f in sorted(os.listdir(os.path.join(a.local_dir, d)))]}
            for d in sorted(os.listdir(a.local_dir))]
    else:
        key = os.environ["ETSY_API_KEY"]
        listings = etsy_listings(a.shop, key)

    reports, failed, urls, stats = [], [], [], dict(listings=0, images=0, ok=0, skipped=0, failed=0, dup=0)
    done = 0
    for l in listings:
        lid = str(l["listing_id"])
        stats["listings"] += 1
        ldir = os.path.join(a.out, lid)
        os.makedirs(ldir, exist_ok=True)
        with open(os.path.join(ldir, "title.txt"), "w", encoding="utf-8") as f:
            f.write(l["title"].strip() + "\n")
        entry = manifest.setdefault(lid, {"title": l["title"], "images": {}})
        entry["title"] = l["title"]
        seen = {}
        for i, img in enumerate(l["images"], 1):
            if a.max_images and done >= a.max_images:
                break
            name = f"{i:02d}.jpg"
            dst = os.path.join(ldir, name)
            src_url = img["url_fullxfull"]
            stats["images"] += 1
            rec = entry["images"].get(name)
            if rec and rec.get("src_url") == src_url and os.path.exists(dst):
                try:
                    validate_jpeg(dst)
                    if sha256(dst) == rec["dst_sha256"]:
                        stats["skipped"] += 1; urls.append(f"{raw}/{a.out}/{lid}/{name}"); continue
                except Exception:
                    pass  # existing file is bad -> re-process
            tmpd = tempfile.mkdtemp()
            try:
                srcp = os.path.join(tmpd, "src")
                if a.local_dir:
                    shutil.copy(src_url, srcp)
                else:
                    time.sleep(IMG_DELAY)
                    r = get(src_url, headers={"User-Agent": UA}, stream=True)
                    with open(srcp, "wb") as f:
                        for c in r.iter_content(1 << 20):
                            f.write(c)
                    exp = int(r.headers.get("Content-Length", 0))
                    if exp and os.path.getsize(srcp) != exp:
                        raise RuntimeError("truncated download")
                validate_jpeg(srcp)
                s = sha256(srcp)
                if s in seen:
                    stats["dup"] += 1; continue
                seen[s] = name
                outp = os.path.join(tmpd, "out.jpg")
                rep = process(srcp, outp)
                if not rep["ok"]:
                    raise RuntimeError("quality checks failed: " + json.dumps(
                        {k: v for k, v in rep["checks"].items() if not v}))
                validate_jpeg(outp)
                if os.path.exists(dst):  # back up before replacing
                    bk = os.path.join(a.backup, lid, stamp)
                    os.makedirs(bk, exist_ok=True)
                    shutil.copy2(dst, os.path.join(bk, name))
                os.replace(outp, dst) if os.path.dirname(outp) == os.path.dirname(dst) else shutil.move(outp, dst)
                rep["dst"] = dst
                rep["url"] = f"{raw}/{a.out}/{lid}/{name}"
                rep["src_url"] = src_url
                reports.append(rep); urls.append(rep["url"])
                entry["images"][name] = {"src_url": src_url, "src_sha256": s,
                                         "dst_sha256": rep["dst_sha256"], "bytes": rep["dst_bytes"]}
                stats["ok"] += 1; done += 1
                print(f"OK  {lid}/{name}  {rep['src_bytes']}->{rep['dst_bytes']} B  maxdiff={rep['pixel']['max_abs_diff']}")
            except Exception as e:
                stats["failed"] += 1
                failed.append({"listing": lid, "image": name, "url": src_url, "error": str(e)})
                print(f"FAIL {lid}/{name}: {e}", file=sys.stderr)  # existing good file untouched
            finally:
                shutil.rmtree(tmpd, ignore_errors=True)
        if a.max_images and done >= a.max_images:
            break

    size = dir_size(a.out)
    stats["repo_images_bytes"] = size
    if size > REPO_SOFT_LIMIT:
        print(f"WARNING: {a.out}/ is {size/1e6:.0f} MB, near repository size limits", file=sys.stderr)
    json.dump(manifest, open(a.manifest, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    json.dump(reports, open(os.path.join(a.report_dir, f"report-{stamp}.json"), "w"), indent=1, ensure_ascii=False, default=str)
    json.dump(failed, open(os.path.join(a.report_dir, f"failed-{stamp}.json"), "w"), indent=1)
    open(os.path.join(a.report_dir, "urls.txt"), "w").write("\n".join(urls) + "\n")
    json.dump(stats, open(os.path.join(a.report_dir, "summary.json"), "w"), indent=1)
    print(json.dumps(stats))
    sys.exit(1 if failed and not reports and not stats["skipped"] else 0)


if __name__ == "__main__":
    main()
