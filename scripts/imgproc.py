"""Re-encode an image with Pillow (decode -> encode) and audit metadata before/after.

Rules: JPEG q=100, 4:4:4, optimize, pixel size unchanged, EXIF orientation applied,
ICC profile kept, personal metadata dropped, copyright/creator attribution kept.
"""
import hashlib, io, re, os, json
from PIL import Image, ImageOps, JpegImagePlugin, IptcImagePlugin
import numpy as np

TAG_ARTIST, TAG_COPYRIGHT, TAG_ORIENT = 0x013B, 0x8298, 0x0112
MAX_FILE_BYTES = 25 * 1024 * 1024  # warn threshold (GitHub hard limit is 100 MB)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _xmp_rights(xmp_bytes):
    if not xmp_bytes:
        return None
    m = re.search(rb"<dc:rights>.*?<rdf:li[^>]*>(.*?)</rdf:li>", xmp_bytes, re.S)
    return m.group(1).decode("utf-8", "replace").strip() if m else None


def audit_metadata(path):
    """Return a JSON-able summary of every metadata container found in the file."""
    with Image.open(path) as im:
        exif = im.getexif()
        ifd0 = {int(k): str(v)[:80] for k, v in exif.items()}
        gps = dict(exif.get_ifd(0x8825)) if 0x8825 in exif else {}
        sub = dict(exif.get_ifd(0x8769)) if 0x8769 in exif else {}
        xmp = im.info.get("xmp") or b""
        try:
            iptc = IptcImagePlugin.getiptcinfo(im) or {}
        except Exception:
            iptc = {}
        comment = im.info.get("comment")
        icc = im.info.get("icc_profile")
        return {
            "format": im.format, "mode": im.mode, "size": list(im.size),
            "exif_ifd0_tags": sorted(ifd0), "exif_orientation": exif.get(TAG_ORIENT),
            "exif_artist": exif.get(TAG_ARTIST), "exif_copyright": exif.get(TAG_COPYRIGHT),
            "exif_make_model": [exif.get(0x010F), exif.get(0x0110)],
            "exif_software": exif.get(0x0131),
            "exif_has_gps": bool(gps), "exif_subifd_tags": len(sub),
            "exif_body_serial": sub.get(0xA431),
            "xmp_bytes": len(xmp), "xmp_rights": _xmp_rights(xmp),
            "iptc_tags": sorted(f"{a}:{b}" for a, b in iptc.keys()),
            "iptc_copyright": (iptc.get((2, 116)) or b"").decode("utf-8", "replace") if iptc.get((2, 116)) else None,
            "jpeg_comment": comment.decode("utf-8", "replace") if isinstance(comment, bytes) else comment,
            "icc_profile_bytes": len(icc) if icc else 0,
            "icc_sha256": hashlib.sha256(icc).hexdigest() if icc else None,
            "subsampling": JpegImagePlugin.get_sampling(im) if im.format == "JPEG" else None,
        }


def _decode_rgb_oriented(path):
    with Image.open(path) as im:
        im.load()
        icc = im.info.get("icc_profile")
        exif = im.getexif()
        info_before = dict(im.info)
        oriented = ImageOps.exif_transpose(im)
        if oriented.mode != "RGB":
            oriented = oriented.convert("RGB")
        return oriented, icc, exif, info_before


def process(src, dst):
    """Decode src and re-encode to dst (JPEG). Returns report dict. Raises on failure."""
    im, icc, exif, info = _decode_rgb_oriented(src)
    meta_before = audit_metadata(src)

    # Attribution: EXIF first, then IPTC/XMP fallbacks. Everything else is dropped.
    copyright_ = exif.get(TAG_COPYRIGHT) or meta_before["iptc_copyright"] or meta_before["xmp_rights"]
    artist = exif.get(TAG_ARTIST)
    new_exif = Image.Exif()
    if copyright_:
        new_exif[TAG_COPYRIGHT] = str(copyright_)
    if artist:
        new_exif[TAG_ARTIST] = str(artist)

    im.info = {}  # drop comment/xmp/adobe/etc. carried in info
    kw = dict(format="JPEG", quality=100, subsampling=0, optimize=True)
    if icc:
        kw["icc_profile"] = icc
    if len(new_exif):
        kw["exif"] = new_exif.tobytes()
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    im.save(dst, **kw)

    meta_after = audit_metadata(dst)
    a = np.asarray(im, dtype=np.int16)
    with Image.open(dst) as out:
        out.load()
        b = np.asarray(out.convert("RGB"), dtype=np.int16)
    d = np.abs(a - b)
    mse = float((d.astype(np.float64) ** 2).mean())
    psnr = float("inf") if mse == 0 else float(10 * np.log10(255 ** 2 / mse))
    mean_rgb_delta = [float(a[..., c].mean() - b[..., c].mean()) for c in range(3)]
    checks = {
        "size_unchanged_after_orientation": list(im.size) == meta_after["size"],
        "subsampling_444": meta_after["subsampling"] == 0,
        "no_gps": not meta_after["exif_has_gps"],
        "no_xmp": meta_after["xmp_bytes"] == 0,
        "no_iptc": not meta_after["iptc_tags"],
        "no_comment": not meta_after["jpeg_comment"],
        "no_orientation_tag": meta_after["exif_orientation"] in (None, 1),
        "icc_preserved": meta_after["icc_sha256"] == meta_before["icc_sha256"],
        "copyright_preserved": (not copyright_) or meta_after["exif_copyright"] == str(copyright_),
        "file_under_limit": os.path.getsize(dst) < MAX_FILE_BYTES,
    }
    return {
        "src": src, "dst": dst,
        "src_bytes": os.path.getsize(src), "dst_bytes": os.path.getsize(dst),
        "src_sha256": sha256(src), "dst_sha256": sha256(dst),
        "src_size": meta_before["size"], "dst_size": meta_after["size"],
        "orientation_applied": meta_before["exif_orientation"] not in (None, 1),
        "meta_before": meta_before, "meta_after": meta_after,
        "pixel": {"max_abs_diff": int(d.max()), "mean_abs_diff": float(d.mean()),
                  "psnr_db": psnr, "mean_rgb_delta": mean_rgb_delta},
        "checks": checks, "ok": all(checks.values()),
    }


def validate_jpeg(path):
    """Full decode check; raises on truncated/corrupt files."""
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        im.load()
        if im.width < 10 or im.height < 10:
            raise ValueError("image too small")
    return True
