
# STRM2STL: 3D Map Generator

A Python toolkit for converting geographic and elevation data (e.g., SRTM, GEBCO, OSM) into 3D STL models suitable for 3D printing. Generate detailed terrain models of cities, oceans, rivers, and more.

## Features

- **City Models**: Extract buildings, roads, and terrain from OpenStreetMap (OSM) data.
- **Ocean Models**: Process bathymetric data from GEBCO for underwater topography with region-specific processing.
- **Terrain Models**: Convert DEM (Digital Elevation Model) data to 3D surfaces.
- **Satellite Imagery**: Integrate satellite images for water detection and terrain texturing.
- **Web UI**: Interactive map interface for selecting bounding boxes.
- **CLI Tools**: Command-line interface for batch processing predefined geographic regions.
- **Modular Design**: Separate modules for different data types (cities, geo, numpy-to-STL, oceans).

## Installation

### Prerequisites
- Python 3.8 or higher
- Virtual environment recommended

### Install from Source
1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/strm2stl.git
   cd strm2stl
   ```

2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   # On Windows:
   .venv\Scripts\activate
   # On macOS/Linux:
   source .venv/bin/activate
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. (Optional) Install as a package:
   ```bash
   pip install -e .
   ```

## Usage

### Via Notebooks
Explore the `notebooks/` folder for Jupyter notebooks demonstrating various features:
- `Cities.ipynb`: Generate city models with buildings and roads.
- `Oceans.ipynb`: Create ocean floor models.
- `Rivers.ipynb`: Model river systems.
- `Buildings.ipynb`: Focus on building extraction.

Run a notebook:
```bash
jupyter notebook notebooks/Cities.ipynb
```

### Via Scripts
Use the modular scripts directly. Example for city model:

```python
from strm2stl.city2stl.create import get_building_model
import numpy as np

# Assuming you have OSM data as GeoDataFrame
# gdf = ox.geometries_from_place('New York City', tags={'building': True})
# vertices, faces = get_building_model(gdf, scale=1.0)
```

### Web UI
Launch the location picker UI:
```bash
python strm2stl/ui/location_picker.py
```
Then open http://127.0.0.1:9000 in your browser to select bounding boxes interactively.

The interface allows you to:

- Draw or edit bounding boxes on a 2D map.
- Toggle to a 3D globe showing markers for every stored region.
- Load a previously saved region from a dropdown list.
- Preview DEM statistics for the current selection.
- Save custom selections by giving them a name.

All saved regions are kept in the `coordinates.json` file located at the package root. The UI reads from and writes to this file, and it is pre-populated with a set of regions extracted from the original `Oceans.ipynb` notebook, including entries such as `Appalachia`, `Vermont`, `North Jersey`, `Grand Canyon`, `Amazon`, and more.

The file has the following structure:

```json
{
  "regions": [
    {
      "name": "Example",
      "north": 50.0,
      "south": 40.0,
      "east": -70.0,
      "west": -80.0,
      "description": "Optional text"
    }
  ]
}
```

You can manually edit this JSON or let the application manage it through the "Save Current Selection" button.

### CLI
Use the command-line interface for batch processing of geographic regions:

```bash
# Process a predefined region
python strm2stl/cli.py ocean appalachians --output-dir ./models --dim 800 --height 30

# Process a custom bounding box
python strm2stl/cli.py ocean custom "MyRegion" 40.0 30.0 -70.0 -80.0 --dim 600

# List available regions
python strm2stl/cli.py ocean --help
```

Available regions:
- `appalachians`: Appalachian Mountains
- `great-lakes`: Great Lakes region
- `caribbean`: Caribbean Sea
- `andes`: Andes Mountains
- `amazon`: Amazon River basin
- `mediterranean`: Mediterranean Sea
- `africa`: African continent
- `japan`: Japan and surrounding waters

Options:
- `--output-dir, -o`: Output directory (default: output)
- `--dim`: Output dimension size (default: 600)
- `--depth-scale`: Depth scaling factor (default: 0.5)
- `--height`: Maximum model height (default: 25)
- `--no-water-subtract`: Disable water body subtraction
- `--format`: Output format - 'stl' or 'npy' (default: stl)

## Configuration

- Edit `config.json` for data paths (e.g., GEBCO tiles).
- Environment variables can override paths (e.g., `OCEAN_ROOT=/path/to/data`).

## Dependencies

Key libraries:
- `numpy`: Array processing
- `pandas`: Data manipulation
- `shapely`: Geometry operations
- `osmnx`: OSM data fetching
- `trimesh`: 3D mesh handling
- `fastapi`: Web UI
- `pymeshlab`: Mesh processing

See `requirements.txt` for full list.

## Project Structure

```
strm2stl/
├── city2stl/          # City/building models
├── geo2stl/           # Geographic data processing
├── numpy2stl/         # NumPy to STL conversion
├── ui/                # Web interface
├── notebooks/         # Jupyter examples
├── config.json        # Configuration
├── requirements.txt   # Dependencies
└── readme.md          # This file
```

## Contributing

1. Fork the repo.
2. Create a feature branch.
3. Add tests for new code.
4. Submit a pull request.

## License

MIT License - see LICENSE file.

## Examples

### Generate a Simple Terrain Model
```python
import numpy as np
from strm2stl.numpy2stl import numpy2stl

# Create a simple hill
data = np.random.rand(100, 100) * 10
facet = numpy2stl(data)
# Export to STL...
```

### Ocean Model from GEBCO
See `notebooks/Oceans.ipynb` for full example.

## Troubleshooting

- **Import Errors**: Ensure all dependencies are installed.
- **Data Paths**: Check `config.json` for correct file paths.
- **Performance**: For large datasets, consider downsampling or parallel processing.

## Roadmap

- [ ] CLI interface
- [ ] More data sources (e.g., USGS, Copernicus)
- [ ] Web API
- [ ] Docker container
- [ ] Unit tests 