import numpy as np
import matplotlib.pyplot as plt

from shapely.ops import unary_union
from shapely.geometry import Polygon, MultiPolygon

import pandas as pd
import osmnx as ox

from . import create
from numpy2stl import numpy2stl as n2s


def get_road_model(gdf_roads, scale):

  TOLERANCE_M = 0.1            # Simplification tolerance in meters
  ROAD_WIDTHS = {
      'motorway': 12, 'trunk': 10, 'primary': 8, 
      'secondary': 7, 'tertiary': 6, 'residential': 4, 'service': 2
  }  

  gdf_roads2 = get_road_segments(gdf_roads, ROAD_WIDTHS, TOLERANCE_M)
  vx, fs = render_vertices(gdf_roads2)

  vx[:,[1,0]] = vx[:,[0,1]]*1000
  vx[:,2] = vx[:,2]*scale

  return vx, fs

def get_road_segments(gdf_roads, ROAD_WIDTHS, TOLERANCE_M):

  def get_width(highway_attr):
        if isinstance(highway_attr, list):
            highway_attr = highway_attr[0] # Take the first classification
        return ROAD_WIDTHS.get(highway_attr, 3)	

  gdf_roads = gdf_roads.to_crs(epsg=3857)

  # Apply the buffer using the helper function
  gdf_roads['geometry'] = gdf_roads.apply(
      lambda row: row.geometry.buffer( get_width(row.highway),  join_style=2, cap_style=2 ), axis=1)
    
  gdf_roads = gdf_roads.to_crs(epsg=4326)
  # --- 4. PROCESSING ROADS (Line to Polygon) ---
  gdf_roads_exploded = gdf_roads.explode(index_parts=False)

  return gdf_roads_exploded 
    

def geom_to_points(geom):
    """Converts a Polygon or MultiPolygon into the [[x],[y]] format."""
    output = []
    if isinstance(geom, Polygon):
        x, y = geom.exterior.coords.xy
        output.append([list(x), list(y)])
    elif isinstance(geom, MultiPolygon):
        for part in geom.geoms:
            x, y = part.exterior.coords.xy
            output.append([list(x), list(y)])
    return output


def get_z_values(polygon_list, im, bounds_NW):

    im_lims = np.array([(0,im.shape[0]),(0,im.shape[1])])

    z_list = []
    for poly in polygon_list:
        x,y = poly.exterior.coords.xy

        x,y = x[:-1],y[:-1]
        xy_list = np.array([x,y]).T

        ###########################
        Nc, Wc = create.coor2im(bounds_NW , im_lims, xy_list)
        Nc = Nc.clip(0,im.shape[0]-1)
        Wc = Wc.clip(0,im.shape[1]-1)
        
        ############################
        z = im[Nc, Wc] 
        z_list.append(z)

    return  z_list


def polygon_to_vertices(polygon_list):

    triangles = []
    for poly in polygon_list:
        x,y,z = poly

        pts = np.array([x,y,z]).T
        
        tris = n2s.polygon_to_complex(pts, perimeters=None, z_margin=.5)	
        triangles.append(tris)

    triangles = np.concatenate(triangles)
    vertices, faces = n2s.vertices_to_index(triangles)

    return vertices, faces

def render_vertices(gdf):
    
    triangles = []
    
    for n,row in enumerate(gdf.itertuples(index=False)):

        x,y = row.geometry.exterior.coords.xy

        try:
          x,y = x[:-1],y[:-1]
          z1 = row.z
          pts = np.array([x,y,z1]).T
          
          tris = n2s.polygon_to_complex(pts, perimeters=None, z_margin=.5)	
          triangles.append(tris)
        except:
            pass

    triangles = np.concatenate(triangles)
    vertices, faces = n2s.vertices_to_index(triangles)


    return vertices, faces
