# img2 — Etsy shop image backup (owner-authorized)

Pipeline: Etsy Open API v3 (`url_fullxfull`) -> download -> validate -> Pillow decode/encode
(JPEG q=100, 4:4:4, optimize, EXIF orientation applied, ICC kept) -> `listings/<listing_id>/NN.jpg` + `title.txt`.

Metadata policy: GPS, device make/model/serial, software, XMP, IPTC and JPEG comments are dropped.
Copyright and Artist are kept (EXIF; falls back to IPTC/XMP rights if EXIF has none).
Re-encoding does not remove any identifier that is inside the pixels (e.g. watermarks) and does not make
the files unattributable.

Setup: repo secret `ETSY_API_KEY` (Etsy app keystring; use `keystring:shared_secret` if Etsy requires it).
Run: Actions -> "Fetch and process Etsy images" -> mode `test` (5 images, report artifact, no commit), then `full`.
Output URLs: `reports/urls.txt` (https://raw.githubusercontent.com/<repo>/main/listings/<id>/NN.jpg).
Safety: 3 retries, 1.5 s between image downloads, manifest-based skip, backup/ before replacing, bad results never overwrite good files, failures in `reports/failed-*.json`.
Offline test: `python tests/make_samples.py /tmp/s && python scripts/pipeline.py --local-dir /tmp/s --out /tmp/o --manifest /tmp/m.json --max-images 5`
