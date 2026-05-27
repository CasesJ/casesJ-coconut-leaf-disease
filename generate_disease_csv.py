"""
Generate Disease Detection CSV Report with Inventory Totals
Reads all detection records and creates a comprehensive CSV with summary statistics
"""

import json
import csv
from pathlib import Path
from collections import defaultdict
from datetime import datetime

def load_all_detections():
    """Load all detection records from JSON files"""
    detections = []
    
    # Load from detection_history.json
    history_file = Path("detection_history.json")
    if history_file.exists():
        with open(history_file, 'r') as f:
            history_data = json.load(f)
            for detection in history_data:
                detections.append({
                    'source': 'history',
                    'disease_name': detection.get('disease_name'),
                    'confidence': detection.get('confidence'),
                    'severity': detection.get('severity'),
                    'field_id': detection.get('field_id'),
                    'detection_date': detection.get('detection_date'),
                    'location': detection.get('location'),
                    'email': 'unknown'
                })
    
    # Load from individual detection record files
    detection_records_dir = Path("detection_records")
    if detection_records_dir.exists():
        for user_dir in detection_records_dir.iterdir():
            if user_dir.is_dir():
                for json_file in user_dir.glob("*.json"):
                    try:
                        with open(json_file, 'r') as f:
                            record = json.load(f)
                            for inference in record.get('inference_results', []):
                                detections.append({
                                    'source': 'record',
                                    'disease_name': inference.get('class'),
                                    'confidence': inference.get('confidence'),
                                    'severity': infer_severity(inference.get('confidence', 0)),
                                    'field_id': user_dir.name,
                                    'detection_date': record.get('timestamp'),
                                    'location': record.get('gps_data'),
                                    'email': record.get('email', 'unknown'),
                                    'user_id': record.get('user_id')
                                })
                    except Exception as e:
                        print(f"Error reading {json_file}: {e}")
    
    return detections


def infer_severity(confidence):
    """Infer severity level based on confidence score"""
    if confidence >= 0.85:
        return "high"
    elif confidence >= 0.70:
        return "medium"
    else:
        return "low"


def generate_csv():
    """Generate comprehensive CSV report with detections and inventory totals"""
    
    detections = load_all_detections()
    
    if not detections:
        print("No detections found!")
        return
    
    # Calculate disease statistics
    disease_stats = defaultdict(lambda: {
        'count': 0,
        'high': 0,
        'medium': 0,
        'low': 0,
        'avg_confidence': 0,
        'total_confidence': 0
    })
    
    for detection in detections:
        disease = detection['disease_name']
        disease_stats[disease]['count'] += 1
        disease_stats[disease]['total_confidence'] += detection.get('confidence', 0)
        
        severity = detection.get('severity', 'low')
        if severity == 'high':
            disease_stats[disease]['high'] += 1
        elif severity == 'medium':
            disease_stats[disease]['medium'] += 1
        else:
            disease_stats[disease]['low'] += 1
    
    # Calculate average confidence for each disease
    for disease in disease_stats:
        if disease_stats[disease]['count'] > 0:
            disease_stats[disease]['avg_confidence'] = round(
                disease_stats[disease]['total_confidence'] / disease_stats[disease]['count'], 4
            )
    
    # Write CSV
    csv_filename = "disease_detections_inventory.csv"
    
    with open(csv_filename, 'w', newline='') as csvfile:
        fieldnames = [
            'Disease Name',
            'Confidence',
            'Severity',
            'Field ID',
            'Detection Date',
            'Location (Lat/Lng)',
            'Email',
            'Source'
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        
        # Write individual detections
        for detection in detections:
            location_str = ""
            if detection['location']:
                if isinstance(detection['location'], dict):
                    location_str = f"{detection['location'].get('lat', '')}, {detection['location'].get('lng', '')}"
                else:
                    location_str = str(detection['location'])
            
            writer.writerow({
                'Disease Name': detection.get('disease_name', ''),
                'Confidence': round(detection.get('confidence', 0), 4),
                'Severity': detection.get('severity', ''),
                'Field ID': detection.get('field_id', ''),
                'Detection Date': detection.get('detection_date', ''),
                'Location (Lat/Lng)': location_str,
                'Email': detection.get('email', ''),
                'Source': detection.get('source', '')
            })
        
        # Write separator and summary section
        writer.writerow({})
        writer.writerow({'Disease Name': '=== INVENTORY SUMMARY ==='})
        writer.writerow({})
        writer.writerow({
            'Disease Name': 'Disease',
            'Confidence': 'Total Count',
            'Severity': 'Avg Confidence',
            'Field ID': 'High Severity',
            'Detection Date': 'Medium Severity',
            'Location (Lat/Lng)': 'Low Severity'
        })
        
        # Write summary by disease (sorted by count)
        for disease in sorted(disease_stats.keys(), key=lambda x: disease_stats[x]['count'], reverse=True):
            stats = disease_stats[disease]
            writer.writerow({
                'Disease Name': disease,
                'Confidence': stats['count'],
                'Severity': stats['avg_confidence'],
                'Field ID': stats['high'],
                'Detection Date': stats['medium'],
                'Location (Lat/Lng)': stats['low']
            })
        
        # Write grand total
        writer.writerow({})
        total_detections = sum(stats['count'] for stats in disease_stats.values())
        total_high = sum(stats['high'] for stats in disease_stats.values())
        total_medium = sum(stats['medium'] for stats in disease_stats.values())
        total_low = sum(stats['low'] for stats in disease_stats.values())
        
        writer.writerow({
            'Disease Name': 'TOTAL',
            'Confidence': total_detections,
            'Severity': round(sum(stats['total_confidence'] for stats in disease_stats.values()) / total_detections, 4) if total_detections > 0 else 0,
            'Field ID': total_high,
            'Detection Date': total_medium,
            'Location (Lat/Lng)': total_low
        })
    
    print(f"✅ CSV generated: {csv_filename}")
    print(f"\n📊 Disease Inventory Summary:")
    print(f"{'Disease':<25} {'Total':<8} {'Avg Conf':<12} {'High':<6} {'Medium':<8} {'Low':<6}")
    print("-" * 75)
    
    for disease in sorted(disease_stats.keys(), key=lambda x: disease_stats[x]['count'], reverse=True):
        stats = disease_stats[disease]
        print(f"{disease:<25} {stats['count']:<8} {stats['avg_confidence']:<12} {stats['high']:<6} {stats['medium']:<8} {stats['low']:<6}")
    
    print("-" * 75)
    print(f"{'TOTAL':<25} {total_detections:<8} {round(sum(stats['total_confidence'] for stats in disease_stats.values()) / total_detections, 4) if total_detections > 0 else 0:<12} {total_high:<6} {total_medium:<8} {total_low:<6}")
    print(f"\n✨ Report generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    generate_csv()
