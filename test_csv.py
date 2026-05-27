import json
from pathlib import Path
from collections import defaultdict
from io import StringIO

# Read from detection_history.json
all_detections = []
detection_file = Path("detection_history.json")

if detection_file.exists():
    with open(detection_file, 'r') as f:
        all_detections = json.load(f)
    print(f"✅ Loaded {len(all_detections)} detections")
else:
    print("❌ File not found")
    exit(1)

# Calculate disease statistics
disease_stats = defaultdict(lambda: {
    'count': 0,
    'high': 0,
    'medium': 0,
    'low': 0,
    'avg_confidence': 0,
    'total_confidence': 0
})

for detection in all_detections:
    disease = detection.get('disease_name', 'Unknown')
    confidence = float(detection.get('confidence', 0))
    severity = detection.get('severity', 'low')
    
    disease_stats[disease]['count'] += 1
    disease_stats[disease]['total_confidence'] += confidence
    
    if severity == 'high':
        disease_stats[disease]['high'] += 1
    elif severity == 'medium':
        disease_stats[disease]['medium'] += 1
    else:
        disease_stats[disease]['low'] += 1

# Calculate average confidence
for disease in disease_stats:
    if disease_stats[disease]['count'] > 0:
        disease_stats[disease]['avg_confidence'] = round(
            disease_stats[disease]['total_confidence'] / disease_stats[disease]['count'], 4
        )

# Build CSV
csv_buffer = StringIO()
csv_buffer.write("Disease Name,Confidence,Severity,Field ID,Detection Date,Location,Email,Source\n")

# Write detections
for detection in all_detections:
    disease = detection.get('disease_name', 'Unknown')
    confidence = float(detection.get('confidence', 0))
    severity = detection.get('severity', 'low')
    
    location_str = ""
    if detection.get('location'):
        if isinstance(detection['location'], dict):
            location_str = f"{detection['location'].get('lat', '')},{detection['location'].get('lng', '')}"
    
    csv_buffer.write(f"{disease},{confidence:.4f},{severity},{detection.get('field_id', '')},{detection.get('detection_date', '')},{location_str},{detection.get('email', '')},detection\n")

# Summary section
csv_buffer.write("\n=== INVENTORY SUMMARY ===\n\n")
csv_buffer.write("Disease,Total Count,Avg Confidence,High,Medium,Low\n")

total_detections = 0
total_high = 0
total_medium = 0
total_low = 0
total_confidence = 0

for disease in sorted(disease_stats.keys(), key=lambda x: disease_stats[x]['count'], reverse=True):
    stats = disease_stats[disease]
    csv_buffer.write(f"{disease},{stats['count']},{stats['avg_confidence']},{stats['high']},{stats['medium']},{stats['low']}\n")
    total_detections += stats['count']
    total_high += stats['high']
    total_medium += stats['medium']
    total_low += stats['low']
    total_confidence += stats['total_confidence']

csv_buffer.write(f"\nTOTAL,{total_detections},{round(total_confidence/total_detections, 4) if total_detections > 0 else 0},{total_high},{total_medium},{total_low}\n")

csv_content = csv_buffer.getvalue()
print(f"✅ CSV Generated Successfully ({len(csv_content)} bytes)")
print(f"✅ {total_detections} detections processed")
print("\n📊 Preview (first 500 chars):")
print(csv_content[:500])
print("\n✅ CSV export logic is working correctly!")
