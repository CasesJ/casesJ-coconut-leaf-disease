import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from PIL import Image
import piexif
from drone_gps import init_drone_gps

# Paths
orig = os.path.join('static', 'coco-farm.jpg')
dest = os.path.join('static', 'coco-farm-exif.jpg')

# GPS values to embed (example: 7.342700, 125.629000)
lat = 7.3427
lng = 125.6290
alt = 12.3

def to_deg(value):
    # Convert decimal degrees to (deg, min, sec) rational tuples
    d = int(abs(value))
    mfull = (abs(value) - d) * 60
    m = int(mfull)
    s = round((mfull - m) * 60 * 100)
    return ((d,1),(m,1),(s,100))

lat_ref = 'N' if lat >= 0 else 'S'
lon_ref = 'E' if lng >= 0 else 'W'

exif_dict = {"0th":{}, "Exif":{}, "GPS":{}, "1st":{}, "thumbnail": None}
exif_dict['GPS'][piexif.GPSIFD.GPSLatitudeRef] = lat_ref
exif_dict['GPS'][piexif.GPSIFD.GPSLatitude] = to_deg(lat)
exif_dict['GPS'][piexif.GPSIFD.GPSLongitudeRef] = lon_ref
exif_dict['GPS'][piexif.GPSIFD.GPSLongitude] = to_deg(lng)
exif_dict['GPS'][piexif.GPSIFD.GPSAltitude] = (int(alt*10), 10)
exif_bytes = piexif.dump(exif_dict)

# Insert EXIF
im = Image.open(orig)
im.save(dest, "jpeg", exif=exif_bytes)

# Test extraction
gps = init_drone_gps(use_simulation=False).extract_gps_from_image(open(dest,'rb').read(), os.path.basename(dest), fallback_coords=None)
print('Inserted EXIF GPS, extraction result:')
print(gps)
if gps:
    print('lat', gps.latitude, 'lng', gps.longitude, 'alt', gps.altitude, 'acc', gps.accuracy, 'source', gps.source)
else:
    print('Failed to extract EXIF GPS')
