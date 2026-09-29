import os, csv

base = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete'
derived = os.path.join(base, 'derived_metric_validation')

# Create derived GCP CSV with renamed columns
src_gcp = os.path.join(base, '04_gcp/gcp.csv')
dst_gcp = os.path.join(derived, '04_gcp/gcp.csv')

with open(src_gcp, 'r') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

rename_map = {'easting_m': 'x', 'northing_m': 'y', 'elevation_m': 'z'}
new_fieldnames = [rename_map.get(fn, fn) for fn in fieldnames]

with open(dst_gcp, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=new_fieldnames)
    writer.writeheader()
    for row in rows:
        new_row = {rename_map.get(k, k): v for k, v in row.items()}
        writer.writerow(new_row)

print(f'Created derived GCP CSV: {dst_gcp}')
print(f'  Original columns: {fieldnames}')
print(f'  Derived columns: {new_fieldnames}')
print(f'  Row count: {len(rows)}')

# Create README
readme_path = os.path.join(derived, 'README.md')
with open(readme_path, 'w') as f:
    f.write("""# Derived Metric Validation Fixture

## Purpose
This is a VALIDATION-ONLY derived fixture. It exists solely to exercise the
existing TERRAIN-X metric calibration pipeline end-to-end.

## What Was Changed
1. **relative_depth_reference.tif**: Values transformed via inverse-depth (1/rd).
   - Original: inverse-depth convention (larger = farther = lower elevation)
   - Derived: direct-depth convention (larger = closer = higher elevation)
   - This is the ONLY transformation applied.

2. **gcp.csv**: Column names renamed (easting_m->x, northing_m->y, elevation_m->z).
   - Values are identical to the original.
   - Only column names changed to match the pipeline's expected schema.

## What Was NOT Changed
- No production code modified
- No calibration thresholds or gates modified
- No algorithms changed
- No original dataset files overwritten

## Important
This derived fixture is NOT evidence that the original synthetic depth reference
is correct. It is a validation-only tool to exercise the production pipeline's
metric calibration path.
""")

print(f'Created README: {readme_path}')
print()
print('=== DERIVED FIXTURE CREATION COMPLETE ===')