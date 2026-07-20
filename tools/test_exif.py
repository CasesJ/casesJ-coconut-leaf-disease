import sys, os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from drone_gps import init_drone_gps, get_drone_gps

# Initialize drone GPS helper (no real connection needed)
drone_gps = init_drone_gps(use_simulation=False)

with open('static/coco-farm.jpg','rb') as f:
    contents = f.read()

res = drone_gps.extract_gps_from_image(contents, 'coco-farm.jpg', fallback_coords=None)
print('EXIF GPS result:', res)
if res:
    print('latitude:', res.latitude)
    print('longitude:', res.longitude)
    print('altitude:', res.altitude)
    print('accuracy:', res.accuracy)
    print('source:', res.source)
else:
    print('No EXIF GPS found in image')
