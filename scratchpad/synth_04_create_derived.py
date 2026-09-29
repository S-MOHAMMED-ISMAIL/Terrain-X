import shutil
import os
import rasterio
import numpy as np
import csv

base = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete'
derived = os.path.join(base, 'derived_metric_validation')

# 1. Copy original files (never overwrite)
files_to_copy = [
    '02_georeferenced_rgb/mountain_georeferenced_rgb.tif',
    '03_reference_dem/terrainx_reference_dem.tif',
    '05_reference_outputs/terrainx_reference_dsm.tif',
    '05_reference_outputs/validation_checkpoints.csv',
    '06_segmentation_reference/semantic_reference_classes.tif',
    '06_segmentation_reference/sky_terrain_valid_mask.tif',
    '08_metadata/dataset_metadata.json',
    '08_metadata/manifest.json',
    '08_metadata/buildings_reference.geojson',
]

for f in files_to_copy:
    src = os.path.join(base, f)
    dst = os.path.join(derived, f)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    print(f'Copied: {f}')

# 2. Create derived relative depth with inverse-depth transformation
# Transformation: derived_rd = 1.0 / original_rd
# This is the ONLY transformation used, as established by the follow-up investigation.
src_rd = os.path.join(base, '05_reference_outputs/relative_depth_reference.tif')
dst_rd = os.path.join(derived, '05_reference_outputs/relative_depth_reference.tif')

with rasterio.open(src_rd) as src:
    rd = src.read(1)
    profile = src.profile.copy()
    
    # Apply inverse-depth transformation: 1/rd
    # Handle zeros (NoData) carefully
    valid_mask = rd > 0
    derived_rd = np.zeros_like(rd)
    derived_rd[valid_mask] = 1.0 / rd[valid_mask]
    
    # Update profile
    profile.update(dtype='float32', nodata=0.0)
    
    with rasterio.open(dst_rd, 'w', **profile) as dst:
        dst.write(derived_rd.astype(np.float32), 1)
        # Copy tags
        dst.update_tags(**src.tags())

print(f'Created derived relative depth: {dst_rd}')
print(f'  Original range: [{rd[valid_mask].min():.6f}, {rd[valid_mask].max():.6f}]')
print(f'  Derived range: [{derived_rd[valid_mask].min():.6f}, {derived_rd[valid_mask].max():.6f}]')
print(f'  Transformation: derived_rd = 1.0 / original_rd')
print(f'  Purpose: Validation-only. Converts inverse-depth convention to direct-depth convention.')

# 3. Create derived GCP CSV with renamed columns
src_gcp = os.path.join(base, '04_gcp/gcp.csv')
dst_gcp = os.path.join(derived, '04_gcp/gcp.csv')

with open(src_gcp, 'r') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

# Rename columns: easting_m -> x, northing_m -> y, elevation_m -> z
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
print(f'  Values preserved: YES (only column names changed)')

# 4. Create README for derived fixture
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