
import numpy as np 
import osmnx as ox

from shapely.geometry import Polygon,MultiPolygon
from descartes import PolygonPatch
import matplotlib.pyplot as plt 

from matplotlib.colors import ListedColormap
from matplotlib.collections import PatchCollection
import matplotlib.cm as cm


def building_polygons(GEO_poly):
    
    gdf = ox.footprints_from_polygon(GEO_poly)

    columns = ["area","geometry","building:height","building:color","building:levels"]
    columns = [col for col in columns if col in gdf.columns]
    gdf = gdf[columns]


    gdf[["building:levels"]] = gdf[["building:levels"]].astype(np.float)
    if "building:height" not in gdf.columns:
        gdf["building:height"] = gdf["building:levels"]*3.9
    
    return gdf

###################################

def get_building_polygons(gdf):
    
    gdf = gdf.sort_values(by=["building:height","building:levels"])
    H = np.array(gdf[["building:height","building:levels"]],dtype = np.float)
    build_polys = []

    for n,row in enumerate(gdf.itertuples(index=False)):
        
        if np.isnan(H[n,0]) and not np.isnan(H[n,1]):
            H[n,0] = H[n,1] * 3.9

        build_H = np.nan_to_num(H[n,0])
        base_H = 0

        geometry = row.geometry
        if isinstance(geometry, Polygon):
            
            pts = np.array(geometry.exterior.xy)
            poly = {"points":pts, "base_height": base_H, 
                    "building_height":build_H}

            build_polys.append(poly)

        elif isinstance(geometry, MultiPolygon):
            for subpolygon in geometry: #if geometry is multipolygon, go through each constituent subpolygon
                
                pts = np.array(subpolygon.exterior.xy)
                poly = {"points":pts, "base_height": base_H, 
                        "building_height":build_H}

                build_polys.append(poly)

    return build_polys


def triangulate_buildings(build_polys):

    triangles = []

    for _,p in enumerate(build_polys ):

        if (p['building_height'] < 1) or (p['area']>0):
            continue

        height = p['building_height']  / 100000
        base = p['base_height'] / 100000

        verts = p['points'].T
        if (np.isclose(verts[0],verts[-1])):  
            verts = verts[0:-1]

        zdim = np.zeros((len(verts),1)) + height + base
        verts = np.concatenate([verts, zdim],axis=1)
        
        tris = np2stl.polygon_to_prism(verts, base_val=base)
        triangles.append(tris)

    triangles = np.concatenate(triangles)   
    return triangles

################################################

def MakePatchCollection(gdf):
    
    gdf = gdf.sort_values(by=["building:height","building:levels"])
    H = np.array(gdf[["building:height","building:levels"]]).astype(np.float)
    patches = []
    idx = 0

    for _,row in gdf.iterrows():

        geometry = row["geometry"]
        if np.isnan(H[idx,0]) and not np.isnan(H[idx,1]):
            H[idx,0] = H[idx,1] * 3.9

        if isinstance(geometry, Polygon):
            patches.append(PolygonPatch(geometry))
        elif isinstance(geometry, MultiPolygon):
            for subpolygon in geometry: #if geometry is multipolygon, go through each constituent subpolygon
                patches.append(PolygonPatch(subpolygon))
        idx+=1
        
    H = np.nan_to_num(H) 
    H_norm = H[:,0] / np.max(H[:,0])
    
    return patches,H_norm

def draw_building_patches(patches,H):

    newcolors = cm.get_cmap('jet', 256)(np.linspace(0, 1, 256))
    newcolors = np.concatenate(([[.6,.6,.6,1]], newcolors), axis=0)
    newcmp = ListedColormap(newcolors)   

    p = PatchCollection(patches, linewidth=0.5, edgecolor = 'w', cmap=newcmp)
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

