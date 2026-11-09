import math
import os
from osgeo import gdal

from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtGui import QIcon

from qgis.core import (
    QgsProject,
    QgsMapLayerType,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsGeometry,
    QgsFeature,
    QgsVectorLayer,
    QgsField,
)
from qgis.gui import QgsMapToolEmitPoint


class WaterDropMapTool(QgsMapToolEmitPoint):
    """Trace a steepest-descent path from a clicked DEM cell."""

    NEIGHBOURS = (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    )

    def __init__(self, plugin, dem_layer):
        super().__init__(plugin.iface.mapCanvas())
        self.plugin = plugin
        self.iface = plugin.iface
        self.canvas = self.iface.mapCanvas()
        self.dem = dem_layer

        # Raster layer source may include provider options after '|'.
        dem_path = self.dem.source().split("|")[0]
        self.ds = gdal.Open(dem_path, gdal.GA_ReadOnly)
        if self.ds is None:
            raise RuntimeError(
                "GDAL could not open the selected DEM. "
                "Check that it is a file-based raster."
            )

        self.band = self.ds.GetRasterBand(1)
        self.gt = self.ds.GetGeoTransform()
        self.nodata = self.band.GetNoDataValue()
        self.arr = self.band.ReadAsArray()

        if self.arr is None:
            raise RuntimeError("Could not read the DEM raster band.")

        self.inv_gt = gdal.InvGeoTransform(self.gt)
        # GDAL Python bindings differ: some return a transform,
        # while others return (success, transform).
        if (
            isinstance(self.inv_gt, (tuple, list))
            and len(self.inv_gt) == 2
            and isinstance(self.inv_gt[0], (bool, int))
        ):
            success, transform = self.inv_gt
            if not success:
                raise RuntimeError("Could not invert the DEM geotransform.")
            self.inv_gt = transform

        if self.inv_gt is None:
            raise RuntimeError("Could not invert the DEM geotransform.")

        self.transform = QgsCoordinateTransform(
            self.canvas.mapSettings().destinationCrs(),
            self.dem.crs(),
            QgsProject.instance(),
        )

        self.canvasClicked.connect(self.on_click)
        self.setCursor(Qt.CrossCursor)

    def valid_cell(self, row, col):
        rows, cols = self.arr.shape
        if row < 0 or row >= rows or col < 0 or col >= cols:
            return False

        z = float(self.arr[row, col])
        if not math.isfinite(z):
            return False

        if self.nodata is not None and math.isclose(
            z, float(self.nodata), rel_tol=0.0, abs_tol=1e-12
        ):
            return False

        return True

    def cell_center(self, row, col):
        x, y = gdal.ApplyGeoTransform(
            self.gt, col + 0.5, row + 0.5
        )
        return QgsPointXY(x, y)

    def trace_path(self, start_row, start_col, max_steps=100000):
        row, col = start_row, start_col

        if not self.valid_cell(row, col):
            raise ValueError("The selected DEM cell is NoData or invalid.")

        path = [self.cell_center(row, col)]
        visited = {(row, col)}

        for _ in range(max_steps):
            z0 = float(self.arr[row, col])
            x0, y0 = gdal.ApplyGeoTransform(
                self.gt, col + 0.5, row + 0.5
            )

            best_cell = None
            best_slope = 0.0

            for dr, dc in self.NEIGHBOURS:
                nr, nc = row + dr, col + dc

                if (nr, nc) in visited or not self.valid_cell(nr, nc):
                    continue

                zn = float(self.arr[nr, nc])
                drop = z0 - zn
                if drop <= 0:
                    continue

                xn, yn = gdal.ApplyGeoTransform(
                    self.gt, nc + 0.5, nr + 0.5
                )
                distance = math.hypot(xn - x0, yn - y0)
                if distance <= 0:
                    continue

                slope = drop / distance
                if slope > best_slope:
                    best_slope = slope
                    best_cell = (nr, nc)

            # Stops at a local minimum, flat, pit, or closed depression.
            if best_cell is None:
                break

            row, col = best_cell
            visited.add((row, col))
            path.append(self.cell_center(row, col))

            rows, cols = self.arr.shape
            if row == 0 or col == 0 or row == rows - 1 or col == cols - 1:
                break

        return path

    def on_click(self, point, button):
        if button != Qt.LeftButton:
            return

        try:
            dem_point = self.transform.transform(point)
            px, py = gdal.ApplyGeoTransform(
                self.inv_gt, dem_point.x(), dem_point.y()
            )
            col = int(math.floor(px))
            row = int(math.floor(py))

            path = self.trace_path(row, col)
            if len(path) < 2:
                raise ValueError(
                    "No downhill path found. The selected cell may be flat, "
                    "a local minimum, or adjacent to NoData."
                )

            line_layer = QgsVectorLayer(
                "LineString?crs=" + self.dem.crs().authid(),
                "Water Drop",
                "memory",
            )
            if not line_layer.isValid():
                raise RuntimeError("Could not create the output line layer.")

            provider = line_layer.dataProvider()
            provider.addAttributes([
                QgsField("start_row", QVariant.Int),
                QgsField("start_col", QVariant.Int),
                QgsField("vertices", QVariant.Int),
                QgsField("length", QVariant.Double),
                QgsField("end_row", QVariant.Int),
                QgsField("end_col", QVariant.Int),
            ])
            line_layer.updateFields()

            feature = QgsFeature(line_layer.fields())
            geometry = QgsGeometry.fromPolylineXY(path)
            feature.setGeometry(geometry)
            end_px, end_py = gdal.ApplyGeoTransform(
                self.inv_gt, path[-1].x(), path[-1].y()
            )
            end_col = int(math.floor(end_px))
            end_row = int(math.floor(end_py))

            feature.setAttributes([
                row, col, len(path), geometry.length(), end_row, end_col
            ])

            result = provider.addFeatures([feature])
            if not result[0]:
                raise RuntimeError("Could not add the traced line feature.")

            line_layer.updateExtents()
            QgsProject.instance().addMapLayer(line_layer)

            self.iface.messageBar().pushSuccess(
                "Water Drop",
                f"Created path with {len(path)} vertices.",
            )

        except Exception as exc:
            QMessageBox.warning(None, "Water Drop", str(exc))


class WaterDropPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.stop_action = None
        self.tool = None

    def initGui(self):
        logo_path = os.path.join(os.path.dirname(__file__), "logos", "water_drop.png")

        self.action = QAction(QIcon(logo_path), "Start Water Drop", self.iface.mainWindow())
        self.action.setToolTip("Activate or deactivate Water Drop")
        self.action.triggered.connect(self.toggle_tool)

        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Water Drop", self.action)
        
    def toggle_tool(self):
        if self.tool is not None:
            self.stop_tool()
            self.action.setText("Start Water Drop")

            self.iface.messageBar().pushInfo(
                "Water Drop",
                "Water Drop tool stopped."
        )
        else:
            self.start_tool()

            if self.tool is not None:
                self.action.setText("Stop Water Drop")

    def start_tool(self):
        dem = self.iface.activeLayer()
        if dem is None or dem.type() != QgsMapLayerType.RasterLayer:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Water Drop",
                "Select a DEM raster layer in the Layers panel first.",
            )
            return

        try:
            self.stop_tool()
            self.tool = WaterDropMapTool(self, dem)
            self.iface.mapCanvas().setMapTool(self.tool)
            self.iface.messageBar().pushInfo(
                "Water Drop", "Click a starting point on the DEM."
            )
        except Exception as exc:
            self.tool = None
            QMessageBox.critical(
                self.iface.mainWindow(), "Water Drop", str(exc)
            )

    def stop_tool(self):
        if self.tool is not None:
            try:
                self.tool.canvasClicked.disconnect(self.tool.on_click)
            except (TypeError, RuntimeError):
                pass

            if self.iface.mapCanvas().mapTool() == self.tool:
                self.iface.mapCanvas().unsetMapTool(self.tool)

            self.tool = None

        self.iface.mapCanvas().setCursor(Qt.ArrowCursor)

    def unload(self):
        self.stop_tool()

        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginMenu("&Water Drop", self.action)
            self.action.deleteLater()
            self.action = None

        if self.stop_action is not None:
            self.iface.removePluginMenu("&Water Drop", self.stop_action)
            self.stop_action.deleteLater()
            self.stop_action = None
