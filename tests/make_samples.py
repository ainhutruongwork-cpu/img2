"""Create 5 synthetic JPEGs carrying EXIF (GPS, serial, Make, Orientation, Copyright), ICC, XMP and a comment."""
import os, sys, numpy as np
from PIL import Image, ImageCms
out = sys.argv[1]
os.makedirs(f"{out}/1001", exist_ok=True)
icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
rng = np.random.default_rng(1)
for n in range(1, 6):
    w, h = 1200 + n * 37, 900 + n * 11
    x = np.linspace(0, 255, w)[None, :, None].repeat(h, 0)
    arr = (x * np.array([1, .6, .3]) + rng.integers(0, 40, (h, w, 3))).clip(0, 255).astype("uint8")
    im = Image.fromarray(arr)
    ex = Image.Exif()
    ex[0x010F], ex[0x0110], ex[0x0131] = "CamCo", "X100", "EditorPro"
    ex[0x013B], ex[0x8298] = "Test Artist", "(c) EnjoyingCraft test"
    ex[0x0112] = 6 if n == 3 else 1
    ex.get_ifd(0x8825)[1] = "N"; ex.get_ifd(0x8825)[2] = (10.0, 0.0, 0.0)
    ex.get_ifd(0x8769)[0xA431] = "SERIAL12345"
    im.save(f"{out}/1001/{n}.jpg", quality=85, subsampling=2, icc_profile=icc, exif=ex,
            comment=b"made by something", xmp=b'<x:xmpmeta><rdf:RDF><dc:rights><rdf:Alt><rdf:li xml:lang="x-default">XMP rights</rdf:li></rdf:Alt></dc:rights></rdf:RDF></x:xmpmeta>')
