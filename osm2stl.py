
import numpy as np
from shapely.geometry import Polygon,MultiPolygon
from descartes import PolygonPatch
import osmnx as ox
import numpy2stl as npstl
## Functions using OSMNX

class Line:
    def __init__(self, name, pts, color='w', type=None):
        self.name = name
        self.pts = pts
        self.type = type
        self.color = color

def get_pistes_osmnx(bbox):
  
    infra = '["piste:type"="downhill"]'       
    G = ox.graph_from_bbox(*bbox, custom_filter = infra)
    
    clrs = ["g","g","b","r","k","w"]
    trail_types = ["novice","easy","intermediate","advanced","expert"]

    pistes = []
    for u, v, data in G.edges(keys=False, data=True):

        name, diff = "",""
        if "name" in data:  
            name = data["name"]             

        if "geometry" in data: 
            pts = np.array(data["geometry"].xy)
        else:
            node = G.nodes
            pts = [[ node[u]['x'], node[v]['x']],
                   [ node[u]['y'], node[v]['y']]]
            pts = np.array(pts)

        if "piste:difficulty" in data:
            diff = data["piste:difficulty"] 
           
        idx = trail_types.index(diff) if diff in trail_types else -1

        line = Line(name, pts, color=clrs[idx], type=diff)
        pistes.append(line)

    return pistes

def get_boundries_osmnx(loc_name):
        
    poly = ox.geocode_to_gdf(loc_name)
    boundry = np.array(poly.geometry.exterior[0]).T
    bbox = np.array(poly[["bbox_north","bbox_south","bbox_east","bbox_west"]])[0]
    boundry_line = Line(loc_name, boundry, color="r", type="boundry")
    return boundry_line, bbox
    
def get_roads_osmnx(loc_name):
        
    G = ox.graph_from_place(loc_name, custom_filter = '["highway"="primary"]')
    D = [data for data in G.edges(keys=False, data=True)]
    pts_geo = [np.array(X1[2]["geometry"].xy) for X1 in D if "geometry" in X1[2]]
        
    return pts_geo

def get_rivers(bbox, incl_streams=True):

    infra = '["waterway"="river"]'
    if incl_streams:
        infra = infra + '["waterway"="stream"]'     
    G = ox.graph_from_bbox(*bbox, custom_filter = infra, 
    simplify=True,  retain_all=True,  clean_periphery=False)

    rivers = []
    for u, v, data in G.edges(keys=False, data=True):
        
        name = ""
        if "name" in data:  
            name = data["name"]         

        if "geometry" in data: 
            pts = np.array(data["geometry"].xy)
        else:
            node = G.nodes
            pts = [[ node[u]['x'], node[v]['x']],
                   [ node[u]['y'], node[v]['y']]]
            pts = np.array(pts)

        line = Line(name, pts, color="g", type="river")
        rivers.append(line)

    return rivers, G

