import rasterio
import numpy as np

base = r'D:\SIH_project\TERRAIN-X-TEST-DATA\04_synthetic_complete'

with rasterio.open(base + '/05_reference_outputs/relative_depth_reference.tif') as ds:
    rd = ds.read(1)
with rasterio.open(base + '/03_reference_dem/terrainx_reference_dem.tif') as ds:
    dem = ds.read(1)
with rasterio.open(base + '/05_reference_outputs/terrainx_reference_dsm.tif') as ds:
    dsm = ds.read(1)

valid = rd > 0
print(f'Valid pixels: {valid.sum()} / {rd.size}')

rd_v = rd[valid]
dem_v = dem[valid]
dsm_v = dsm[valid]

from numpy import corrcoef

def rankdata(a):
    arr = np.asarray(a, dtype=float)
    temp = arr.argsort()
    ranks = np.empty_like(temp, dtype=float)
    ranks[temp] = np.arange(len(arr), dtype=float)
    return ranks

r_pearson = corrcoef(rd_v, dem_v)[0, 1]
r_spearman = corrcoef(rankdata(rd_v), rankdata(dem_v))[0, 1]
print(f'Pearson r (rd vs DEM): {r_pearson:.6f}')
print(f'Spearman rho (rd vs DEM): {r_spearman:.6f}')

# Fit affine: DEM = a * rd + b
A = np.vstack([rd_v, np.ones_like(rd_v)]).T
a, b = np.linalg.lstsq(A, dem_v, rcond=None)[0]
print(f'Affine fit: DEM = {a:.6f} * rd + {b:.6f}')
print(f'  Scale a: {a:.6f}')
print(f'  Offset b: {b:.6f}')
sign = 'positive' if a > 0 else 'negative'
print(f'  Sign of a: {sign}')

# Try inverse depth: DEM = a * (1/rd) + b
rd_inv = 1.0 / rd_v
A_inv = np.vstack([rd_inv, np.ones_like(rd_inv)]).T
a_inv, b_inv = np.linalg.lstsq(A_inv, dem_v, rcond=None)[0]
print(f'Inverse-depth fit: DEM = {a_inv:.6f} * (1/rd) + {b_inv:.6f}')
sign_inv = 'positive' if a_inv > 0 else 'negative'
print(f'  Sign of a: {sign_inv}')

# Try negative: DEM = a * (-rd) + b
rd_neg = -rd_v
A_neg = np.vstack([rd_neg, np.ones_like(rd_neg)]).T
a_neg, b_neg = np.linalg.lstsq(A_neg, dem_v, rcond=None)[0]
print(f'Negative-depth fit: DEM = {a_neg:.6f} * (-rd) + {b_neg:.6f}')
sign_neg = 'positive' if a_neg > 0 else 'negative'
print(f'  Sign of a: {sign_neg}')

# R-squared for each
ss_tot = np.sum((dem_v - dem_v.mean())**2)
ss_res = np.sum((dem_v - (a * rd_v + b))**2)
r2 = 1 - ss_res / ss_tot
print(f'R-squared (affine): {r2:.6f}')

ss_res_inv = np.sum((dem_v - (a_inv * rd_inv + b_inv))**2)
r2_inv = 1 - ss_res_inv / ss_tot
print(f'R-squared (inverse): {r2_inv:.6f}')

ss_res_neg = np.sum((dem_v - (a_neg * rd_neg + b_neg))**2)
r2_neg = 1 - ss_res_neg / ss_tot
print(f'R-squared (negative): {r2_neg:.6f}')

# Also check DSM relationship
r_pearson_dsm = corrcoef(rd_v, dsm_v)[0, 1]
print(f'Pearson r (rd vs DSM): {r_pearson_dsm:.6f}')

# Summary
print()
print('=== SUMMARY ===')
print(f'Direct affine: scale={a:.3f} ({"+" if a>0 else "-"}), R2={r2:.4f}')
print(f'Inverse-depth: scale={a_inv:.3f} ({"+" if a_inv>0 else "-"}), R2={r2_inv:.4f}')
print(f'Negative-depth: scale={a_neg:.3f} ({"+" if a_neg>0 else "-"}), R2={r2_neg:.4f}')
print()
print('An inverse-depth transformation (1/rd) would make the scale positive:', 'YES' if a_inv > 0 else 'NO')
print('A negative-depth transformation (-rd) would make the scale positive:', 'YES' if a_neg > 0 else 'NO')