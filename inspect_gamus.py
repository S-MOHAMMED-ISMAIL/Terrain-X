import glob
import h5py
import numpy as np

ROOT = r"D:\SIH_project\TERRAIN-X-TEST-DATA\03_gamus_validation"

files = sorted(glob.glob(ROOT + r"\**\*.h5", recursive=True))

print("=" * 70)
print("GAMUS SAMPLE INSPECTION")
print("=" * 70)

for path in files:
    print("\nFILE:", path)

    with h5py.File(path, "r") as f:
        print("HDF5 keys:", list(f.keys()))

        ds = f["image"]

        print("Shape:", ds.shape)
        print("Dtype:", ds.dtype)

        print("\nDataset attributes:")
        if ds.attrs:
            for key, value in ds.attrs.items():
                print(f"  {key}: {value}")
        else:
            print("  <none>")

        data = ds[:]

        print("\nStatistics:")
        print("  min:", np.nanmin(data))
        print("  max:", np.nanmax(data))
        print("  mean:", np.nanmean(data))
        print("  std:", np.nanstd(data))
        print("  finite:", np.isfinite(data).mean())

        if data.ndim == 2:
            unique = np.unique(data)

            if len(unique) <= 30:
                print("  unique values:", unique)
            else:
                print("  unique values:", len(unique))

        print("-" * 70)