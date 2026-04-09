import napari

def view3D_napari(solid):

	#tri = numpy2stl(im)
	#solid = Solid(tri)
	
	vertices = solid.vertices.copy().astype(float)
	faces = solid.faces
	
	v = napari.current_viewer()
	if v is None: v = napari.Viewer()
	v.layers.clear()

	surface = (vertices,faces)
	s = v.add_surface(surface)
	s.wireframe.visible = True
	s.wireframe.color = '#AAA'
	v.dims.ndisplay = 3
	s.shading = 'smooth' 
	s.opacity = 0.8
	v.axes.visible = True
	v.axes.colored = True