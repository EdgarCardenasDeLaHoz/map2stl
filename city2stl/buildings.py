
import numpy as np 
import osmnx as ox
import pandas as pd

from shapely.geometry import Polygon,MultiPolygon
from descartes import PolygonPatch

import matplotlib.pyplot as plt 
from matplotlib.colors import ListedColormap
from matplotlib.collections import PatchCollection
import matplotlib.cm as cm

####################################
def building_to_gdf(GEO_poly):
    
    gdf = ox.footprints_from_polygon(GEO_poly)

    columns = ["area","geometry",
               "building:height",
               "building:color",
               "building:levels"]

    columns = [col for col in columns if col in gdf.columns]
    gdf = gdf[columns]

    if "building:levels" not in gdf.columns:
        gdf["building:levels"] = 1
    else: 
        gdf["building:levels"] = pd.to_numeric(gdf["building:levels"], errors='coerce')
    
    if "building:height" not in gdf.columns:
        gdf["building:height"] = gdf["building:levels"]*3.9
    
    return gdf

def building_to_polygons(gdf):
    
    gdf = gdf.sort_values(by=["building:height","building:levels"])

    H = np.array(gdf[["building:height","building:levels"]])
    H = H.astype(np.float)
    build_polys = []

    for n,row in enumerate(gdf.itertuples(index=False)):
        
        if np.isnan(H[n,0]) and not np.isnan(H[n,1]):
            H[n,0] = H[n,1] * 3.9

        base_H, roof_H  = 0, np.nan_to_num(H[n,0])

        geometry = row.geometry
        if isinstance(geometry, Polygon):
            
            pts = np.array(geometry.exterior.xy)
            poly = {"points":pts, 
                    "base_height": base_H, 
                    "roof_height": roof_H}

            build_polys.append(poly)

        elif isinstance(geometry, MultiPolygon):
            for subpolygon in geometry: #if geometry is multipolygon, go through each constituent subpolygon
                
                pts = np.array(subpolygon.exterior.xy)
                poly = {"points":pts, 
                        "base_height": base_H, 
                        "roof_height": roof_H}

                build_polys.append(poly)

    return build_polys

#######################################
def get_polygons(gdf):
    
    polygons = []
    for n,row in enumerate(gdf.itertuples(index=False)):
        geometry = row.geometry

        z1 = row.z1
        z0 = row.z0
        try:
            if isinstance(geometry, Polygon):
                pts = np.array(geometry.exterior.xy)
                poly = {"points":pts, "z0":z0, "z1":z1}
                polygons.append(poly)
            elif isinstance(geometry, MultiPolygon):
                #if geometry is multipolygon, go through each subpolygon 
                for subpolygon in geometry.geoms: 
                    pts = np.array(subpolygon.exterior.xy)
                    poly = {"points":pts, "z0":z0, "z1":z1}
                    polygons.append(poly)
        except:
            print("!", end="")
            continue

    #polygons = [Polygon(poly["points"].T) for poly in polygons]

    return polygons

#####################################
def draw_building_patches(gdf):

    H = building_heights(gdf)
    patches = building_to_patches(gdf)
    draw_patches(patches,H)

def building_heights(gdf):
    Heights = []
    for _,row in gdf.iterrows():

        b_h,b_l = row[["height","building:levels"]]

        H = 10

        if not pd.isnull(b_h):
            H = b_h
        elif not pd.isnull(b_l):    
            H = float(b_l) * 3  

        geometry = row.geometry
        if isinstance(geometry, Polygon):
            Heights.append(float(H))
        elif isinstance(geometry, MultiPolygon):
            Heights.append(H)
        else:
            Heights.append(H)
            #for subpolygon in geometry.geoms: 
                

    Heights = np.array(Heights)

    return Heights

def building_to_patches(gdf):
    
    patches = [] 
    for _,row in gdf.iterrows():
        geometry = row["geometry"]
        if isinstance(geometry, Polygon):
            patches.append(PolygonPatch(geometry))
        elif isinstance(geometry, MultiPolygon):
            #if geometry is multipolygon, go through each constituent subpolygon
            for subpolygon in geometry:
                patches.append(PolygonPatch(subpolygon))
            
    return patches

#####################################

def draw_patches(patches,H=None):

    newcolors = cm.get_cmap('jet', 256)(np.linspace(0, 1, 256))
    newcolors = np.concatenate(([[.6,.6,.6,1]], newcolors), axis=0)
    newcmp = ListedColormap(newcolors)   

    p = PatchCollection(patches, linewidth=0.5, edgecolor = 'w', cmap=newcmp)

    if H is not None:
        p.set_array(H)

    fig, ax = plt.subplots(figsize=[14,10], facecolor='k')
    ax.set_facecolor('k')

    plt.setp(ax.spines.values(), color="w")
    ax.add_collection(p)
    
    ax.grid(True,color="#333333") 
    ax.margins(0)
    ax.tick_params(which="both", direction="in",colors="#333333")
    ax.set_aspect("equal")

    #plt.colorbar(p)
    fig.canvas.draw()

#####################################
