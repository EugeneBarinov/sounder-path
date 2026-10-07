"""
Bathymetric Dataset Acquisition Script
======================================
Automates validation and provides instructions for acquiring high-resolution
regional bathymetry datasets (EMODnet Bathymetry DTM / GEBCO).
"""

from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
TARGET_TIF = DATA_RAW_DIR / "E8_2024.tif"

EMODNET_PORTAL_URL = "https://emodnet.ec.europa.eu/en/bathymetry"
GEBCO_PORTAL_URL = "https://www.gebco.net/data_and_products/gridded_bathymetry_data/"


def check_dataset_status() -> bool:
    DATA_RAW_DIR.mkdir(parents=True, exist_ok=True)
    if TARGET_TIF.exists():
        size_mb = TARGET_TIF.stat().st_size / (1024 * 1024)
        print(f"[FOUND] Operational dataset present: {TARGET_TIF.name} ({size_mb:.2f} MB)")
        return True

    print(f"[MISSING] Primary GeoTIFF dataset not found at: {TARGET_TIF}")
    print("\nHow to acquire regional bathymetry:")
    print("1. Visit the EMODnet Bathymetry Viewing and Download Portal:")
    print(f"   {EMODNET_PORTAL_URL}")
    print("2. Download Tile E8 (Black Sea / Crimean Fairways, 2024 DTM).")
    print(f"3. Place the uncompressed GeoTIFF file as:\n   {TARGET_TIF}\n")
    print("Note: The SeaPath navigation engine includes an integrated synthetic bathymetry")
    print("generator that automatically activates in demonstration mode if this file is absent.")
    return False


if __name__ == "__main__":
    has_data = check_dataset_status()
    sys.exit(0 if has_data else 1)
