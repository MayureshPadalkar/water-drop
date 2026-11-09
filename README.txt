Water Drop QGIS Plugin
======================

Installation
------------
1. Extract the water_drop_plugin folder.
2. Copy the folder into your QGIS profile's python/plugins directory.
3. Restart QGIS (or use Plugin Reloader if already installed).
4. Open Plugins > Manage and Install Plugins.
5. Enable "Water Drop".

Typical Windows profile path:
%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\

Usage
-----
1. Load a file-based DEM raster into QGIS.
2. For better experience, convert the DEM to Fill Sinks (Wang and Liu).
3. Select the DEM in the Layers panel.
4. Choose Plugins > Water Drop > Start Water Drop.
5. Left-click a starting location on the map.
6. A "Water Drop" line memory layer is added to the project.
7. Choose Plugins > Water Drop > Stop Water Drop to deactivate the tool.

Method and limitations
----------------------
The tool moves to the neighbouring cell with the greatest positive
elevation drop divided by centre-to-centre distance. It checks eight
neighbours and stops at a local minimum/flat, NoData boundary, raster
edge, or maximum step count.

This is a simple steepest-descent tracer, not a hydrologically conditioned
flow-routing algorithm. It does not fill sinks, breach barriers, resolve
flats, or model flow accumulation. The output is a memory layer; save it
to a GeoPackage if it needs to persist after closing the project.

The plugin uses QGIS, GDAL and Python libraries already supplied by QGIS.
