# backend/app/satellite/processors/vegetation_indices.py

import math

def compute_ndvi(red: float, nir: float) -> float:
    """Compute the Normalized Difference Vegetation Index (NDVI).
    
    Formula: (NIR - Red) / (NIR + Red)
    """
    if red is None or nir is None:
        return float('nan')
    if math.isnan(red) or math.isnan(nir):
        return float('nan')
    if red < 0.0 or nir < 0.0:
        return float('nan')
        
    denom = nir + red
    if abs(denom) < 1e-9:
        return float('nan')
        
    return (nir - red) / denom

def compute_osavi(red: float, nir: float, L: float = 0.16) -> float:
    """Compute the Optimized Soil Adjusted Vegetation Index (OSAVI).
    
    Formula: (1.16 * (NIR - Red)) / (NIR + Red + 0.16)
    """
    if red is None or nir is None:
        return float('nan')
    if math.isnan(red) or math.isnan(nir):
        return float('nan')
    if red < 0.0 or nir < 0.0:
        return float('nan')
        
    denom = nir + red + L
    if abs(denom) < 1e-9:
        return float('nan')
        
    return ((1.0 + L) * (nir - red)) / denom

def compute_seli(red_edge: float, nir: float) -> float:
    """Compute the Sentinel-2 Red-Edge Leaf Area Index (SeLI) index.
    
    Formula: (NIR - Red_Edge) / (NIR + Red_Edge)
    Where NIR is Band 8A (865nm) and Red_Edge is Band 5 (705nm).
    """
    if red_edge is None or nir is None:
        return float('nan')
    if math.isnan(red_edge) or math.isnan(nir):
        return float('nan')
    if red_edge < 0.0 or nir < 0.0:
        return float('nan')
        
    denom = nir + red_edge
    if abs(denom) < 1e-9:
        return float('nan')
        
    return (nir - red_edge) / denom
