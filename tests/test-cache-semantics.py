"""
Test script to validate cache semantics: whether projection/clipping should be
part of cache keys or applied post-cache.

This test demonstrates the CURRENT behavior (buggy) where each projection/clip
combination creates a separate cache entry. After refactoring, all combinations
should share a single cache entry for the same raw bbox data.
"""

import os
import sys
import json
import hashlib
import time
import shutil
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
import numpy as np


BASE_URL = "http://127.0.0.1:9000"
CACHE_DIR = Path(__file__).parent.parent / "cache"

# Amazon region (same throughout tests)
TEST_BBOX = {
    "north": 15,
    "south": -19,
    "east": -45,
    "west": -85,
    "dim": 600,
}

# Test combinations: same bbox, different projection + clip
TEST_COMBOS = [
    {"name": "combo_1", "projection": "cosine", "clip_valid_region": "true"},
    {"name": "combo_2", "projection": "cosine", "clip_valid_region": "false"},
    {"name": "combo_3", "projection": "sinusoidal", "clip_valid_region": "true"},
    {"name": "combo_4", "projection": "none", "clip_valid_region": "true"},
]


def get_cache_files():
    """Get list of cache JSON files."""
    if not CACHE_DIR.exists():
        return []
    return sorted([f for f in CACHE_DIR.glob("*.json")])


def clear_cache():
    """Clear the cache directory."""
    if CACHE_DIR.exists():
        print(f"Clearing cache directory: {CACHE_DIR}")
        try:
            shutil.rmtree(CACHE_DIR)
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            print("✓ Cache cleared")
        except PermissionError:
            print("⚠ Cache directory in use (server has lock), attempting to clear individual files...")
            # Remove files individually, skip locked directories
            try:
                for item in CACHE_DIR.rglob("*"):
                    if item.is_file():
                        item.unlink()
                print("✓ Cache files cleared (directories preserved)")
            except Exception as e:
                print(f"⚠ Could not clear all cache files: {e}")
                print("  Running test anyway (results may be cached from previous runs)")


def show_cache_state(label: str):
    """Print current cache files."""
    files = get_cache_files()
    print(f"\n{label}")
    print(f"  Total cache files: {len(files)}")
    for f in files:
        size_kb = f.stat().st_size / 1024
        print(f"    - {f.name} ({size_kb:.1f} KB)")
    return files


def fetch_dem(combo: dict) -> dict:
    """Fetch DEM for given projection/clip combo. Returns response dict."""
    params = {
        **TEST_BBOX,
        **{k: v for k, v in combo.items() if k != "name"},
    }
    print(f"\n  Fetching: projection={params['projection']}, clip_valid_region={params['clip_valid_region']}")
    
    start = time.time()
    resp = requests.get(f"{BASE_URL}/api/terrain/dem", params=params)
    elapsed = time.time() - start
    
    if resp.status_code != 200:
        print(f"    ✗ HTTP {resp.status_code}")
        return None
    
    data = resp.json()
    dims = data.get("dimensions")
    min_elev = data.get("min_elevation")
    max_elev = data.get("max_elevation")
    
    print(f"    ✓ OK ({elapsed:.2f}s) dims={dims} elev=[{min_elev}, {max_elev}]")
    return {
        "combo": combo,
        "response": data,
        "elapsed": elapsed,
        "dimensions": dims,
        "min_elev": min_elev,
        "max_elev": max_elev,
    }


def main():
    print("=" * 80)
    print("CACHE SEMANTICS TEST")
    print("=" * 80)
    
    # Verify server is running
    print("\n[1] Checking server connectivity...")
    try:
        resp = requests.get(f"{BASE_URL}/api/terrain/dem", 
                           params={**TEST_BBOX, "projection": "none"}, 
                           timeout=5)
        if resp.status_code == 200:
            print("✓ Server is running")
        else:
            print(f"✗ Server returned {resp.status_code}")
            return
    except Exception as e:
        print(f"✗ Cannot connect to {BASE_URL}: {e}")
        print("  Make sure the server is running on port 9000")
        return
    
    # Clear cache
    print("\n[2] Clearing cache...")
    clear_cache()
    show_cache_state("Initial cache state:")
    
    # Fetch with different projection/clip combos
    print("\n[3] Fetching DEM with different projection/clip combinations...")
    results = []
    for combo in TEST_COMBOS:
        result = fetch_dem(combo)
        if result:
            results.append(result)
    
    # Show cache state after fetches
    cache_after = show_cache_state("\nCache state after fetches:")
    
    # Analyze results
    print("\n[4] Analysis:")
    print(f"  Requests made: {len(results)}")
    print(f"  Cache files created: {len(cache_after)}")
    
    print("\n  Dimensions by combo:")
    for r in results:
        print(f"    {r['combo']['name']:10s}: {r['dimensions']}")
    
    # Check for dimension differences
    dims_set = set(str(r['dimensions']) for r in results)
    if len(dims_set) > 1:
        print(f"\n  ✓ Dimensions vary ({len(dims_set)} unique) - projection/clip is being applied")
    else:
        print(f"\n  ✗ All dimensions identical - projection/clip NOT being applied!")
    
    # Key insight
    print("\n[5] Key Observations:")
    
    if len(cache_after) > 1:
        print(f"  ⚠ BUGGY: Created {len(cache_after)} cache files for same bbox")
        print("    Expected: 1 cache file (all requests should share raw data)")
        print("    Current behavior: Each projection/clip combo creates separate cache entry")
        print("\n    This is inefficient and multiplies cache size unnecessarily.")
    else:
        print(f"  ✓ CORRECT: All {len(results)} requests used 1 cache file")
        print("    Projection/clipping applied post-cache (correct architecture)")
    
    print("\n  Request latencies:")
    for r in results:
        print(f"    {r['combo']['name']:10s}: {r['elapsed']*1000:6.1f} ms")
    
    first_latency = results[0]['elapsed'] if results else 0
    others_latency = [r['elapsed'] for r in results[1:]]
    if others_latency:
        speedup = first_latency / np.mean(others_latency) if np.mean(others_latency) > 0 else 1
        print(f"    (Cache hit speedup vs first request: {speedup:.1f}x)")
    
    # Summary
    print("\n" + "=" * 80)
    if len(cache_after) > 1:
        print("RESULT: Architecture NEEDS REFACTORING")
        print("  - Remove projection + clip_valid_region from cache keys")
        print("  - Cache only raw (unprojected) data")
        print("  - Apply projection/clipping on every request (post-cache)")
    else:
        print("RESULT: Architecture is CORRECT")
        print("  - Caching raw data without projection/clipping in key")
        print("  - All requests share same cache entry")
        print("  - Projection/clipping applied consistently post-cache")
    print("=" * 80)


if __name__ == "__main__":
    main()
